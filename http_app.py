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

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
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
