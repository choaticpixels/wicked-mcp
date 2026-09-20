# Wicked MCP server

An MCP server wrapping the public HTTP surface of [WickedAPI](https://api.wickedapi.com)
(trading data), [Wicked Reputation](https://stake.wickedapi.com) (on-chain
agent staking/reputation), [Wicked Registry](https://registry.wickedapi.com)
(real reliability scores for x402/MCP tools), and [Wicked Identity](https://verify.wickedapi.com)
(reverse-CAPTCHA / Know-Your-Agent verification) — 20 tools, no new backend
logic, just a protocol-native front door onto the same endpoints each
service's own docs show calling with plain `requests`. Live at
**`mcp.wickedapi.com`**; the reputation/staking, registry, and identity
services and cookbooks live in separate (private) repos.

## Tools

**WickedAPI** (works unauthenticated via x402, or with a free-tier key — see below)
- `wickedapi_price(symbol, asset_class?)`
- `wickedapi_momentum(symbol, timeframe?)`
- `wickedapi_funding_oi(symbol)`

**Wicked Reputation** (all public, all free, no key needed)
- `reputation_status(wallet)`
- `reputation_statement(wallet)` — position + full event ledger
- `reputation_query(tier?, min_stake?, ..., sort?, limit?)` — filter/sort agents
- `reputation_tiers()`
- `reputation_transparency()`

**Wicked Registry** (search/detail work unauthenticated via x402, or with a
free-tier key — see below; featured/badge/report are always free)
- `registry_search_tools(category?, protocol?, min_score?, sort?, limit?)`
- `registry_tool_detail(tool_id)` — score breakdown + real check history
- `registry_featured_tools()` — top scored tools, always free
- `registry_tool_badge(tool_id)` — a tool's current score, always free
- `registry_report_tool(tool_id, reporter, reason, evidence?)` — file a complaint, always free
- `registry_register_tool(name, endpoint_url, protocol, category, description, owner_wallet, signature, timestamp, schema_url?)` —
  register a tool you own; you supply a real wallet signature, this tool
  doesn't sign anything itself

**Wicked Identity** (register/challenge/response require a real wallet
signature over a server-issued nonce — these tools never sign anything
themselves; status is public and works unauthenticated via x402 or with a
free-tier key)
- `identity_get_nonce(wallet)` — fetch a fresh one-time nonce before every signed call below
- `identity_register(wallet, nonce, signature)` — lightweight identity record, no stake required
- `identity_get_challenge(wallet, nonce, signature)` — issue a real, time-boxed (12s default) agent-liveness challenge
- `identity_submit_response(wallet, nonce, signature, challenge_id, response_text)` — score it; pass issues a signed assertion token
- `identity_status(wallet)` — does this wallet hold a valid, unexpired assertion? always free
- `identity_jwks()` — public RS256 keys to verify an assertion_token locally, always free

Every tool returns the upstream JSON body plus an `http_status` field.
Non-2xx responses are returned, not raised — a 402 from WickedAPI, Wicked
Registry, or Wicked Identity carries real x402 payment instructions (in the
JSON body for the first two; decoded from the `PAYMENT-REQUIRED` header
into a `payment_required` key for Identity's newer x402 v2 protocol); a 404
from the reputation service just means the wallet has never registered.
All of that is useful data for whatever's calling the tool, not failures to
hide.

## Run it locally (stdio — for Claude Desktop, `mcp dev`, etc.)

```bash
pip install -r requirements.txt
python server.py
```

Add to Claude Desktop's config:

```json
{
  "mcpServers": {
    "wicked": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-server/server.py"],
      "env": { "WICKEDAPI_API_KEY": "your-key-if-you-have-one" }
    }
  }
}
```

No `WICKEDAPI_API_KEY`? WickedAPI tools still work — they fall back to
x402, returning payment instructions instead of data, same as calling the
API directly with no key.

## Remote deployment (streamable HTTP)

`http_app.py` wraps the same server with `mcp.streamable_http_app()` and a
per-IP rate limit (`RATE_LIMIT_PER_MINUTE`, default 30) — the one thing
that changes when this runs as a public endpoint instead of something each
user runs under their own control. Deployed via the `Dockerfile` in this
directory; Railway sets `$PORT`.

```bash
uvicorn http_app:app --host 0.0.0.0 --port 8080
```

Live at **`https://mcp.wickedapi.com/mcp`** — point any streamable-HTTP
MCP client there directly. (`https://wicked-mcp-production.up.railway.app/mcp`
also works — same deployment, Railway-generated domain.)

**No API key is baked into the deployed server.** It authenticates
WickedAPI calls with whatever `WICKEDAPI_API_KEY` is set in its own
environment (optional — omit it and every caller just gets the x402
fallback), so hosting cost doesn't scale with usage. If you want free-tier
WickedAPI access through the remote endpoint, run it locally instead with
your own key — see above.

## Config

| Env var | Default | Notes |
|---|---|---|
| `WICKEDAPI_API_KEY` | _(unset)_ | Optional. Omit to fall back to x402 on every WickedAPI call. |
| `REGISTRY_API_KEY` | _(unset)_ | Optional. Omit to fall back to x402 on every paid Wicked Registry call. |
| `IDENTITY_API_KEY` | _(unset)_ | Optional. Omit to fall back to x402 on the paid `identity_status` call. |
| `WICKEDAPI_BASE_URL` | `https://api.wickedapi.com` | Override for local/staging testing only. |
| `REPUTATION_BASE_URL` | `https://stake.wickedapi.com` | Override for local/staging testing only. |
| `REGISTRY_BASE_URL` | `https://registry.wickedapi.com` | Override for local/staging testing only. |
| `IDENTITY_BASE_URL` | `https://verify.wickedapi.com` | Override for local/staging testing only. |
| `RATE_LIMIT_PER_MINUTE` | `30` | HTTP deployment only (`http_app.py`), per client IP. |
