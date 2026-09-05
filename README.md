# Wicked MCP server

An MCP server wrapping the public HTTP surface of [WickedAPI](https://api.wickedapi.com)
(trading data) and [Wicked Reputation](https://stake.wickedapi.com) (on-chain
agent staking/reputation) — 8 tools, no new backend logic, just a
protocol-native front door onto the same endpoints WickedAPI's own
integration cookbooks show calling with plain `requests`. Live at
**`mcp.wickedapi.com`**; the reputation/staking service and cookbooks live
in a separate (private) repo.

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

Every tool returns the upstream JSON body plus an `http_status` field.
Non-2xx responses are returned, not raised — a 402 from WickedAPI carries
real x402 payment instructions in its body; a 404 from the reputation
service just means the wallet has never registered. Both are useful data
for whatever's calling the tool, not failures to hide.

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
| `WICKEDAPI_BASE_URL` | `https://api.wickedapi.com` | Override for local/staging testing only. |
| `REPUTATION_BASE_URL` | `https://stake.wickedapi.com` | Override for local/staging testing only. |
| `RATE_LIMIT_PER_MINUTE` | `30` | HTTP deployment only (`http_app.py`), per client IP. |
