"""
WickedAPI + Wicked Reputation + Wicked Registry MCP server.

Wraps the public HTTP surface of all three live services as MCP tools — no
new backend logic, no secrets required for the reputation side (every
endpoint it calls is public/unauthenticated by design, see
stake.wickedapi.com's /transparency). WickedAPI's and Wicked Registry's
market-data/paid endpoints work unauthenticated too, falling back to x402
pay-per-call; set WICKEDAPI_API_KEY / REGISTRY_API_KEY for free-tier header
auth instead (see https://api.wickedapi.com and the cookbooks/ in this repo
for the same pattern applied to LangChain/CrewAI/LlamaIndex/ElizaOS).

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

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

WICKEDAPI_BASE_URL = os.environ.get("WICKEDAPI_BASE_URL", "https://api.wickedapi.com")
REPUTATION_BASE_URL = os.environ.get("REPUTATION_BASE_URL", "https://stake.wickedapi.com")
REGISTRY_BASE_URL = os.environ.get("REGISTRY_BASE_URL", "https://registry.wickedapi.com")
WICKEDAPI_API_KEY = os.environ.get("WICKEDAPI_API_KEY", "")
REGISTRY_API_KEY = os.environ.get("REGISTRY_API_KEY", "")

mcp = MCPServer(
    "wicked-reputation",
    instructions=(
        "Tools for WickedAPI (pay-per-call trading data on Base, x402 or "
        "x-api-key), Wicked Reputation (on-chain agent staking/reputation on "
        "Base), and Wicked Registry (real reliability scores for x402/MCP "
        "tools — uptime, latency, schema conformance, from real synthetic "
        "checks, never fabricated). Reputation tools are all free and "
        "unauthenticated. WickedAPI and Wicked Registry tools work without a "
        "key via x402 (a 402 response carries payment instructions in its "
        "body) or with a free-tier key set via WICKEDAPI_API_KEY / "
        "REGISTRY_API_KEY respectively."
    ),
)


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
    if isinstance(body, dict):
        body["http_status"] = resp.status_code
        return body
    return {"data": body, "http_status": resp.status_code}


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
    if isinstance(body, dict):
        body["http_status"] = resp.status_code
        return body
    return {"data": body, "http_status": resp.status_code}


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


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
