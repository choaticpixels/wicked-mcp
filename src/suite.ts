// Hand-written tools for the Wicked trust/memory/payments services whose HTTP
// surface isn't the `/v1/*` OpenAPI shape the generic loader in index.ts
// understands: Reputation, Registry, Identity, Sanity, Memory and the x402
// Paywall. Behaviour mirrors the Python wicked-mcp server (tool names, args,
// and the `http_status` / `payment_required` envelope) so agents see the same
// surface whether they use the remote server or this npm package.
//
// Nothing here signs anything or holds a wallet key: Identity, Registry
// registration and Memory take a signature the caller produced themselves.
import { createHash, randomBytes } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

function envUrl(name: string, fallback: string): string {
  return (process.env[name] || fallback).replace(/\/$/, "");
}

const REPUTATION_URL = envUrl("REPUTATION_BASE_URL", "https://stake.wickedapi.com");
const REGISTRY_URL = envUrl("REGISTRY_BASE_URL", "https://registry.wickedapi.com");
const IDENTITY_URL = envUrl("IDENTITY_BASE_URL", "https://verify.wickedapi.com");
const SANITY_URL = envUrl("SANITY_BASE_URL", "https://sanity.wickedapi.com");
const MEMORY_URL = envUrl("MEMORY_BASE_URL", "https://memory.wickedapi.com");
const PAYWALL_URL = envUrl("PAYWALL_BASE_URL", "https://paywall.wickedapi.com");

const REGISTRY_API_KEY = process.env.REGISTRY_API_KEY || "";
const IDENTITY_API_KEY = process.env.IDENTITY_API_KEY || "";
const SANITY_API_KEY = process.env.SANITY_API_KEY || "";
const MEMORY_API_KEY = process.env.MEMORY_API_KEY || "";
const PAYWALL_API_KEY = process.env.PAYWALL_API_KEY || "";

const DEFAULT_TIMEOUT_MS = 20_000;
const SANITY_TIMEOUT_MS = 90_000; // open mode runs a live web search per claim

type Json = Record<string, unknown>;

// x402 v2 services put the payment instructions in a base64 PAYMENT-REQUIRED
// header instead of the JSON body; decode it so a 402 is useful either way.
function extractPaymentRequired(res: Response): unknown | undefined {
  const header = res.headers.get("payment-required");
  if (!header) return undefined;
  try {
    return JSON.parse(Buffer.from(header, "base64").toString("utf8"));
  } catch {
    return undefined;
  }
}

async function finalize(res: Response): Promise<Json> {
  const text = await res.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = { raw_body: text };
  }
  const body: Json = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Json) : { data: parsed };
  body.http_status = res.status;
  const pr = extractPaymentRequired(res);
  if (pr !== undefined) body.payment_required = pr;
  const retryAfter = res.headers.get("retry-after");
  if (res.status === 429 && retryAfter) body.retry_after = retryAfter;
  return body;
}

function asResult(body: Json) {
  const status = Number(body.http_status ?? 200);
  // 4xx/5xx are returned (not thrown) because 402/404/429 bodies are data the
  // calling agent needs; flag them as errors without hiding the payload.
  return { isError: status >= 500, content: [{ type: "text" as const, text: JSON.stringify(body, null, 2) }] };
}

function clean(obj: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null));
}

interface ReqOpts {
  headers?: Record<string, string>;
  query?: Record<string, unknown>;
  json?: Record<string, unknown>;
  timeoutMs?: number;
}

async function call(method: string, base: string, path: string, opts: ReqOpts = {}): Promise<Json> {
  const url = new URL(base + path);
  for (const [k, v] of Object.entries(clean(opts.query ?? {}))) url.searchParams.set(k, String(v));
  const headers = { ...(opts.headers ?? {}) };
  const init: RequestInit = { method, headers, signal: AbortSignal.timeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS) };
  if (opts.json) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(clean(opts.json));
  }
  try {
    return await finalize(await fetch(url.toString(), init));
  } catch (err) {
    return { error: (err as Error).message, source: "mcp", http_status: 502 };
  }
}

const keyHeader = (key: string): Record<string, string> => (key ? { "x-api-key": key } : {});
const bearer = (key: string): Record<string, string> => (key ? { authorization: `Bearer ${key}` } : {});
const addr = (wallet: string) => encodeURIComponent(wallet.toLowerCase());

