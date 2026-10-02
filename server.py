"""
WickedAPI + Wicked Reputation + Wicked Registry + Wicked Identity + Wicked Sanity
MCP server.

Wraps the public HTTP surface of all five live services as MCP tools — no
new backend logic, no secrets required for the reputation side (every
endpoint it calls is public/unauthenticated by design, see
stake.wickedapi.com's /transparency). WickedAPI's, Wicked Registry's, and
Wicked Identity's paid endpoints work unauthenticated too, falling back to
x402 pay-per-call; set WICKEDAPI_API_KEY / REGISTRY_API_KEY / IDENTITY_API_KEY
/ SANITY_API_KEY for free-tier header auth instead (see https://api.wickedapi.com and the
cookbooks/ in this repo for the same pattern applied to
LangChain/CrewAI/LlamaIndex/ElizaOS).

Wicked Identity's tools do not sign anything themselves, same principle as
registry_register_tool below: fetch a nonce, sign it with your own wallet,
then call the tool with the signature. This server never holds or asks for
a private key.

Every tool returns the upstream JSON body as-is, plus an `http_status`
field. Non-2xx responses are NOT raised as exceptions — a 402 from
WickedAPI carries the x402 payment instructions in its body (older x402
v1), a 402 from Wicked Registry's search/detail tools or Wicked Identity's
status tool carries them in a `payment_required` key decoded from the
response's PAYMENT-REQUIRED header instead (newer x402 v2, see
_extract_payment_required below), and a 404 from the reputation service
just means "this wallet has never registered" — all of that is meaningful
data for the calling agent, not failures to hide.

Run locally (stdio, for Claude Desktop / `mcp dev`):
    python server.py

Run as a remote server (streamable HTTP, for deployment):
    MCP_TRANSPORT=streamable-http python server.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from mcp.server.mcpserver import MCPServer

WICKEDAPI_BASE_URL = os.environ.get("WICKEDAPI_BASE_URL", "https://api.wickedapi.com")
REPUTATION_BASE_URL = os.environ.get("REPUTATION_BASE_URL", "https://stake.wickedapi.com")
REGISTRY_BASE_URL = os.environ.get("REGISTRY_BASE_URL", "https://registry.wickedapi.com")
IDENTITY_BASE_URL = os.environ.get("IDENTITY_BASE_URL", "https://verify.wickedapi.com")
SANITY_BASE_URL = os.environ.get("SANITY_BASE_URL", "https://sanity.wickedapi.com")
MEMORY_BASE_URL = os.environ.get("MEMORY_BASE_URL", "https://memory.wickedapi.com")
MEMORY_API_KEY = os.environ.get("MEMORY_API_KEY", "")
WICKEDAPI_API_KEY = os.environ.get("WICKEDAPI_API_KEY", "")
REGISTRY_API_KEY = os.environ.get("REGISTRY_API_KEY", "")
IDENTITY_API_KEY = os.environ.get("IDENTITY_API_KEY", "")
SANITY_API_KEY = os.environ.get("SANITY_API_KEY", "")

mcp = MCPServer(
    "wicked-reputation",
    instructions=(
        "Tools for WickedAPI (pay-per-call trading data on Base, x402 or "
        "x-api-key), Wicked Reputation (on-chain agent staking/reputation on "
        "Base), Wicked Registry (real reliability scores for x402/MCP "
        "tools — uptime, latency, schema conformance, from real synthetic "
        "checks, never fabricated), and Wicked Identity (reverse-CAPTCHA / "
        "Know-Your-Agent verification — proves a caller is an autonomous "
        "agent via a real time-boxed challenge and binds it to a wallet), and "
        "Wicked Sanity (hallucination / eval check — verifies a claim against "
        "context you supply, or against live web evidence, before you act on "
        "it). "
        "Reputation tools are all free and unauthenticated (as are Wicked "
        "Registry's featured/badge/report/register tools and Wicked "
        "Identity's register/challenge/response/jwks tools). WickedAPI's "
        "paid tools, Wicked Registry's search/detail tools, and Wicked "
        "Identity's status tool work without a key via x402 (a 402 response "
        "carries payment instructions — in the JSON body for WickedAPI's "
        "older x402 v1, or under a `payment_required` key decoded from the "
        "newer x402 v2 PAYMENT-REQUIRED header for Wicked Registry and "
        "Wicked Identity) or with a free-tier key set via WICKEDAPI_API_KEY "
        "/ REGISTRY_API_KEY / IDENTITY_API_KEY respectively. Wicked "
        "Sanity's sanity_check / sanity_check_batch tools work the same way "
        "(x402 v2 payment instructions under `payment_required`, or a "
        "free-tier key via SANITY_API_KEY). Wicked Memory is persistent, "
        "wallet-scoped agent memory (store, semantic search, versioned "
        "history, hard delete): every memory_* call needs a fresh wallet "
        "signature, so call memory_prepare first, sign the message it "
        "returns with your own wallet, then call the memory_* tool — this "
        "server never signs or holds a key. memory_search is the metered "
        "call (x402 v2 under `payment_required`, or a free-tier key via "
        "MEMORY_API_KEY)."
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


async def _post(
    base_url: str,
    path: str,
    json_body: dict[str, Any],
    *,
    api_key: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    json_body = {k: v for k, v in json_body.items() if v is not None}
    async with httpx.AsyncClient(timeout=timeout) as client:
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


# ---------------------------------------------------------------------------
# Wicked Sanity — hallucination / eval check
# ---------------------------------------------------------------------------

# Open mode runs a live web search (and batches run one per claim), so these
# calls can legitimately take longer than the 20s used by the other tools.
_SANITY_TIMEOUT = 90.0


@mcp.tool()
async def sanity_check(claim: str, context: str | list[str] | None = None, mode: str = "grounded") -> dict[str, Any]:
    """Verify a claim before you act on it or hand it downstream. Every
    verdict comes from a real model inference run at request time (HHEM
    entailment) — never cached or guessed.

    Two modes:
      - "grounded" (default): is the claim faithful to the `context` you
        supply? `context` is REQUIRED. This is a scoreable question.
      - "open": is the claim consistent with live web evidence found right
        now (real Tavily search, then the same model)? `context` is ignored.
        This is consistency with current web content, NOT objective truth —
        a claim can be well-supported by the web and still be wrong.

    Reading the result (in `data`):
      - `verdict`: "supported" (consistent with the source), "contradicted"
        (clearly inconsistent), "unsupported" (not supported but not clearly
        contradicted — treat as unverified), or "insufficient_evidence"
        (open mode found nothing usable; says nothing about whether the
        claim is true).
      - `confidence_score`: the raw consistency score in [0, 1] — near 1 =
        consistent with the source, near 0 = inconsistent. It is NOT
        confidence-in-the-verdict: a "contradicted" verdict has a score near
        0. It is 0.0 for "insufficient_evidence".
      - `evidence`: the audit trail (context snippet, or web citations).
    Suggested policy: act on "supported"; treat "unsupported" and
    "insufficient_evidence" as unverified; reject or regenerate on
    "contradicted".

    Access: the public mcp.wickedapi.com server uses a shared, rate-limited
    key, so this returns real verdicts directly -- but the shared budget is
    small (about 20 requests/minute and 30 open-mode checks/day across ALL
    users; grounded mode is not counted against the daily limit). An
    http_status 429 means that shared budget is used up: retry after the
    Retry-After seconds, run this server locally with your own
    SANITY_API_KEY, or pay per call via x402 directly. A server with no key
    configured returns an http_status 402 carrying x402 payment instructions
    under `payment_required` instead.

    Args:
        claim: The single statement to verify (max 5,000 characters).
        context: Source text the claim must be faithful to — a string or a
            list of strings. Required for mode "grounded".
        mode: "grounded" (check against `context`) or "open" (check against
            live web evidence).
    """
    return await _post(
        SANITY_BASE_URL,
        "/check",
        {"claim": claim, "context": context, "mode": mode},
        api_key=SANITY_API_KEY or None,
        timeout=_SANITY_TIMEOUT,
    )


@mcp.tool()
async def sanity_check_batch(
    claims: list[str], context: str | list[str] | None = None, mode: str = "grounded"
) -> dict[str, Any]:
    """Verify several claims in one call — use this for a multi-sentence
    output instead of calling sanity_check once per sentence. Split the
    output into individual claims, pass the source as `context`, and every
    claim is checked against it. One payment / one rate-limit hit covers the
    whole batch, and grounded batches are scored far faster than N separate
    calls.

    Returns `data` as a list with one result per claim, in the same order
    as `claims`; each result is shaped exactly like sanity_check's (see
    that tool for how to read `verdict`, `confidence_score` and `evidence`).
    In "open" mode a separate live web search runs per claim, so large open
    batches are slow.

    Access: the public mcp.wickedapi.com server uses a shared, rate-limited
    key, so this returns real verdicts directly -- but the shared budget is
    small (about 20 requests/minute and 30 open-mode checks/day across ALL
    users; grounded mode is not counted against the daily limit). An
    http_status 429 means that shared budget is used up: retry after the
    Retry-After seconds, run this server locally with your own
    SANITY_API_KEY, or pay per call via x402 directly. A server with no key
    configured returns an http_status 402 carrying x402 payment instructions
    under `payment_required` instead.

    Args:
        claims: 1-50 statements to verify, each up to 5,000 characters.
        context: Shared source text for every claim — a string or a list of
            strings. Required for mode "grounded".
        mode: "grounded" (check against `context`) or "open" (check against
            live web evidence).
    """
    return await _post(
        SANITY_BASE_URL,
        "/check/batch",
        {"claims": claims, "context": context, "mode": mode},
        api_key=SANITY_API_KEY or None,
        timeout=_SANITY_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Wicked Memory — portable, wallet-scoped agent memory
# ---------------------------------------------------------------------------
#
# Every /memories* call carries a per-request wallet signature (EIP-191
# personal_sign) over a message binding the wallet to that exact method, path,
# body, timestamp and nonce. Like the Identity tools above, this server never
# signs anything or holds a key: call memory_prepare, sign the message it
# returns with your own wallet, then call the memory_* tool with the
# timestamp/nonce/signature. The tool rebuilds the identical request, so pass
# exactly the same arguments to both calls.

_MEMORY_OPS = {"store", "search", "get", "update", "history", "delete", "deletions"}
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _memory_clean(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}


def _memory_id(params: dict[str, Any]) -> str:
    try:
        return str(uuid.UUID(str(params.get("memory_id"))))
    except ValueError:
        raise ValueError("memory_id must be a UUID") from None


def _memory_build(operation: str, params: dict[str, Any] | None) -> tuple[str, str, bytes | None]:
    """Deterministically build (method, path-with-query, raw body bytes) for an
    operation. The path and body bytes are exactly what gets signed and sent."""
    p = _memory_clean(params)
    if operation not in _MEMORY_OPS:
        raise ValueError(f"unknown operation {operation!r}; expected one of {sorted(_MEMORY_OPS)}")

    def body(keys: tuple[str, ...]) -> bytes:
        return json.dumps({k: p[k] for k in keys if k in p}, separators=(",", ":"), ensure_ascii=False).encode()

    if operation == "store":
        if "content" not in p:
            raise ValueError("store requires content")
        return "POST", "/memories", body(("content", "tags", "metadata", "source"))
    if operation == "search":
        if "q" not in p:
            raise ValueError("search requires q")
        q: dict[str, Any] = {"q": p["q"]}
        for key in ("limit", "created_after", "created_before", "as_of"):
            if key in p:
                q[key] = p[key]
        if "tags" in p:
            q["tags"] = ",".join(p["tags"]) if isinstance(p["tags"], list) else p["tags"]
        if "include_superseded" in p:
            q["include_superseded"] = "true" if p["include_superseded"] else "false"
        return "GET", "/memories/search?" + urlencode(q, quote_via=quote), None
    if operation == "get":
        return "GET", f"/memories/{_memory_id(p)}", None
    if operation == "update":
        return "PATCH", f"/memories/{_memory_id(p)}", body(("content", "tags", "metadata", "source"))
    if operation == "history":
        return "GET", f"/memories/{_memory_id(p)}/history", None
    if operation == "delete":
        # scope is always explicit so memory_prepare (which may omit it) and memory_delete (whose
        # default is "chain") sign and send the identical path.
        q = {"scope": p.get("scope", "chain")}
        if "reason" in p:
            q["reason"] = p["reason"]
        return "DELETE", f"/memories/{_memory_id(p)}?" + urlencode(q, quote_via=quote), None
    return "GET", "/memory-deletions", None


def _memory_message(wallet: str, method: str, path: str, timestamp: str, nonce: str, raw_body: bytes | None) -> str:
    sha = hashlib.sha256(raw_body).hexdigest() if raw_body else _EMPTY_SHA256
    return "\n".join(
        [
            "Wicked Memory auth",
            f"wallet: {wallet.lower()}",
            f"method: {method.upper()}",
            f"path: {path}",
            f"timestamp: {timestamp}",
            f"nonce: {nonce}",
            f"body-sha256: {sha}",
        ]
    )


@mcp.tool()
async def memory_prepare(operation: str, wallet: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Step 1 of every Wicked Memory call: get the exact message to sign.
    Wicked Memory is persistent, wallet-scoped memory for agents (store,
    semantic search, versioned history, hard delete). Each call needs a fresh
    wallet signature; this server never signs or holds a key.

    Sign `message_to_sign` with your wallet (EIP-191 personal_sign), then
    call the matching tool (memory_store, memory_search, ...) with the SAME
    arguments plus the returned `timestamp`, `nonce` and your `signature`.
    The timestamp must be within 5 minutes and the nonce is single-use, so
    prepare again for every call.

    Args:
        operation: One of "store", "search", "get", "update", "history",
            "delete", "deletions".
        wallet: Your agent's 0x wallet address (your memories are scoped to it).
        params: The same arguments you will pass to the tool, by name, e.g.
            {"content": "...", "tags": ["a"]} for store, {"q": "..."} for
            search, {"memory_id": "<uuid>"} for get/update/history/delete.
    """
    try:
        method, path, raw = _memory_build(operation, params)
    except ValueError as exc:
        return {"error": str(exc), "source": "mcp", "http_status": 400}
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {
        "message_to_sign": _memory_message(wallet, method, path, timestamp, nonce, raw),
        "timestamp": timestamp,
        "nonce": nonce,
        "operation": operation,
        "wallet": wallet,
        "request": {"method": method, "path": path},
        "next": f"Sign message_to_sign with the wallet's key (EIP-191 personal_sign), then call memory_{operation} with the same arguments plus timestamp, nonce and signature.",
    }


