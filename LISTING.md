# Directory listing copy — wickedapi-mcp

Copy-paste blocks for glama.ai, smithery.ai, and anywhere else that takes a name/tagline/description/tags.

---

## Name

`Wicked MCP` (package: `wickedapi-mcp`, remote: `https://mcp.wickedapi.com/mcp`)

## Tagline (one-liner, ~60 chars)

**A whole API platform for agents — zero setup, no API key.**

Alt taglines:
- Give your agent a Base wallet. It pays for trading data, DeFi risk checks, and brokerage sync itself.
- 70 tools across trading data, DeFi safety, and brokerage sync — before you've made an account.
- The MCP server that never asks you to sign up.

---

## Short description (~200 chars, for cards/previews)

Real-time trading data, DeFi protocol health, token scam/rug-checks, and brokerage sync for agents — 70 tools, 6 resources, 3 workflow prompts. Configure one Base wallet key and it just works. No signup, no dashboard, no subscription.

---

## Long description (full listing body, Markdown)

**WickedAPI turns your agent's wallet into its API key.**

Most APIs make you sign up, generate a key, and manage a subscription before your agent can ask a single question. This one skips all of that: point this MCP server at a funded Base-mainnet wallet (`WICKEDAPI_PAYER_PRIVATE_KEY`) and every priced tool call auto-pays a fraction of a cent in USDC via [x402](https://x402.org) — no account, no dashboard, no monthly bill. Your agent goes from zero to live data in one environment variable.

Don't have a wallet handy? It still works. Call any priced tool with no credentials at all and you'll get back the real x402 payment challenge (price, network, `payTo` address) instead of a dead end — and the token-risk tool is free no matter what.

**70 tools across 10 live services, generated live from each service's own OpenAPI spec** — so this listing never drifts out of sync with what's actually deployed:

- **Trading Data API** (22 tools) — spot price & OHLCV for crypto and US equities/ETFs, Wilder ATR, Fair Value Gaps (standard ICT/LuxAlgo), ICT liquidity levels, perp funding/OI, long/short ratios, order-book depth, spot-vs-perp basis, Hyperliquid, Kalshi prediction markets, DeFi TVL, crypto Fear & Greed, a verified FOMC/CPI/jobs macro calendar, an earnings calendar, Ethereum gas, US Treasury yields, market clock, global crypto stats, and an AI momentum score across 10 liquid assets.
- **Protocol Health Oracle** (6 tools) — live-scored DeFi protocol health across the full DefiLlama universe: GitHub activity, TVL trend, and treasury signals. Vet a dependency before you integrate it.
- **Token Risk Oracle** (1 tool, always free) — real on-chain scam/rug-check signals for any EVM or Solana token contract: honeypot detection, mint authority, holder/LP concentration, trust-list status.
- **Wicked Reputation, Registry, Identity, Sanity, Memory** (27 tools) — on-chain agent reputation, reliability scores for x402/MCP tools, Know-Your-Agent verification, hallucination checks, and persistent wallet-scoped memory. **x402 Paywall** (8 tools) — put any endpoint behind pay-per-call USDC.
- **Broker Sync API** (6 tools) — real positions, balances, and transactions across 30+ brokerages through one normalized schema.

**Plus 6 resources** (the platform directory, `llms.txt`, and each service's live endpoint catalog — readable without a tool call) **and 3 prompts** that chain tools into one-click workflows: `rug_check`, `vet_dependency`, `morning_briefing`.

**No fabricated data, ever.** If an upstream source fails, you get a real error — never a made-up number. That rule is enforced across every endpoint, every service.

**Three ways to authenticate, your choice:**
1. `WICKEDAPI_PAYER_PRIVATE_KEY` — a Base-mainnet wallet. Every call auto-pays via x402. Fully autonomous.
2. `WICKEDAPI_API_KEY` — a partner key, free and rate-limited, works across all four services.
3. Nothing — you'll get the real 402 challenge back so you can see the price before paying, and the token-risk tool works regardless.

Built by [WickedAPI](https://wickedapi.com). Full platform directory at [wickedapi.com](https://wickedapi.com); each service publishes its own docs, OpenAPI spec, and interactive explorer.

---

## Tags / categories

`trading` `crypto` `stocks` `market-data` `defi` `risk` `brokerage` `prediction-markets` `x402` `payments` `agents` `real-time-data` `technical-analysis`

---

## Install snippet (for listings that render it)

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

## Repo / homepage links

- Repo: _(add your public GitHub URL once pushed)_
- Homepage: https://wickedapi.com
- Trading Data API docs: https://api.wickedapi.com/docs
- Protocol Health Oracle docs: https://protocol-health-oracle.wickedapi.com/docs
- Token Risk Oracle docs: https://token-risk-oracle.wickedapi.com/docs
- Broker Sync API docs: https://broker-sync-api.wickedapi.com/docs
