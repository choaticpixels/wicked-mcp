"""
WickedAPI + Wicked Reputation + Wicked Registry + Wicked Identity MCP server.

Wraps the public HTTP surface of all four live services as MCP tools — no
new backend logic, no secrets required for the reputation side (every
endpoint it calls is public/unauthenticated by design, see
stake.wickedapi.com's /transparency). WickedAPI's, Wicked Registry's, and
Wicked Identity's paid endpoints work unauthenticated too, falling back to
x402 pay-per-call; set WICKEDAPI_API_KEY / REGISTRY_API_KEY / IDENTITY_API_KEY
for free-tier header auth instead (see https://api.wickedapi.com and the
cookbooks/ in this repo for the same pattern applied to
LangChain/CrewAI/LlamaIndex/ElizaOS).

Wicked Identity's tools do not sign anything themselves, same principle as
registry_register_tool below: fetch a nonce, sign it with your own wallet,
then call the tool with the signature. This server never holds or asks for
a private key.

Every tool returns the upstream JSON body as-is, plus an `http_status`
field. Non-2xx responses are NOT raised as exceptions — a 402 from
WickedAPI carries the x402 payment instructions in its body, and a 404 from
the reputation service just means "this wallet has never registered" —
both are meaningful data for the calling agent, not failures to hide.

Run locally (stdio, for Claude Desktop / `mcp dev`):
    python server.py

Run as a remote server (streamable HTTP, for deployment):
    MCP_TRANSPORT=streamable-http python server.py
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

WICKEDAPI_BASE_URL = os.environ.get("WICKEDAPI_BASE_URL", "https://api.wickedapi.com")
REPUTATION_BASE_URL = os.environ.get("REPUTATION_BASE_URL", "https://stake.wickedapi.com")
REGISTRY_BASE_URL = os.environ.get("REGISTRY_BASE_URL", "https://registry.wickedapi.com")
IDENTITY_BASE_URL = os.environ.get("IDENTITY_BASE_URL", "https://verify.wickedapi.com")
WICKEDAPI_API_KEY = os.environ.get("WICKEDAPI_API_KEY", "")
REGISTRY_API_KEY = os.environ.get("REGISTRY_API_KEY", "")
IDENTITY_API_KEY = os.environ.get("IDENTITY_API_KEY", "")

mcp = MCPServer(
    "wicked-reputation",
    instructions=(
        "Tools for WickedAPI (pay-per-call trading data on Base, x402 or "
        "x-api-key), Wicked Reputation (on-chain agent staking/reputation on "
        "Base), Wicked Registry (real reliability scores for x402/MCP "
        "tools — uptime, latency, schema conformance, from real synthetic "
        "checks, never fabricated), and Wicked Identity (reverse-CAPTCHA / "
        "Know-Your-Agent verification — proves a caller is an autonomous "
        "agent via a real time-boxed challenge and binds it to a wallet). "
        "Reputation tools are all free and unauthenticated. WickedAPI, "
        "Wicked Registry, and Wicked Identity tools work without a key via "
        "x402 (a 402 response carries payment instructions — in the JSON "
        "body for WickedAPI/Registry, or under a `payment_required` key "
        "decoded from the newer x402 v2 header format for Wicked Identity) "
        "or with a free-tier key set via WICKEDAPI_API_KEY / REGISTRY_API_KEY "
        "/ IDENTITY_API_KEY respectively."
    ),
)


def _extract_payment_required(resp: httpx.Response) -> dict[str, Any] | None:
    """x402 v2 (Wicked Identity) puts the payment-required payload in a
    base64-encoded PAYMENT-REQUIRED response header instead of the JSON
    body that x402 v1 (WickedAPI, Wicked Registry) uses — decode it so a 402
    from either version is equally useful to the calling agent."""
    header = resp.headers.get("payment-required")
    if not header:
        return None
    try:
        return json.loads(base64.b64decode(header))
    except (ValueError, TypeError):
        return None


def _finalize(resp: httpx.Response, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        body = {"data": body}
    body["http_status"] = resp.status_code
    payment_required = _extract_payment_required(resp)
    if payment_required is not None:
        body["payment_required"] = payment_required
    return body


async def _get(
    base_url: str,
    path: str,
    params: dict[str, Any],
    *,
    with_wickedapi_key: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    headers = {}
    if with_wickedapi_key and WICKEDAPI_API_KEY:
        headers["x-api-key"] = WICKEDAPI_API_KEY
    if api_key:
        headers["x-api-key"] = api_key
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base_url}{path}", params=params, headers=headers)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw_body": resp.text}
    return _finalize(resp, body)


async def _post(base_url: str, path: str, json_body: dict[str, Any], *, api_key: str | None = None) -> dict[str, Any]:
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    json_body = {k: v for k, v in json_body.items() if v is not None}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base_url}{path}", json=json_body, headers=headers)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw_body": resp.text}
    return _finalize(resp, body)


# ---------------------------------------------------------------------------
# WickedAPI — trading data
# ---------------------------------------------------------------------------

@mcp.tool()
async def wickedapi_price(symbol: str, asset_class: str | None = None) -> dict[str, Any]:
    """Get the current price for a symbol (crypto or stock) from WickedAPI.

    Args:
        symbol: e.g. BTC, ETH, SUI, NVDA, SPY.
        asset_class: "stock" or "crypto" — required for symbols outside the
            curated list, optional otherwise.
    """
    return await _get(WICKEDAPI_BASE_URL, "/v1/price", {"symbol": symbol, "class": asset_class}, with_wickedapi_key=True)


@mcp.tool()
async def wickedapi_momentum(symbol: str, timeframe: str = "1h") -> dict[str, Any]:
    """Get WickedAPI's momentum score for a symbol.

    Args:
        symbol: One of BTC, ETH, SOL, SUI, NVDA, IBIT, COIN, QQQ, TQQQ, DIA.
        timeframe: One of 15m, 1h, 4h, 1D, 1W. Defaults to 1h.
    """
    return await _get(
        WICKEDAPI_BASE_URL, "/v1/momentum-score", {"symbol": symbol, "timeframe": timeframe}, with_wickedapi_key=True
    )


@mcp.tool()
async def wickedapi_funding_oi(symbol: str) -> dict[str, Any]:
    """Get perpetual-futures funding rate and open interest for a crypto symbol.

    Args:
        symbol: Base crypto symbol, e.g. BTC, ETH, SOL — mapped to the
            <SYMBOL>USDT perpetual on Binance/Bybit.
    """
    return await _get(WICKEDAPI_BASE_URL, "/v1/funding-oi", {"symbol": symbol}, with_wickedapi_key=True)


# ---------------------------------------------------------------------------
# Wicked Reputation — on-chain agent staking/reputation (all public, free)
# ---------------------------------------------------------------------------

@mcp.tool()
async def reputation_status(wallet: str) -> dict[str, Any]:
    """Get an agent's current reputation: tier, active/unbonding stake,
    reputation score, slash count, founding-member badge. Returns
    http_status 404 for a wallet that has never registered — treat that the
    same as tier "unranked", not an error.

    Args:
        wallet: The agent's 0x wallet address on Base.
    """
    return await _get(REPUTATION_BASE_URL, f"/agents/{wallet.lower()}/status", {})


@mcp.tool()
async def reputation_statement(wallet: str) -> dict[str, Any]:
    """Get an agent's full position + real event ledger (stakes, unstakes,
    withdrawals, slashes) from Wicked Reputation's own records. Does NOT
    include WickedAPI call volume or fees paid — see the `note` field in the
    response for exactly what this does and doesn't cover.

    Args:
        wallet: The agent's 0x wallet address on Base.
    """
    return await _get(REPUTATION_BASE_URL, f"/agents/{wallet.lower()}/statement", {})


@mcp.tool()
async def reputation_query(
    tier: str | None = None,
    min_stake: float | None = None,
    max_stake: float | None = None,
    min_reputation: float | None = None,
    founding_member: bool | None = None,
    min_slash_count: int | None = None,
    sort: str = "-stake_amount",
    limit: int = 20,
) -> dict[str, Any]:
    """Search/rank registered agents by tier, stake, reputation, or slash
    history. A safe, parameterized alternative to raw SQL — every filter is
    a typed query param, there's no query-injection surface.

    Args:
        tier: Exact tier name to filter to, e.g. "gold".
        min_stake / max_stake: Active-stake bounds in USDC.
        min_reputation: Minimum reputation score.
        founding_member: True to only show founding members.
        min_slash_count: Minimum number of slash events (e.g. 1 to find agents that have ever been slashed).
        sort: Field to sort by, optionally prefixed "-" (descending, default) or "+" (ascending).
            One of stake_amount, reputation_score, slash_count, registered_at.
        limit: Max results, 1-200. Defaults to 20.
    """
    return await _get(
        REPUTATION_BASE_URL,
        "/agents/query",
        {
            "tier": tier,
            "min_stake": min_stake,
            "max_stake": max_stake,
            "min_reputation": min_reputation,
            "founding_member": founding_member,
            "min_slash_count": min_slash_count,
            "sort": sort,
            "limit": limit,
        },
    )


@mcp.tool()
async def reputation_tiers() -> dict[str, Any]:
    """Get Wicked Reputation's live tier thresholds and benefits
    (min stake required, rate-limit multiplier, fee-discount %). Read at
    call time rather than assumed — thresholds are admin-configurable."""
    return await _get(REPUTATION_BASE_URL, "/tiers", {})


@mcp.tool()
async def reputation_transparency() -> dict[str, Any]:
    """Get Wicked Reputation's live transparency numbers: treasury address,
    custody model, block-explorer link, unbonding cooldown, total active
    stake, total agents, total slash events, founding-slot counts."""
    return await _get(REPUTATION_BASE_URL, "/transparency", {})


# ---------------------------------------------------------------------------
# Wicked Registry — real reliability scores for x402/MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def registry_search_tools(
    category: str | None = None,
    protocol: str | None = None,
    min_score: float | None = None,
    sort: str = "score",
    limit: int = 50,
) -> dict[str, Any]:
    """Search Wicked Registry for x402/MCP tools by real, live reliability
    data (uptime, latency, schema conformance — never fabricated).

    Works unauthenticated via x402 (a 402 response carries payment
    instructions in its body) or with a free-tier key set via
    REGISTRY_API_KEY.

    Args:
        category: Filter by category, e.g. "data", "crypto".
        protocol: Filter by protocol — "x402", "mcp", or "rest".
        min_score: Minimum composite score (0-100). Tools with no check
            history yet are excluded once this is set, rather than treated
            as a 0.
        sort: One of "score" (default), "latency", "recency".
        limit: Max results, 1-200. Defaults to 50.
    """
    return await _get(
        REGISTRY_BASE_URL,
        "/tools",
        {"category": category, "protocol": protocol, "min_score": min_score, "sort": sort, "limit": limit},
        api_key=REGISTRY_API_KEY or None,
    )


@mcp.tool()
async def registry_tool_detail(tool_id: str) -> dict[str, Any]:
    """Get one tool's full score breakdown, component history, and recent
    real health checks from Wicked Registry.

    Works unauthenticated via x402, or with a free-tier key set via
    REGISTRY_API_KEY.

    Args:
        tool_id: The tool's Wicked Registry id, from registry_search_tools
            or registry_featured_tools.
    """
    return await _get(REGISTRY_BASE_URL, f"/tools/{tool_id}", {}, api_key=REGISTRY_API_KEY or None)


@mcp.tool()
async def registry_featured_tools() -> dict[str, Any]:
    """Get Wicked Registry's top scored active tools. Public, free, no key
    or payment required — a quick look before calling registry_search_tools
    with a specific filter."""
    return await _get(REGISTRY_BASE_URL, "/featured-tools", {})


@mcp.tool()
async def registry_tool_badge(tool_id: str) -> dict[str, Any]:
    """Get a tool's current composite reliability score. Public, free, no
    key or payment required — the same badge a tool owner embeds on their
    own docs.

    Args:
        tool_id: The tool's Wicked Registry id.
    """
    return await _get(REGISTRY_BASE_URL, f"/tools/{tool_id}/badge", {})


@mcp.tool()
async def registry_report_tool(
    tool_id: str, reporter: str, reason: str, evidence: str | None = None
) -> dict[str, Any]:
    """File a real complaint about a tool's reliability or behavior with
    Wicked Registry. Public, rate-limited, no auth required. Reviewed
    manually by an admin — filing a report does not auto-suspend the tool.

    Args:
        tool_id: The tool's Wicked Registry id.
        reporter: Your wallet address, or another best-effort identifier.
        reason: One of "downtime", "incorrect_response", "scam_or_abuse",
            "schema_violation", "other".
        evidence: Optional free-text note or URL supporting the report.
    """
    return await _post(
        REGISTRY_BASE_URL,
        f"/tools/{tool_id}/report",
        {"reporter": reporter, "reason": reason, "evidence": evidence},
    )


@mcp.tool()
async def registry_register_tool(
    name: str,
    endpoint_url: str,
    protocol: str,
    category: str,
    description: str,
    owner_wallet: str,
    signature: str,
    timestamp: str,
    schema_url: str | None = None,
) -> dict[str, Any]:
    """Register a tool you own with Wicked Registry so it starts getting
    real synthetic checks and a live reliability score.

    Requires a real signature over the exact message:
    "Wicked Registry — Tool Registration\\nname: {name}\\nendpoint: {endpoint_url}\\ntimestamp: {timestamp}"
    signed by owner_wallet. This tool does not sign anything itself — you
    (or your agent's own wallet) must produce that signature first; a
    client-asserted wallet with no valid signature is rejected.

    Args:
        name: Tool name.
        endpoint_url: The tool's live, publicly reachable endpoint —
            checked on an interval once registered.
        protocol: One of "x402", "mcp", "rest".
        category: Free-text category, e.g. "data", "crypto".
        description: What the tool does.
        owner_wallet: The 0x wallet address that signed the registration message.
        signature: The 0x-prefixed ECDSA signature over the registration message.
        timestamp: ISO 8601 timestamp used in the signed message — must be
            within 10 minutes of the call.
        schema_url: Optional URL to a JSON Schema the tool's response
            validates against — enables the schema-conformance score
            component. Omit if the tool doesn't publish one.
    """
    return await _post(
        REGISTRY_BASE_URL,
        "/tools/register",
        {
            "name": name,
            "endpointUrl": endpoint_url,
            "protocol": protocol,
            "category": category,
            "description": description,
            "ownerWallet": owner_wallet,
            "signature": signature,
            "timestamp": timestamp,
            "schemaUrl": schema_url,
        },
    )


# ---------------------------------------------------------------------------
# Wicked Identity — reverse-CAPTCHA / Know-Your-Agent verification
# ---------------------------------------------------------------------------

@mcp.tool()
async def identity_get_nonce(wallet: str) -> dict[str, Any]:
    """Get a one-time nonce + message to sign for Wicked Identity — the
    first step before identity_register, identity_get_challenge, or
    identity_submit_response, each of which needs a FRESH nonce (single-use,
    short TTL — don't reuse one across calls).

    Args:
        wallet: Your agent's 0x wallet address on Base.
    """
    return await _get(IDENTITY_BASE_URL, f"/agents/{wallet.lower()}/nonce", {})


@mcp.tool()
async def identity_register(wallet: str, nonce: str, signature: str) -> dict[str, Any]:
    """Register your wallet with Wicked Identity — a lightweight identity
    record, no stake required. Requires a real signature over the exact
    message identity_get_nonce returned; this tool does not sign anything
    itself.

    Args:
        wallet: Your agent's 0x wallet address on Base.
        nonce: The nonce from a fresh identity_get_nonce call.
        signature: The 0x-prefixed ECDSA signature over that nonce's message.
    """
    return await _post(IDENTITY_BASE_URL, "/agents/register", {"wallet_address": wallet, "nonce": nonce, "signature": signature})


@mcp.tool()
async def identity_get_challenge(wallet: str, nonce: str, signature: str) -> dict[str, Any]:
    """Request a real, time-boxed agent-liveness challenge from Wicked
    Identity for an already-registered wallet. Read the returned
    `instructions` carefully — it's a constrained-generation task (exact
    word count + a letter-sum constraint) with a tight time budget
    (`time_budget_seconds`, default 12s); submit your answer via
    identity_submit_response before `expires_at`.

    Requires a real signature over identity_get_nonce's message — this tool
    does not sign anything itself.

    Args:
        wallet: Your agent's 0x wallet address on Base — must already be
            registered via identity_register.
        nonce: The nonce from a fresh identity_get_nonce call.
        signature: The 0x-prefixed ECDSA signature over that nonce's message.
    """
    return await _post(
        IDENTITY_BASE_URL, "/verify/challenge", {"wallet_address": wallet, "nonce": nonce, "signature": signature}
    )


@mcp.tool()
async def identity_submit_response(
    wallet: str, nonce: str, signature: str, challenge_id: str, response_text: str
) -> dict[str, Any]:
    """Submit your answer to a Wicked Identity challenge. Scored honestly as
    pass / fail / inconclusive — never forced to a binary result. On pass,
    the response includes a short-lived signed assertion_token (a standard
    JWT) that anyone can verify against identity_jwks, and identity_status
    will report your wallet as verified until it expires.

    Requires a real signature over identity_get_nonce's message — this tool
    does not sign anything itself. Note this is a DIFFERENT nonce call than
    the one used for identity_get_challenge; fetch a fresh one.

    Args:
        wallet: Your agent's 0x wallet address on Base.
        nonce: The nonce from a fresh identity_get_nonce call.
        signature: The 0x-prefixed ECDSA signature over that nonce's message.
        challenge_id: The challenge_id from identity_get_challenge.
        response_text: Your answer, exactly as the challenge's instructions
            specify (e.g. raw lowercase words, no extra commentary).
    """
    return await _post(
        IDENTITY_BASE_URL,
        "/verify/response",
        {
            "wallet_address": wallet,
            "nonce": nonce,
            "signature": signature,
            "challenge_id": challenge_id,
            "response_text": response_text,
        },
    )


@mcp.tool()
async def identity_status(wallet: str) -> dict[str, Any]:
    """Check whether a wallet currently holds a valid, unexpired Wicked
    Identity assertion — the read-only check any third party can run on
    someone else's wallet, no signature needed.

    Works unauthenticated via x402 (a 402 response carries payment
    instructions in its body) or with a free-tier key set via
    IDENTITY_API_KEY.

    Args:
        wallet: The 0x wallet address to check.
    """
    return await _get(IDENTITY_BASE_URL, f"/verify/status/{wallet.lower()}", {}, api_key=IDENTITY_API_KEY or None)


@mcp.tool()
async def identity_jwks() -> dict[str, Any]:
    """Get Wicked Identity's public JWKS (RS256 keys) — use this to verify
    an assertion_token's signature locally with a standard JWT library,
    independent of calling identity_status. Public, free, no key needed."""
    return await _get(IDENTITY_BASE_URL, "/.well-known/jwks.json", {})


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
