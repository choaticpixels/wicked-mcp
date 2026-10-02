# Wicked MCP server

An MCP server wrapping the public HTTP surface of [WickedAPI](https://api.wickedapi.com)
(trading data), [Wicked Reputation](https://stake.wickedapi.com) (on-chain
agent staking/reputation), [Wicked Registry](https://registry.wickedapi.com)
(real reliability scores for x402/MCP tools), [Wicked Identity](https://verify.wickedapi.com)
(reverse-CAPTCHA / Know-Your-Agent verification), [Wicked Sanity](https://sanity.wickedapi.com)
(hallucination / eval check), and [Wicked Memory](https://memory.wickedapi.com)
(persistent, wallet-scoped agent memory) — 30 tools, no new backend
logic, just a protocol-native front door onto the same endpoints each
service's own docs show calling with plain `requests`. Live at
**`mcp.wickedapi.com`**; the reputation/staking, registry, identity, and
sanity services and cookbooks live in separate (private) repos.

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

**Wicked Sanity** (hallucination / eval check — works unauthenticated via
x402, or with a free-tier key; every verdict is real model inference run at
request time, never cached or guessed)
- `sanity_check(claim, context?, mode?)` — verify one claim. `mode: "grounded"`
  (default; `context` required) checks it against source text you supply;
  `mode: "open"` checks it against live web evidence. Open mode is
  consistency with current web content, **not objective truth**, and returns
  `insufficient_evidence` when nothing relevant is found. Note
  `confidence_score` is the raw consistency score (a `contradicted` verdict
  scores near 0), not confidence-in-the-verdict
- `sanity_check_batch(claims, context?, mode?)` — up to 50 claims against one
  shared context in a single call; use it for a multi-sentence output rather
  than one `sanity_check` per sentence

**Wicked Memory** (persistent, wallet-scoped agent memory: store, semantic
search, versioned history, hard delete — only search is metered)

Every memory call needs a fresh per-request wallet signature. Like the Identity
tools, this server never signs anything or holds a key: call `memory_prepare`
with the operation and its arguments, sign the `message_to_sign` it returns
(EIP-191 `personal_sign`) with your own wallet, then call the matching tool
with the **same arguments** plus the returned `timestamp`, `nonce` and your
`signature`. The nonce is single-use and the timestamp must be within 5
minutes, so prepare again for every call.
- `memory_prepare(operation, wallet, params?)` — step 1 of every call; returns the message to sign
- `memory_store(wallet, content, …)` — store a memory (free); a real embedding is computed at write time
- `memory_search(wallet, q, …)` — semantic search over **your** memories only, ranked by real cosine
  similarity. The one metered call: free with `MEMORY_API_KEY`, otherwise a `402` with x402 v2 payment
  instructions under `payment_required` (0.001 USDC on Base); pay, then call again with the same
  arguments plus `payment_signature` (a 402 does not consume the nonce). Supports `tags`, time filters,
  `as_of` (memory as it stood at a past instant) and `include_superseded`
- `memory_get`, `memory_history`, `memory_deletions` — read one memory, its full version chain, or your deletion audit log
- `memory_update(wallet, memory_id, …)` — supersedes: creates a new version and closes the old one
  (`valid_until` / `superseded_by`); nothing is overwritten
- `memory_delete(wallet, memory_id, scope?, reason?)` — permanent hard delete; `scope: "chain"` (default)
  removes every version, `"version"` just one. Only an audit row (no content) is kept

Every tool returns the upstream JSON body plus an `http_status` field.
Non-2xx responses are returned, not raised — a 402 from WickedAPI carries
real x402 payment instructions in the JSON body (older x402 v1); a 402 from
Wicked Registry's search/detail tools, Wicked Identity's status tool, or
Wicked Sanity's check tools carries them decoded from the `PAYMENT-REQUIRED` header into a
`payment_required` key instead (newer x402 v2); a 404 from the reputation
service just means the wallet has never registered. All of that is useful
data for whatever's calling the tool, not failures to
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
| `SANITY_API_KEY` | _(unset)_ | Optional. Omit to fall back to x402 on `sanity_check` / `sanity_check_batch`. **Set on the public deployment** to a dedicated *restricted* key, so public callers get real verdicts: Sanity enforces its limits (20 requests/minute and 30 open-mode checks/day, shared by all users; grounded mode is not counted against the daily cap) via its `API_KEY_LIMITS` setting, because every caller shares that one key and open mode spends a live web search per call. Over the limit, the tool returns `http_status` 429 with `Retry-After`. |
| `MEMORY_API_KEY` | _(unset)_ | Optional. Omit to fall back to x402 on `memory_search` (the only metered Memory call). Memory keys are issued by the operator. |
| `MEMORY_BASE_URL` | `https://memory.wickedapi.com` | Override for local/staging testing only (staging: `https://memory-testnet.wickedapi.com`, Base Sepolia). |
| `WICKEDAPI_BASE_URL` | `https://api.wickedapi.com` | Override for local/staging testing only. |
| `REPUTATION_BASE_URL` | `https://stake.wickedapi.com` | Override for local/staging testing only. |
| `REGISTRY_BASE_URL` | `https://registry.wickedapi.com` | Override for local/staging testing only. |
| `IDENTITY_BASE_URL` | `https://verify.wickedapi.com` | Override for local/staging testing only. |
| `SANITY_BASE_URL` | `https://sanity.wickedapi.com` | Override for local/staging testing only (staging: `https://wicked-sanity-staging.up.railway.app`). |
| `RATE_LIMIT_PER_MINUTE` | `30` | HTTP deployment only (`http_app.py`), per real client IP (IPv6 grouped by /64). |
| `TRUSTED_CLIENT_IP_HEADER` | `x-real-ip` | HTTP deployment only. Header carrying the caller's real IP, set by the proxy in front (Railway sets `X-Real-IP`). Behind the proxy the TCP peer is always Railway's own address, so without this every caller would share one bucket. Only safe when the proxy sets the header itself; set empty to use the TCP peer instead (e.g. if run without a proxy). If the domain is ever proxied through Cloudflare, point it at `cf-connecting-ip`. Missing/invalid values fall back to the TCP peer. |
