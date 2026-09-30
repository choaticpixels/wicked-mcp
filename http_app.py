"""
ASGI entrypoint for running the Wicked MCP server (see server.py) over
streamable HTTP, for remote deployment.

Running it this way — instead of just `python server.py` with stdio, which
is what a locally-run client like Claude Desktop uses — turns it into a
public HTTP endpoint anyone can hit, not something under each user's own
control. The one thing that changes as a result: a per-IP rate limit, so
one caller can't monopolize it or hammer the upstream services on your
behalf. Everything else (tools, upstream calls, auth-optional design) is
identical to running server.py locally.

Run directly:
    uvicorn http_app:app --host 0.0.0.0 --port 8000

Deployed via the Dockerfile in this directory, Railway sets $PORT.
"""
from __future__ import annotations

import ipaddress
import os
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.transport_security import TransportSecuritySettings

from server import mcp

RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))
_WINDOW_SECONDS = 60.0

# Behind Railway's edge proxy the TCP peer is always one of Railway's internal
# addresses (100.64.0.x), never the real caller -- keying the limiter on
# request.client.host made every user share (or randomly split across) a
# handful of buckets. Railway's proxy sets X-Real-IP to the caller's actual
# remote address (it matches the `srcIp` in Railway's own request logs), so
# that is the header we trust by default.
#
# This is only safe because the proxy sets the header itself; if this server
# is ever exposed without such a proxy in front, a client could choose its own
# value -- set TRUSTED_CLIENT_IP_HEADER to empty to fall back to the TCP peer.
# If the domain is ever put behind Cloudflare's proxy, Railway would see
# Cloudflare as the client, so point this at a header that carries the real IP
# (e.g. cf-connecting-ip). The header must hold a single IP address.
_TRUSTED_CLIENT_IP_HEADER = os.environ.get("TRUSTED_CLIENT_IP_HEADER", "x-real-ip").strip().lower()
_PRUNE_EVERY_SECONDS = 30.0
_PRUNE_ABOVE_ENTRIES = 1000


def _normalize_ip(raw: str) -> str | None:
    """Canonical rate-limit key for an IP string, or None if it isn't one.
    IPv4-mapped IPv6 collapses to the IPv4 address, and IPv6 is grouped by /64
    (the smallest block a single host/subscriber normally controls) so one
    caller can't dodge the limit by rotating addresses within their prefix."""
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(ip)


def client_key(request: Request) -> str:
    """Who is calling, for rate limiting. Prefers the trusted proxy header;
    anything missing or not a valid IP falls back to the TCP peer, so junk
    header values can never mint fresh buckets."""
    if _TRUSTED_CLIENT_IP_HEADER:
        header_value = request.headers.get(_TRUSTED_CLIENT_IP_HEADER)
        if header_value:
            key = _normalize_ip(header_value)
            if key is not None:
                return key
    peer = request.client.host if request.client else "unknown"
    return _normalize_ip(peer) or peer

# The SDK's streamable_http_app() turns on Host/Origin-header DNS-rebinding
# protection with an empty allowlist by default, which rejects every real
# request (421 Invalid Host header) once this is reachable at a real
# hostname instead of localhost. ALLOWED_HOSTS is comma-separated so a
# custom domain can be added later without a code change.
_ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    "ALLOWED_HOSTS", "mcp.wickedapi.com,wicked-mcp-production.up.railway.app,localhost:8080,127.0.0.1:8080"
).split(",") if h.strip()]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory fixed-window limiter, keyed by client IP.

    Good enough for a single instance, which is what this deploys as. If
    this ever needs to scale to multiple instances, move the counters to
    something shared (Redis) instead — an in-memory dict per-instance would
    silently under-count and let the effective limit multiply by instance
    count.
    """

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = 0.0

    def _prune(self, now: float) -> None:
        """Drop buckets with no hits left in the window. Without this, every
        distinct caller ever seen would stay in memory forever."""
        if len(self._hits) < _PRUNE_ABOVE_ENTRIES or now - self._last_prune < _PRUNE_EVERY_SECONDS:
            return
        self._last_prune = now
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] > _WINDOW_SECONDS]:
            del self._hits[key]

    async def dispatch(self, request: Request, call_next):
        client_ip = client_key(request)
        now = time.monotonic()
        self._prune(now)
        hits = self._hits[client_ip]
        while hits and now - hits[0] > _WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                {"error": f"Rate limit exceeded: {RATE_LIMIT_PER_MINUTE} requests/minute per IP. Run this server locally (see README) for unlimited use."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        hits.append(now)
        return await call_next(request)


app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        # Non-browser MCP clients generally send no Origin header at all
        # (same-origin requests, and requests with no Origin, are always
        # allowed regardless of this list — see _validate_origin) so this
        # only matters for browser-based clients, which none of these tools
        # are designed for.
        allowed_origins=[],
    )
)
app.add_middleware(RateLimitMiddleware)
