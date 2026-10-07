# WickedAPI MCP Server

MCP server for the whole [Wicked](https://wickedapi.com) suite — trading data, DeFi protocol health, token safety, brokerage sync, agent identity, reputation, a tool-reliability registry, hallucination checks, persistent agent memory, and x402 paywalls, in one server.

**73 tools** across 10 live services. The first four are generated automatically from each service's own live `openapi.json`, so the list always matches what's deployed; the rest are hand-written because they use wallet signatures and non-`/v1` routes:

| Service | Tools | Auth |
| --- | --- | --- |
| [Trading Data API](https://api.wickedapi.com) | 22 (unprefixed: `price`, `ohlcv`, `fvg`, `momentum_score`, `sentiment`, `macro_calendar`, …) | API key or x402, 5 free |
| [Protocol Health Oracle](https://protocol-health-oracle.wickedapi.com) | 6 (`protocol_health_*`) | API key or x402, scoreboard free |
| [Token Risk Oracle](https://token-risk-oracle.wickedapi.com) | 1 (`token_risk`) | 100% free, no key |
| [Broker Sync API](https://broker-sync-api.wickedapi.com) | 6 (`broker_sync_*`) | API key or x402 |
| [Wicked Reputation](https://stake.wickedapi.com) | 5 (`reputation_*`) | free, no key |
| [Wicked Registry](https://registry.wickedapi.com) | 6 (`registry_*`) | search/detail: key or x402; featured/badge/report free; register needs your signature |
| [Wicked Identity](https://verify.wickedapi.com) | 8 (`identity_*`) | no wallet? start with `identity_sandbox_register` (no signing); free test key via `identity_get_test_key`; real-wallet calls need your signature over a nonce |
| [Wicked Sanity](https://sanity.wickedapi.com) | 2 (`sanity_check`, `sanity_check_batch`) | key or x402 |
| [Wicked Memory](https://memory.wickedapi.com) | 8 (`memory_prepare`, `memory_store`, `memory_search`, …) | wallet signature on every call; only search is metered |
| [x402 Paywall](https://paywall.wickedapi.com) | 8 (`paywall_*`) | `paywall_signup` is open; the rest need the tenant key |

Plus **6 resources** (the platform directory, `llms.txt`, and each service's live endpoint catalog) and **7 prompts** (`rug_check`, `vet_dependency`, `morning_briefing`, `fact_check`, `verify_agent`, `find_reliable_tool`, `agent_memory_guide`) that chain tools together the way the [cookbook](../cookbooks/wickedapi-cookbook.md) does.

These tools never sign anything or hold a key. Agents with no wallet can still try Wicked Identity end to end through a sandbox identity (`identity_sandbox_register`, then pass `sandbox_token` instead of a signature); sandbox results are for testing and are never reported as verified. Identity (real wallet), Registry registration and Memory take a signature you produce with your own wallet (`identity_get_nonce` / `memory_prepare` return the exact message to sign). Calls that need x402 payment return the real `402` challenge (decoded under `payment_required` for x402 v2) rather than auto-paying — `WICKEDAPI_PAYER_PRIVATE_KEY` auto-pay applies to the Trading, Protocol Health, Token Risk and Broker Sync tools.

## Remote endpoint (no install)

The same 73 tools are served over streamable HTTP at **`https://mcp.wickedapi.com/mcp`** (stateless, rate-limited per IP; the public deployment carries no personal keys, so paid calls return the x402 challenge). Point any streamable-HTTP MCP client at it. To self-host, build the `Dockerfile` (or `npm run build && npm run start:http`); see `.env.example` for `RATE_LIMIT_PER_MINUTE`, `ALLOWED_HOSTS` and `TRUSTED_CLIENT_IP_HEADER`.

## No signup required

Every tool hits a real endpoint gated by [x402](https://x402.org) (HTTP-native pay-per-call, USDC on Base) with an API-key fallback — and the Token Risk Oracle tool is free no matter what:

- **Zero config** — calls without credentials still work; paid endpoints return the real `402` payment challenge (price, network, `payTo`) as the tool result.
- **Autonomous pay-per-call** — set `WICKEDAPI_PAYER_PRIVATE_KEY` to a funded Base-mainnet wallet's private key, and every call auto-pays ~$0.001 in USDC and returns real data. No account, no subscription.
- **Partner API key** — set `WICKEDAPI_API_KEY` for free, rate-limited access across all four services (same key works everywhere).

## Install

```json
{
  "mcpServers": {
    "wickedapi": {
      "command": "npx",
      "args": ["-y", "wickedapi-mcp"],
      "env": {
        "WICKEDAPI_API_KEY": "your-key-here"
      }
    }
  }
}
```

Or for autonomous pay-per-call instead of a key:

```json
{
  "mcpServers": {
    "wickedapi": {
      "command": "npx",
      "args": ["-y", "wickedapi-mcp"],
      "env": {
        "WICKEDAPI_PAYER_PRIVATE_KEY": "0xyour-base-wallet-private-key"
      }
    }
  }
}
```

Nothing configured? The Token Risk Oracle tool and the 5 free Trading Data API endpoints (`sentiment`, `macro_calendar`, `market_overview`, `gas`, `kalshi_probability`) still work with zero setup.

## Resources

Read without calling a tool — useful for a client that surfaces resources in its UI:

- `wickedapi://platform/services` — the full service directory (JSON)
- `wickedapi://platform/llms` — the platform's `llms.txt`
- `wickedapi://trading/status`, `wickedapi://protocol_health/status`, `wickedapi://token_risk/status`, `wickedapi://broker_sync/status` — each service's live endpoint catalog

## Quick start

```bash
claude mcp add --transport http wicked https://mcp.wickedapi.com/mcp   # Claude Code, no install
npx -y wickedapi-mcp                                                    # local stdio
```

Claude Desktop / Cursor / VS Code config and per-framework starters: see the [cookbooks](https://github.com/choaticpixels/api_services/tree/main/cookbooks). The remote also serves a landing page at `/`, `/llms.txt`, an MCP server card at `/.well-known/mcp/server-card.json`, and aggregate per-tool call counts at `/stats` (counts only; no arguments or IPs).

## Prompts

One-click workflows that chain real tool calls (Claude Desktop and other prompt-aware clients surface these directly):

- **`rug_check`** (`address`, `chain`, optional `symbol`) — token risk check + market sentiment + optional momentum read, before buying.
- **`vet_dependency`** (`slug`, optional `token_address`, `chain`) — protocol health + optional contract-level risk check, before building on a DeFi protocol.
- **`morning_briefing`** (optional `symbol`) — macro calendar + earnings calendar + sentiment + momentum verdict, as one digest.
- **`fact_check`** (`output`, optional `source`) — split an answer into claims and verify each with Wicked Sanity.
- **`verify_agent`** (`wallet`) — Identity assertion + Reputation stake/tier before you transact with an agent.
- **`find_reliable_tool`** (`need`, optional `category`, `min_score`) — Registry search by real uptime/latency/schema scores.
- **`agent_memory_guide`** (`wallet`, `task`) — the two-step signed Memory flow, explained to the model.

## Environment variables

| Var | Required | Notes |
| --- | --- | --- |
| `WICKEDAPI_API_KEY` | no | Free, rate-limited access across all four services (same key works everywhere). |
| `WICKEDAPI_PAYER_PRIVATE_KEY` | no | Base-mainnet wallet private key. If set, every call auto-pays the x402 challenge in USDC — no key needed. |
| `WICKEDAPI_BASE_URL` | no | Override the Trading Data API base (default `https://api.wickedapi.com`). |
| `WICKEDAPI_PROTOCOL_HEALTH_URL` | no | Override the Protocol Health Oracle base (default `https://protocol-health-oracle.wickedapi.com`). |
| `WICKEDAPI_TOKEN_RISK_URL` | no | Override the Token Risk Oracle base (default `https://token-risk-oracle.wickedapi.com`). |
| `WICKEDAPI_BROKER_SYNC_URL` | no | Override the Broker Sync API base (default `https://broker-sync-api.wickedapi.com`). |
| `REGISTRY_API_KEY` / `IDENTITY_API_KEY` / `SANITY_API_KEY` / `MEMORY_API_KEY` | no | Free-tier keys for the paid calls of Registry, Identity, Sanity and Memory search. Omit to get the x402 challenge instead. |
| `PAYWALL_API_KEY` | for `paywall_*` | Tenant Bearer key returned once by `paywall_signup`. |
| `REPUTATION_BASE_URL` / `REGISTRY_BASE_URL` / `IDENTITY_BASE_URL` / `SANITY_BASE_URL` / `MEMORY_BASE_URL` / `PAYWALL_BASE_URL` | no | Override the matching service base (defaults are the `*.wickedapi.com` hosts). |
| `WICKEDAPI_SHOWCASE_URL` | no | Override the platform directory base used by resources (default `https://wickedapi.com`). |

If neither `WICKEDAPI_API_KEY` nor `WICKEDAPI_PAYER_PRIVATE_KEY` is set, paid calls still succeed at the protocol level — you'll get the real `402` challenge back as the tool result instead of data. If one service's spec fails to load (network hiccup, that service is down), its tools are simply skipped for the session — the rest still register normally.

## Local development

```bash
npm install
npm test            # builds, then unit + end-to-end tests (e2e needs network)
node dist/index.js  # stdio
node dist/http.js   # streamable HTTP on $PORT (default 8080)
```

## What this is not

This server doesn't reimplement any data logic — it's a thin bridge from MCP tool calls to the live Wicked HTTP endpoints across all ten services. All the actual market data, indicators, scoring, and risk signals live in the APIs themselves.