// --- Wicked Memory request signing --------------------------------------
// Every /memories* call carries an EIP-191 signature over a message binding
// the wallet to the exact method, path, body, timestamp and nonce. The path
// and body bytes built here are identical for memory_prepare and the call
// itself, so pass the same arguments to both.

const MEMORY_OPS = ["store", "search", "get", "update", "history", "delete", "deletions"] as const;
type MemoryOp = (typeof MEMORY_OPS)[number];
const EMPTY_SHA256 = createHash("sha256").update("").digest("hex");
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function memoryId(p: Record<string, unknown>): string {
  const id = String(p.memory_id ?? "");
  if (!UUID_RE.test(id)) throw new Error("memory_id must be a UUID");
  return id.toLowerCase();
}

// Matches Python's urlencode(quote_via=quote): the signed path must be
// byte-identical to the one the Python server signs, and encodeURIComponent
// differs on ! ' ( ) *.
function pyQuote(s: string): string {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

function queryString(q: Record<string, string>): string {
  return Object.entries(q)
    .map(([k, v]) => `${pyQuote(k)}=${pyQuote(v)}`)
    .join("&");
}

function memoryBuild(operation: string, params?: Record<string, unknown>): { method: string; path: string; body?: string } {
  const p = clean(params ?? {});
  if (!(MEMORY_OPS as readonly string[]).includes(operation)) {
    throw new Error(`unknown operation '${operation}'; expected one of ${[...MEMORY_OPS].sort().join(", ")}`);
  }
  const body = (keys: string[]) => JSON.stringify(Object.fromEntries(keys.filter((k) => k in p).map((k) => [k, p[k]])));
  switch (operation as MemoryOp) {
    case "store":
      if (!("content" in p)) throw new Error("store requires content");
      return { method: "POST", path: "/memories", body: body(["content", "tags", "metadata", "source"]) };
    case "search": {
      if (!("q" in p)) throw new Error("search requires q");
      const q: Record<string, string> = { q: String(p.q) };
      for (const k of ["limit", "created_after", "created_before", "as_of"]) if (k in p) q[k] = String(p[k]);
      if ("tags" in p) q.tags = Array.isArray(p.tags) ? p.tags.join(",") : String(p.tags);
      if ("include_superseded" in p) q.include_superseded = p.include_superseded ? "true" : "false";
      return { method: "GET", path: `/memories/search?${queryString(q)}` };
    }
    case "get":
      return { method: "GET", path: `/memories/${memoryId(p)}` };
    case "update":
      return { method: "PATCH", path: `/memories/${memoryId(p)}`, body: body(["content", "tags", "metadata", "source"]) };
    case "history":
      return { method: "GET", path: `/memories/${memoryId(p)}/history` };
    case "delete": {
      // scope is always explicit so prepare (may omit it) and delete (default "chain") sign the same path.
      const q: Record<string, string> = { scope: String(p.scope ?? "chain") };
      if ("reason" in p) q.reason = String(p.reason);
      return { method: "DELETE", path: `/memories/${memoryId(p)}?${queryString(q)}` };
    }
    case "deletions":
      return { method: "GET", path: "/memory-deletions" };
  }
}

function memoryMessage(wallet: string, method: string, path: string, timestamp: string, nonce: string, body?: string): string {
  const sha = body ? createHash("sha256").update(body, "utf8").digest("hex") : EMPTY_SHA256;
  return [
    "Wicked Memory auth",
    `wallet: ${wallet.toLowerCase()}`,
    `method: ${method.toUpperCase()}`,
    `path: ${path}`,
    `timestamp: ${timestamp}`,
    `nonce: ${nonce}`,
    `body-sha256: ${sha}`,
  ].join("\n");
}

async function memoryCall(
  operation: MemoryOp,
  wallet: string,
  params: Record<string, unknown>,
  sig: { timestamp: string; nonce: string; signature: string },
  paymentSignature?: string
): Promise<Json> {
  let built;
  try {
    built = memoryBuild(operation, params);
  } catch (err) {
    return { error: (err as Error).message, source: "mcp", http_status: 400 };
  }
  const headers: Record<string, string> = {
    "x-wallet-address": wallet,
    "x-wallet-timestamp": sig.timestamp,
    "x-wallet-nonce": sig.nonce,
    "x-wallet-signature": sig.signature,
  };
  if (built.body !== undefined) headers["content-type"] = "application/json";
  if (operation === "search") {
    if (MEMORY_API_KEY) headers["x-api-key"] = MEMORY_API_KEY;
    if (paymentSignature) headers["PAYMENT-SIGNATURE"] = paymentSignature;
  }
  try {
    const res = await fetch(MEMORY_URL + built.path, {
      method: built.method,
      headers,
      body: built.body,
      signal: AbortSignal.timeout(30_000),
    });
    return await finalize(res);
  } catch (err) {
    return { error: (err as Error).message, source: "mcp", http_status: 502 };
  }
}

// --- Registration ---------------------------------------------------------

const sigShape = {
  timestamp: z.string().describe("From memory_prepare."),
  nonce: z.string().describe("From memory_prepare (single use)."),
  signature: z.string().describe("Your wallet's signature over memory_prepare's message_to_sign."),
};

export function registerSuiteTools(server: McpServer): number {
  let n = 0;
  const tool = (name: string, description: string, shape: Record<string, z.ZodTypeAny>, run: (a: any) => Promise<Json>) => {
    server.registerTool(name, { description, inputSchema: shape }, async (args: any) => asResult(await run(args)));
    n++;
  };
  const wallet = z.string().describe("The 0x wallet address.");

  // ---- Wicked Reputation (all public, free) ----
  tool(
    "reputation_status",
    "[Wicked Reputation] An agent's current reputation: tier, active/unbonding stake, score, slash count, founding-member badge. http_status 404 means the wallet never registered — treat as tier 'unranked'.",
    { wallet },
    ({ wallet }) => call("GET", REPUTATION_URL, `/agents/${addr(wallet)}/status`)
  );
  tool(
    "reputation_statement",
    "[Wicked Reputation] An agent's full position plus real event ledger (stakes, unstakes, withdrawals, slashes). Does not include WickedAPI call volume or fees — see the `note` field.",
    { wallet },
    ({ wallet }) => call("GET", REPUTATION_URL, `/agents/${addr(wallet)}/statement`)
  );
  tool(
    "reputation_query",
    "[Wicked Reputation] Search/rank registered agents by tier, stake, reputation or slash history. Every filter is a typed query param.",
    {
      tier: z.string().optional().describe('Exact tier name, e.g. "gold".'),
      min_stake: z.coerce.number().optional().describe("Minimum active stake (USDC)."),
      max_stake: z.coerce.number().optional().describe("Maximum active stake (USDC)."),
      min_reputation: z.coerce.number().optional().describe("Minimum reputation score."),
      founding_member: z.coerce.boolean().optional().describe("True to only show founding members."),
      min_slash_count: z.coerce.number().optional().describe("Minimum slash events (1 finds agents ever slashed)."),
      sort: z.string().optional().describe('stake_amount | reputation_score | slash_count | registered_at, prefixed "-" (desc, default) or "+" (asc).'),
      limit: z.coerce.number().optional().describe("1-200, default 20."),
    },
    (a) => call("GET", REPUTATION_URL, "/agents/query", { query: { sort: "-stake_amount", limit: 20, ...clean(a) } })
  );
  tool(
    "reputation_tiers",
    "[Wicked Reputation] Live tier thresholds and benefits (min stake, rate-limit multiplier, fee discount). Read at call time — thresholds are admin-configurable.",
    {},
    () => call("GET", REPUTATION_URL, "/tiers")
  );
  tool(
    "reputation_transparency",
    "[Wicked Reputation] Live transparency numbers: treasury address, custody model, unbonding cooldown, total stake, agents, slash events, founding-slot counts.",
    {},
    () => call("GET", REPUTATION_URL, "/transparency")
  );

  // ---- Wicked Registry ----
  tool(
    "registry_search_tools",
    "[Wicked Registry] Search x402/MCP tools by real reliability data (uptime, latency, schema conformance). Works via x402 (402 carries payment instructions) or a free API key (pass api_key, or set REGISTRY_API_KEY). No key yet? Call registry_onboarding_message then registry_create_api_key to get one yourself. For a quick free check of a known tool, use registry_lookup.",
    {
      api_key: z.string().optional().describe("Free Wicked Registry API key (wr_...). Overrides REGISTRY_API_KEY."),
      category: z.string().optional().describe('e.g. "data", "crypto".'),
      protocol: z.string().optional().describe('"x402", "mcp" or "rest".'),
      min_score: z.coerce.number().optional().describe("Minimum composite score 0-100; unchecked tools are excluded once set."),
      sort: z.string().optional().describe('"score" (default), "latency" or "recency".'),
      limit: z.coerce.number().optional().describe("1-200, default 50."),
    },
    ({ api_key, ...a }) =>
      call("GET", REGISTRY_URL, "/tools", { headers: keyHeader(api_key || REGISTRY_API_KEY), query: { sort: "score", limit: 50, ...clean(a) } })
  );
  tool(
    "registry_tool_detail",
    "[Wicked Registry] One tool's full score breakdown, component history and recent real health checks. x402 or a free API key.",
    {
      tool_id: z.string().describe("Registry id from registry_lookup / registry_search_tools / registry_featured_tools."),
      api_key: z.string().optional().describe("Free Wicked Registry API key (wr_...). Overrides REGISTRY_API_KEY."),
    },
    ({ tool_id, api_key }) =>
      call("GET", REGISTRY_URL, `/tools/${encodeURIComponent(tool_id)}`, { headers: keyHeader(api_key || REGISTRY_API_KEY) })
  );
  tool(
    "registry_lookup",
    "[Wicked Registry] Free lookup: find a tool's id and headline reliability score by name or URL fragment (q), exact endpoint URL (endpoint) or category. No key or payment. Use it to check a tool before your agent pays it; follow with registry_tool_badge or registry_tool_detail.",
    {
      q: z.string().optional().describe("Name or URL fragment, at least 2 characters."),
      endpoint: z.string().optional().describe("Exact endpoint URL of the tool you are about to call."),
      category: z.string().optional(),
      limit: z.coerce.number().optional().describe("1-25, default 10."),
    },
    (a) => call("GET", REGISTRY_URL, "/lookup", { query: clean(a) })
  );
  tool(
    "registry_onboarding_message",
    "[Wicked Registry] Step 1 of self-onboarding. Returns the exact message YOU must sign (EIP-191 personal_sign) plus its timestamp, valid for about an hour. action=api_key (needs wallet) for a free API key, or action=register (needs name and endpoint_url) to list your own tool. This tool never signs anything.",
    {
      action: z.enum(["api_key", "register"]),
      wallet: z.string().optional().describe("Your 0x wallet address (required for api_key)."),
      name: z.string().optional().describe("Tool name (required for register)."),
      endpoint_url: z.string().optional().describe("Tool endpoint URL (required for register)."),
    },
    (a) => call("GET", REGISTRY_URL, "/onboard", { query: { action: a.action, wallet: a.wallet, name: a.name, endpointUrl: a.endpoint_url } })
  );
  tool(
    "registry_create_api_key",
    "[Wicked Registry] Step 2 of getting a free API key. Submit the wallet, the timestamp and YOUR signature over the message from registry_onboarding_message(action=api_key). The key (wr_...) is returned once - store it and pass it as api_key to registry_search_tools / registry_tool_detail. Up to 3 active keys per wallet; past the daily cap calls fall back to x402.",
    {
      wallet: z.string().describe("0x wallet that signed the message."),
      timestamp: z.string().describe("The timestamp returned with the message."),
      signature: z.string().describe("0x-prefixed ECDSA signature over the message."),
      label: z.string().optional().describe("Optional label for the key."),
    },
    (a) => call("POST", REGISTRY_URL, "/keys", { json: { wallet: a.wallet, timestamp: a.timestamp, signature: a.signature, label: a.label } })
  );
  tool(
    "registry_featured_tools",
    "[Wicked Registry] Top scored active tools. Public and free.",
    {},
    () => call("GET", REGISTRY_URL, "/featured-tools")
  );
  tool(
    "registry_tool_badge",
    "[Wicked Registry] A tool's current composite reliability score. Public and free.",
    { tool_id: z.string().describe("The tool's Wicked Registry id.") },
    ({ tool_id }) => call("GET", REGISTRY_URL, `/tools/${encodeURIComponent(tool_id)}/badge`)
  );
  tool(
    "registry_report_tool",
    "[Wicked Registry] File a complaint about a tool's reliability or behaviour. Public, rate-limited; reviewed manually, never auto-suspends.",
    {
      tool_id: z.string().describe("The tool's Wicked Registry id."),
      reporter: z.string().describe("Your wallet address or another best-effort identifier."),
      reason: z.string().describe('"downtime", "incorrect_response", "scam_or_abuse", "schema_violation" or "other".'),
      evidence: z.string().optional().describe("Optional note or URL."),
    },
    ({ tool_id, reporter, reason, evidence }) =>
      call("POST", REGISTRY_URL, `/tools/${encodeURIComponent(tool_id)}/report`, { json: { reporter, reason, evidence } })
  );
  tool(
    "registry_register_tool",
    "[Wicked Registry] Register a tool you own so it gets real synthetic checks and a live score. Get the exact message to sign from registry_onboarding_message(action=register), sign it with owner_wallet, then call this. This tool never signs.",
    {
      name: z.string(),
      endpoint_url: z.string().describe("Live, publicly reachable endpoint."),
      protocol: z.string().describe('"x402", "mcp" or "rest".'),
      category: z.string(),
      description: z.string(),
      owner_wallet: z.string().describe("0x wallet that signed the registration message."),
      signature: z.string().describe("0x-prefixed ECDSA signature over the registration message."),
      timestamp: z.string().describe("ISO 8601 timestamp used in the signed message (within an hour of the call)."),
      schema_url: z.string().optional().describe("Optional JSON Schema URL enabling the schema-conformance score."),
    },
    (a) =>
      call("POST", REGISTRY_URL, "/tools/register", {
        json: {
          name: a.name,
          endpointUrl: a.endpoint_url,
          protocol: a.protocol,
          category: a.category,
          description: a.description,
          ownerWallet: a.owner_wallet,
          signature: a.signature,
          timestamp: a.timestamp,
          schemaUrl: a.schema_url,
        },
      })
  );

  // ---- Wicked Identity (reverse-CAPTCHA / Know-Your-Agent) ----
  const idSig = {
    wallet: z.string().describe("Your agent's 0x wallet address on Base."),
    nonce: z.string().describe("Nonce from a FRESH identity_get_nonce call."),
    signature: z.string().describe("0x-prefixed ECDSA signature over that nonce's message."),
  };
  tool(
    "identity_get_nonce",
    "[Wicked Identity] Step 1: a one-time nonce + message to sign. Every signed identity call needs a fresh nonce (single-use, short TTL).",
    { wallet: idSig.wallet },
    ({ wallet }) => call("GET", IDENTITY_URL, `/agents/${addr(wallet)}/nonce`)
  );
  tool(
    "identity_register",
    "[Wicked Identity] Register your wallet (no stake required). Needs your signature over identity_get_nonce's message; this tool never signs.",
    idSig,
    ({ wallet, nonce, signature }) => call("POST", IDENTITY_URL, "/agents/register", { json: { wallet_address: wallet, nonce, signature } })
  );
  tool(
    "identity_get_challenge",
    "[Wicked Identity] Request a real time-boxed liveness challenge for a registered wallet (constrained-generation task, ~12s). Answer via identity_submit_response before `expires_at`. Needs a fresh signed nonce.",
    idSig,
    ({ wallet, nonce, signature }) => call("POST", IDENTITY_URL, "/verify/challenge", { json: { wallet_address: wallet, nonce, signature } })
  );
  tool(
    "identity_submit_response",
    "[Wicked Identity] Submit your answer to a challenge; scored pass / fail / inconclusive. A pass returns a short-lived signed assertion_token (JWT) verifiable via identity_jwks. Needs a DIFFERENT fresh nonce than identity_get_challenge used.",
    {
      ...idSig,
      challenge_id: z.string().describe("The challenge_id from identity_get_challenge."),
      response_text: z.string().describe("Your answer exactly as the instructions specify."),
    },
    ({ wallet, nonce, signature, challenge_id, response_text }) =>
      call("POST", IDENTITY_URL, "/verify/response", { json: { wallet_address: wallet, nonce, signature, challenge_id, response_text } })
  );
  tool(
    "identity_status",
    "[Wicked Identity] Does a wallet currently hold a valid, unexpired assertion? No signature needed. x402 or IDENTITY_API_KEY.",
    { wallet },
    ({ wallet }) => call("GET", IDENTITY_URL, `/verify/status/${addr(wallet)}`, { headers: keyHeader(IDENTITY_API_KEY) })
  );
  tool(
    "identity_jwks",
    "[Wicked Identity] Public RS256 keys to verify an assertion_token locally. Free.",
    {},
    () => call("GET", IDENTITY_URL, "/.well-known/jwks.json")
  );

  // ---- Wicked Sanity (hallucination / eval check) ----
  const contextShape = z
    .union([z.string(), z.array(z.string())])
    .optional()
    .describe('Source text the claim(s) must be faithful to. Required for mode "grounded".');
  const modeShape = z.string().optional().describe('"grounded" (default; check against context) or "open" (check against live web evidence — consistency with the web, not objective truth).');
  tool(
    "sanity_check",
    '[Wicked Sanity] Verify one claim before acting on it. Verdicts: supported | contradicted | unsupported | insufficient_evidence (see `data`). confidence_score is raw consistency (a "contradicted" verdict scores near 0). Policy: act on supported; treat unsupported/insufficient_evidence as unverified; reject contradicted. x402 or SANITY_API_KEY; 429 means a rate limit — see retry_after.',
    { claim: z.string().describe("The single statement to verify (max 5,000 chars)."), context: contextShape, mode: modeShape },
    ({ claim, context, mode }) =>
      call("POST", SANITY_URL, "/check", { headers: keyHeader(SANITY_API_KEY), json: { claim, context, mode: mode ?? "grounded" }, timeoutMs: SANITY_TIMEOUT_MS })
  );
  tool(
    "sanity_check_batch",
    "[Wicked Sanity] Verify up to 50 claims against one shared context in a single call — prefer this over one sanity_check per sentence. `data` is a list in the same order as `claims`.",
    { claims: z.array(z.string()).min(1).max(50).describe("1-50 statements, each up to 5,000 chars."), context: contextShape, mode: modeShape },
    ({ claims, context, mode }) =>
      call("POST", SANITY_URL, "/check/batch", { headers: keyHeader(SANITY_API_KEY), json: { claims, context, mode: mode ?? "grounded" }, timeoutMs: SANITY_TIMEOUT_MS })
  );

  // ---- Wicked Memory (wallet-scoped, signature per call) ----
  tool(
    "memory_prepare",
    'Wicked Memory step 1 of every call: returns the exact `message_to_sign`. Sign it with your own wallet (EIP-191 personal_sign), then call the matching memory_* tool with the SAME arguments plus the returned timestamp, nonce and your signature. Timestamp must be within 5 minutes; nonce is single-use, so prepare again for every call.',
    {
      operation: z.string().describe('"store", "search", "get", "update", "history", "delete" or "deletions".'),
      wallet: z.string().describe("Your agent's 0x wallet address (memories are scoped to it)."),
      params: z.record(z.string(), z.unknown()).optional().describe('The arguments you will pass to the tool, by name, e.g. {"content":"...","tags":["a"]} or {"memory_id":"<uuid>"}.'),
    },
    async ({ operation, wallet, params }) => {
      let built;
      try {
        built = memoryBuild(operation, params);
      } catch (err) {
        return { error: (err as Error).message, source: "mcp", http_status: 400 };
      }
      const timestamp = String(Math.floor(Date.now() / 1000));
      const nonce = randomBytes(16).toString("hex");
      return {
        message_to_sign: memoryMessage(wallet, built.method, built.path, timestamp, nonce, built.body),
        timestamp,
        nonce,
        operation,
        wallet,
        request: { method: built.method, path: built.path },
        next: `Sign message_to_sign with the wallet's key (EIP-191 personal_sign), then call memory_${operation} with the same arguments plus timestamp, nonce and signature.`,
      };
    }
  );
  const memWallet = z.string().describe("Your agent's 0x wallet address.");
  const memId = z.string().describe("The memory's UUID.");
  tool(
    "memory_store",
    "[Wicked Memory] Store a memory (free, fair-use rate limited). A real embedding is computed at write time. Run memory_prepare with operation 'store' and these same arguments first.",
    {
      wallet: memWallet,
      content: z.string().describe("Text to remember (max 16 KB)."),
      ...sigShape,
      tags: z.array(z.string()).optional().describe("Up to 20 short tags."),
      metadata: z.record(z.string(), z.unknown()).optional().describe("Free-form JSON object (max 8 KB)."),
      source: z.string().optional().describe("Which agent/session wrote this."),
    },
    (a) => memoryCall("store", a.wallet, { content: a.content, tags: a.tags, metadata: a.metadata, source: a.source }, a)
  );
  tool(
    "memory_search",
    "[Wicked Memory] Semantic search over YOUR memories only, ranked by real cosine similarity. The one metered call: free with MEMORY_API_KEY, otherwise http_status 402 with x402 v2 instructions under `payment_required` (0.001 USDC on Base) — pay, then call again with the SAME arguments (a 402 does not consume the nonce) plus payment_signature.",
    {
      wallet: memWallet,
      q: z.string().describe("What to look for, in natural language."),
      ...sigShape,
      limit: z.coerce.number().optional().describe("1-50, default 10."),
      tags: z.array(z.string()).optional().describe("Only memories having ALL of these tags."),
      created_after: z.string().optional().describe("ISO-8601 lower bound."),
      created_before: z.string().optional().describe("ISO-8601 upper bound."),
      as_of: z.string().optional().describe("ISO-8601 instant; search memory as it stood then."),
      include_superseded: z.coerce.boolean().optional().describe("Also search old, superseded versions."),
      payment_signature: z.string().optional().describe("x402 payment payload (base64) for the keyless path."),
    },
    (a) =>
      memoryCall(
        "search",
        a.wallet,
        { q: a.q, limit: a.limit, tags: a.tags, created_after: a.created_after, created_before: a.created_before, as_of: a.as_of, include_superseded: a.include_superseded },
        a,
        a.payment_signature
      )
  );
  tool(
    "memory_get",
    "[Wicked Memory] Fetch one of your memories by id (another wallet's or a deleted id returns the same 404).",
    { wallet: memWallet, memory_id: memId, ...sigShape },
    (a) => memoryCall("get", a.wallet, { memory_id: a.memory_id }, a)
  );
  tool(
    "memory_update",
    "[Wicked Memory] Update without overwriting: creates a new version and closes the old one (valid_until / superseded_by). Omitted fields carry over; only the current version can be updated (409 otherwise).",
    {
      wallet: memWallet,
      memory_id: z.string().describe("UUID of the CURRENT version to supersede."),
      ...sigShape,
      content: z.string().optional().describe("New text (re-embedded). Provide at least one of content/tags/metadata/source."),
      tags: z.array(z.string()).optional().describe("Replacement tags."),
      metadata: z.record(z.string(), z.unknown()).optional().describe("Replacement metadata object."),
      source: z.string().optional().describe("New source label."),
    },
    (a) => memoryCall("update", a.wallet, { memory_id: a.memory_id, content: a.content, tags: a.tags, metadata: a.metadata, source: a.source }, a)
  );
  tool(
    "memory_history",
    "[Wicked Memory] Full version chain for a memory, oldest first, with valid_from / valid_until / superseded_by. Works from any version's id.",
    { wallet: memWallet, memory_id: z.string().describe("UUID of any version in the chain."), ...sigShape },
    (a) => memoryCall("history", a.wallet, { memory_id: a.memory_id }, a)
  );
  tool(
    "memory_delete",
    '[Wicked Memory] PERMANENTLY hard-delete a memory (right-to-be-forgotten). Default scope "chain" removes every version; "version" only this one. Only an audit row (no content) is kept.',
    {
      wallet: memWallet,
      memory_id: z.string().describe("UUID of any version in the chain."),
      ...sigShape,
      scope: z.string().optional().describe('"chain" (all versions, default) or "version".'),
      reason: z.string().optional().describe("Optional audit-log note (max 200 chars)."),
    },
    (a) => memoryCall("delete", a.wallet, { memory_id: a.memory_id, scope: a.scope ?? "chain", reason: a.reason }, a)
  );
  tool(
    "memory_deletions",
    "[Wicked Memory] Your deletion audit log (ids, times, reasons; never content).",
    { wallet: memWallet, ...sigShape },
    (a) => memoryCall("deletions", a.wallet, {}, a)
  );

  // ---- x402 Paywall (tenant API; needs PAYWALL_API_KEY except signup) ----
  const routeShape = {
    price: z.string().describe('Decimal USD amount, e.g. "0.01" or "$0.01".'),
    network: z.string().describe("CAIP-2 id: Base mainnet eip155:8453, Base Sepolia eip155:84532."),
    payTo: z.string().describe("Your payout wallet (0x…). Settlement pays here directly; nothing is custodied."),
    method: z.enum(["GET", "POST"]).optional().describe("Default GET."),
    path: z.string().optional().describe('Route path, e.g. "/weather".'),
    asset: z.string().optional().describe("Payment token contract; defaults to USDC on the network."),
    description: z.string().optional().describe("Up to 200 chars: letters, digits, space . _ -"),
    upstreamUrl: z.string().optional().describe("Your https backend. Omit for the demo confirmation payload."),
  };
  tool(
    "paywall_signup",
    "[x402 Paywall] Create a tenant account. Returns a Bearer apiKey ONCE — store it and set PAYWALL_API_KEY. Rate-limited to 5/hour per IP.",
    {
      name: z.string().describe("Letters, digits, space . _ - (max 100)."),
      slug: z.string().describe("Lowercase a-z0-9 and hyphens, 2-40 chars; becomes your mount path prefix /{slug}."),
    },
    ({ name, slug }) => call("POST", PAYWALL_URL, "/signup", { json: { name, slug } })
  );
  tool("paywall_get_tenant", "[x402 Paywall] Your tenant record (needs PAYWALL_API_KEY).", {}, () =>
    call("GET", PAYWALL_URL, "/tenants/me", { headers: bearer(PAYWALL_API_KEY) })
  );
  tool("paywall_list_routes", "[x402 Paywall] List your paywalled routes (needs PAYWALL_API_KEY).", {}, () =>
    call("GET", PAYWALL_URL, "/tenants/me/routes", { headers: bearer(PAYWALL_API_KEY) })
  );
  tool(
    "paywall_create_route",
    "[x402 Paywall] Paywall a new endpoint with x402 pay-per-call, settled in USDC straight to payTo. With upstreamUrl, paid requests are proxied to your backend. Applies live within ~20s (needs PAYWALL_API_KEY).",
    routeShape,
    (a) => call("POST", PAYWALL_URL, "/tenants/me/routes", { headers: bearer(PAYWALL_API_KEY), json: a })
  );
  tool(
    "paywall_import_routes",
    "[x402 Paywall] Bulk upsert routes keyed on (method, path) — paywall a whole API in one call (needs PAYWALL_API_KEY).",
    { routes: z.array(z.object(routeShape)).min(1).describe("Route objects, same fields as paywall_create_route.") },
    ({ routes }) => call("POST", PAYWALL_URL, "/tenants/me/routes/import", { headers: bearer(PAYWALL_API_KEY), json: { routes } })
  );
  tool(
    "paywall_update_route",
    "[x402 Paywall] Partial update of a route (e.g. change price or payout wallet); applies live within ~20s (needs PAYWALL_API_KEY).",
    {
      id: z.string().describe("Route id from paywall_list_routes."),
      price: routeShape.price.optional(),
      network: routeShape.network.optional(),
      payTo: routeShape.payTo.optional(),
      method: routeShape.method,
      path: routeShape.path,
      asset: routeShape.asset,
      description: routeShape.description,
      upstreamUrl: routeShape.upstreamUrl,
    },
    ({ id, ...patch }) => call("PATCH", PAYWALL_URL, `/tenants/me/routes/${encodeURIComponent(id)}`, { headers: bearer(PAYWALL_API_KEY), json: patch })
  );
  tool(
    "paywall_delete_route",
    "[x402 Paywall] Remove a paywalled route; applies within ~20s (needs PAYWALL_API_KEY).",
    { id: z.string().describe("Route id from paywall_list_routes.") },
    ({ id }) => call("DELETE", PAYWALL_URL, `/tenants/me/routes/${encodeURIComponent(id)}`, { headers: bearer(PAYWALL_API_KEY) })
  );
  tool(
    "paywall_list_settlements",
    "[x402 Paywall] Your real on-chain USDC settlements (earnings) (needs PAYWALL_API_KEY).",
    { limit: z.coerce.number().optional().describe("Default 50, max 200.") },
    ({ limit }) => call("GET", PAYWALL_URL, "/tenants/me/settlements", { headers: bearer(PAYWALL_API_KEY), query: { limit } })
  );

  return n;
}

// Exposed for tests: deterministic builders used by memory_prepare.
export const __test = { memoryBuild, memoryMessage };
