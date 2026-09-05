"""
WickedAPI + Wicked Reputation MCP server.

Wraps the public HTTP surface of both live services as MCP tools — no new
backend logic, no secrets required for the reputation side (every endpoint
it calls is public/unauthenticated by design, see stake.wickedapi.com's
/transparency). WickedAPI's market-data endpoints work unauthenticated too,
falling back to x402 pay-per-call; set WICKEDAPI_API_KEY for free-tier
header auth instead (see https://api.wickedapi.com and the cookbooks/ in
this repo for the same pattern applied to LangChain/CrewAI/LlamaIndex/
ElizaOS).

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
WICKEDAPI_API_KEY = os.environ.get("WICKEDAPI_API_KEY", "")

mcp = MCPServer(
    "wicked-reputation",
    instructions=(
        "Tools for WickedAPI (pay-per-call trading data on Base, x402 or "
        "x-api-key) and Wicked Reputation (on-chain agent staking/reputation "
        "on Base). Reputation tools are all free and unauthenticated. "
        "WickedAPI tools work without a key via x402 (a 402 response carries "
        "payment instructions in its body) or with a free-tier key set via "
        "WICKEDAPI_API_KEY."
    ),
)


async def _get(base_url: str, path: str, params: dict[str, Any], *, with_wickedapi_key: bool = False) -> dict[str, Any]:
    headers = {}
    if with_wickedapi_key and WICKEDAPI_API_KEY:
        headers["x-api-key"] = WICKEDAPI_API_KEY
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


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