async def _memory_call(
    operation: str,
    wallet: str,
    params: dict[str, Any],
    timestamp: str,
    nonce: str,
    signature: str,
    *,
    payment_signature: str | None = None,
) -> dict[str, Any]:
    try:
        method, path, raw = _memory_build(operation, params)
    except ValueError as exc:
        return {"error": str(exc), "source": "mcp", "http_status": 400}
    headers = {
        "x-wallet-address": wallet,
        "x-wallet-timestamp": timestamp,
        "x-wallet-nonce": nonce,
        "x-wallet-signature": signature,
    }
    if raw is not None:
        headers["content-type"] = "application/json"
    if operation == "search":
        if MEMORY_API_KEY:
            headers["x-api-key"] = MEMORY_API_KEY
        if payment_signature:
            headers["PAYMENT-SIGNATURE"] = payment_signature
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, f"{MEMORY_BASE_URL}{path}", content=raw, headers=headers)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw_body": resp.text}
    return _finalize(resp, body)


@mcp.tool()
async def memory_store(
    wallet: str,
    content: str,
    timestamp: str,
    nonce: str,
    signature: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Store a memory in Wicked Memory (free; fair-use rate limited). Call
    memory_prepare with operation "store" and these same arguments first, sign
    its message, then pass timestamp/nonce/signature here. A real embedding is
    computed at write time. Returns the stored memory (`data.id`, validity
    window, ...).

    Args:
        wallet: Your agent's 0x wallet address (scopes the memory).
        content: The text to remember (max 16 KB).
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
        tags: Up to 20 short tags for filtering.
        metadata: Free-form JSON object (max 8 KB).
        source: Which agent/session wrote this.
    """
    return await _memory_call(
        "store", wallet, {"content": content, "tags": tags, "metadata": metadata, "source": source}, timestamp, nonce, signature
    )


@mcp.tool()
async def memory_search(
    wallet: str,
    q: str,
    timestamp: str,
    nonce: str,
    signature: str,
    limit: int | None = None,
    tags: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    as_of: str | None = None,
    include_superseded: bool | None = None,
    payment_signature: str | None = None,
) -> dict[str, Any]:
    """Semantic search over YOUR memories (scoped to `wallet`; no other
    wallet's memories are ever returned). Ranked by real cosine similarity
    computed at query time (`score`). Call memory_prepare with operation
    "search" and these same arguments first.

    Access: this is the one metered endpoint. With a free-tier key
    (MEMORY_API_KEY, if this server has one) it is free and rate-limited;
    otherwise it returns http_status 402 with x402 payment instructions under
    `payment_required` (0.001 USDC on Base). To pay: build and sign the x402
    payment from `payment_required`, then call this tool AGAIN with the SAME
    arguments (same timestamp/nonce/signature, which a 402 does not consume)
    plus `payment_signature`.

    By default only currently-active versions are searched; use `as_of` to
    search memory as it stood at a past instant, or include_superseded=true
    for every version.

    Args:
        wallet: Your agent's 0x wallet address.
        q: What to look for, in natural language.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
        limit: 1-50 results (default 10).
        tags: Only memories having ALL of these tags.
        created_after: ISO-8601 lower bound on creation time.
        created_before: ISO-8601 upper bound on creation time.
        as_of: ISO-8601 instant; search the versions valid at that moment.
        include_superseded: Also search old, superseded versions.
        payment_signature: x402 payment payload (base64) for the keyless path.
    """
    return await _memory_call(
        "search",
        wallet,
        {
            "q": q,
            "limit": limit,
            "tags": tags,
            "created_after": created_after,
            "created_before": created_before,
            "as_of": as_of,
            "include_superseded": include_superseded,
        },
        timestamp,
        nonce,
        signature,
        payment_signature=payment_signature,
    )


@mcp.tool()
async def memory_get(wallet: str, memory_id: str, timestamp: str, nonce: str, signature: str) -> dict[str, Any]:
    """Fetch one of your memories by id. Another wallet's id (or a deleted
    one) returns the same 404 as an id that never existed. Call
    memory_prepare with operation "get" and params {"memory_id": ...} first.

    Args:
        wallet: Your agent's 0x wallet address.
        memory_id: The memory's UUID.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
    """
    return await _memory_call("get", wallet, {"memory_id": memory_id}, timestamp, nonce, signature)


@mcp.tool()
async def memory_update(
    wallet: str,
    memory_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    content: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Update a memory WITHOUT overwriting it: creates a new version and
    closes the old one (`valid_until` / `superseded_by`), so history is kept.
    Omitted fields carry over. Returns `previous` and `current`. Only the
    current version can be updated (409 otherwise, naming the current id).
    Call memory_prepare with operation "update" and the same arguments first.

    Args:
        wallet: Your agent's 0x wallet address.
        memory_id: UUID of the CURRENT version to supersede.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
        content: New text (re-embedded). Provide at least one of content/tags/metadata/source.
        tags: Replacement tags.
        metadata: Replacement metadata object.
        source: New source label.
    """
    return await _memory_call(
        "update",
        wallet,
        {"memory_id": memory_id, "content": content, "tags": tags, "metadata": metadata, "source": source},
        timestamp,
        nonce,
        signature,
    )


@mcp.tool()
async def memory_history(wallet: str, memory_id: str, timestamp: str, nonce: str, signature: str) -> dict[str, Any]:
    """Full version chain for a memory, oldest first, with each version's
    `valid_from` / `valid_until` / `superseded_by`. Works from any version's
    id. Call memory_prepare with operation "history" first.

    Args:
        wallet: Your agent's 0x wallet address.
        memory_id: UUID of any version in the chain.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
    """
    return await _memory_call("history", wallet, {"memory_id": memory_id}, timestamp, nonce, signature)


@mcp.tool()
async def memory_delete(
    wallet: str,
    memory_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    scope: str = "chain",
    reason: str | None = None,
) -> dict[str, Any]:
    """PERMANENTLY delete a memory (hard delete; right-to-be-forgotten). The
    content is not recoverable; only an audit row (no content) is kept. The
    default scope "chain" removes EVERY version of the memory; "version"
    removes only this one. Call memory_prepare with operation "delete" and
    the same arguments first.

    Args:
        wallet: Your agent's 0x wallet address.
        memory_id: UUID of any version in the chain.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
        scope: "chain" (all versions, default) or "version" (just this row).
        reason: Optional note recorded in the audit log (max 200 chars).
    """
    return await _memory_call(
        "delete", wallet, {"memory_id": memory_id, "scope": scope, "reason": reason}, timestamp, nonce, signature
    )


@mcp.tool()
async def memory_deletions(wallet: str, timestamp: str, nonce: str, signature: str) -> dict[str, Any]:
    """Your deletion audit log (ids, times, reasons; never content). Call
    memory_prepare with operation "deletions" first.

    Args:
        wallet: Your agent's 0x wallet address.
        timestamp: From memory_prepare.
        nonce: From memory_prepare (single use).
        signature: Your wallet's signature over memory_prepare's message_to_sign.
    """
    return await _memory_call("deletions", wallet, {}, timestamp, nonce, signature)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
