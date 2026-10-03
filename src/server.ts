import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { registerSuiteTools } from "./suite.js";
import { registerSuitePrompts } from "./prompts.js";
import { instrument, type ToolHooks } from "./usage.js";

// --- Service registry -------------------------------------------------
// One entry per live WickedAPI service. "trading" is the original
// api.wickedapi.com service and keeps its historical, unprefixed tool
// names (already published — real users may have these hardcoded).
// Every other service gets its tools prefixed with its id so 40+ tools
// stay legible and collision-free.
interface ServiceDef {
  id: string;
  name: string;
  baseUrl: string;
}

function envUrl(name: string, fallback: string): string {
  return (process.env[name] || fallback).replace(/\/$/, "");
}

const SERVICES: ServiceDef[] = [
  { id: "trading", name: "Trading Data API", baseUrl: envUrl("WICKEDAPI_BASE_URL", "https://api.wickedapi.com") },
  { id: "protocol_health", name: "Protocol Health Oracle", baseUrl: envUrl("WICKEDAPI_PROTOCOL_HEALTH_URL", "https://protocol-health-oracle.wickedapi.com") },
  { id: "token_risk", name: "Token Risk Oracle", baseUrl: envUrl("WICKEDAPI_TOKEN_RISK_URL", "https://token-risk-oracle.wickedapi.com") },
  { id: "broker_sync", name: "Broker Sync API", baseUrl: envUrl("WICKEDAPI_BROKER_SYNC_URL", "https://broker-sync-api.wickedapi.com") },
];
const SHOWCASE_URL = envUrl("WICKEDAPI_SHOWCASE_URL", "https://wickedapi.com");

const API_KEY = process.env.WICKEDAPI_API_KEY;
const PAYER_KEY = process.env.WICKEDAPI_PAYER_PRIVATE_KEY;

// A few auto-generated tool names collide or read badly (e.g. GET and POST
// both hitting /v1/protocols) — override just those, keyed by
// "METHOD /openapi/path/{param}".
const TOOL_NAME_OVERRIDES: Record<string, Record<string, string>> = {
  protocol_health: {
    "GET /v1/protocols": "protocol_health_list_protocols",
    "POST /v1/protocols": "protocol_health_track_protocol",
    "GET /v1/health/{slug}": "protocol_health_score",
    "POST /v1/refresh": "protocol_health_refresh_all",
    "POST /v1/ingest": "protocol_health_sync_universe",
  },
  broker_sync: {
    "POST /v1/users": "broker_sync_register_user",
  },
};

// --- OpenAPI shapes we actually consume --------------------------------
interface OpenApiParam {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: { type?: string };
}
interface OpenApiBodySchema {
  required?: string[];
  properties?: Record<string, { type?: string; description?: string }>;
}
interface OpenApiRequestBody {
  content?: { "application/json"?: { schema?: OpenApiBodySchema } };
}
interface OpenApiOp {
  operationId?: string;
  summary?: string;
  description?: string;
  parameters?: OpenApiParam[];
  requestBody?: OpenApiRequestBody;
}
interface OpenApiDoc {
  paths: Record<string, Record<string, OpenApiOp>>;
}

// If a funded Base-mainnet wallet key is provided, every call auto-pays the
// x402 402 challenge in USDC — no API key or signup needed at all. Loaded
// lazily so a plain API-key setup never has to pull in viem/x402-fetch.
async function buildFetcher(): Promise<typeof fetch> {
  if (!PAYER_KEY) return fetch;
  let deps;
  try {
    deps = await Promise.all([import("viem"), import("viem/accounts"), import("viem/chains"), import("x402-fetch")]);
  } catch {
    console.error(
      "[wickedapi-mcp] WICKEDAPI_PAYER_PRIVATE_KEY is set, but the wallet-payment dependencies " +
        "(viem, x402-fetch) aren't installed in this distribution. Falling back to plain fetch — " +
        "calls without an API key will surface the 402 challenge instead of auto-paying. " +
        "Use `npx wickedapi-mcp` for full auto-pay support."
    );
    return fetch;
  }
  const [{ createWalletClient, http }, { privateKeyToAccount }, { base }, { wrapFetchWithPayment }] = deps;
  const account = privateKeyToAccount(PAYER_KEY as `0x${string}`);
  const wallet = createWalletClient({ account, chain: base, transport: http() });
  console.error(`[wickedapi-mcp] x402 auto-pay enabled — wallet ${account.address}`);
  // x402-fetch's Signer type expects a client extended with viem's public
  // actions; the proven pattern in scripts/x402-bot-test.mjs passes a plain
  // wallet client the same way — cast rather than pull in publicActions.
  return wrapFetchWithPayment(fetch, wallet as any) as unknown as typeof fetch;
}

function pathSlug(openapiPath: string): string {
  return openapiPath
    .replace(/^\/v1\//, "")
    .replace(/[{}]/g, "")
    .replace(/[\/\-]+/g, "_");
}

function toolNameFor(service: ServiceDef, method: string, openapiPath: string): string {
  const override = TOOL_NAME_OVERRIDES[service.id]?.[`${method.toUpperCase()} ${openapiPath}`];
  if (override) return override;
  const slug = pathSlug(openapiPath);
  if (service.id === "trading") return slug; // legacy naming, already published
  return slug === service.id ? service.id : `${service.id}_${slug}`;
}

function zodFor(p: { type?: string; required?: boolean; description?: string }): z.ZodTypeAny {
  const base: z.ZodTypeAny =
    p.type === "number" || p.type === "integer"
      ? z.coerce.number()
      : p.type === "boolean"
        ? z.coerce.boolean()
        : z.string();
  const described = p.description ? base.describe(p.description) : base;
  return p.required ? described : described.optional();
}

async function loadServiceSpec(service: ServiceDef): Promise<OpenApiDoc | null> {
  try {
    const res = await fetch(`${service.baseUrl}/openapi.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as OpenApiDoc;
  } catch (err) {
    console.error(
      `[wickedapi-mcp] WARNING: couldn't load ${service.name} spec (${service.baseUrl}/openapi.json): ` +
        `${(err as Error).message} — its tools are unavailable this session, the rest load normally.`
    );
    return null;
  }
}

function registerServiceTools(server: McpServer, service: ServiceDef, doc: OpenApiDoc, doFetch: typeof fetch): number {
  let registered = 0;
  for (const [openapiPath, methods] of Object.entries(doc.paths)) {
    if (!openapiPath.startsWith("/v1/") || openapiPath === "/v1/status") continue; // /v1/status is a resource instead
    if (openapiPath.includes("webhook")) continue; // called by a third party, never by an agent

    for (const [method, op] of Object.entries(methods)) {
      const pathParamNames: string[] = [];
      const queryParamNames: string[] = [];
      const bodyPropNames: string[] = [];
      const shape: Record<string, z.ZodTypeAny> = {};

      for (const p of op.parameters ?? []) {
        if (p.in === "path") {
          pathParamNames.push(p.name);
          shape[p.name] = zodFor({ type: p.schema?.type, required: true, description: p.description });
        } else if (p.in === "query") {
          queryParamNames.push(p.name);
          shape[p.name] = zodFor({ type: p.schema?.type, required: p.required, description: p.description });
        }
      }
      const bodySchema = op.requestBody?.content?.["application/json"]?.schema;
      if (bodySchema) {
        const required = new Set(bodySchema.required ?? []);
        for (const [name, prop] of Object.entries(bodySchema.properties ?? {})) {
          bodyPropNames.push(name);
          shape[name] = zodFor({ type: prop.type, required: required.has(name), description: prop.description });
        }
      }

      const name = toolNameFor(service, method, openapiPath);
      server.registerTool(
        name,
        {
          description: `[${service.name}] ${[op.summary, op.description].filter(Boolean).join(" — ")}`.slice(0, 1000),
          inputSchema: shape,
        },
        async (args: Record<string, unknown>) => {
          let urlPath = openapiPath;
          for (const p of pathParamNames) urlPath = urlPath.replace(`{${p}}`, encodeURIComponent(String(args[p])));
          const url = new URL(service.baseUrl + urlPath);
          for (const p of queryParamNames) {
            const v = args[p];
            if (v !== undefined && v !== null) url.searchParams.set(p, String(v));
          }

          const headers: Record<string, string> = {};
          if (API_KEY) headers["x-api-key"] = API_KEY;
          const init: RequestInit = { method: method.toUpperCase(), headers };
          if (bodyPropNames.length) {
            const bodyObj: Record<string, unknown> = {};
            for (const p of bodyPropNames) if (args[p] !== undefined) bodyObj[p] = args[p];
            headers["content-type"] = "application/json";
            init.body = JSON.stringify(bodyObj);
          }

          const r = await doFetch(url.toString(), init);
          const text = await r.text();
          let body: unknown;
          try {
            body = JSON.parse(text);
          } catch {
            body = text;
          }
          return {
            isError: !r.ok,
            content: [{ type: "text" as const, text: JSON.stringify(body, null, 2) }],
          };
        }
      );
      registered++;
    }
  }
  return registered;
}

// --- Resources -----------------------------------------------------------
// Reference material an agent can read without invoking a tool: the
// platform-wide directory/llms.txt, and each service's live endpoint
// catalog (the /v1/status we deliberately excluded from becoming a tool).
function registerResources(server: McpServer) {
  server.registerResource(
    "platform-services",
    "wickedapi://platform/services",
    {
      title: "WickedAPI service directory",
      description: "Every live WickedAPI service — URL, category, one-line description, auth modes, and links to each one's own OpenAPI spec/docs/llms.txt.",
      mimeType: "application/json",
    },
    async (uri) => {
      const r = await fetch(`${SHOWCASE_URL}/services.json`);
      if (!r.ok) throw new Error(`Failed to load service directory: HTTP ${r.status}`);
      return { contents: [{ uri: uri.toString(), mimeType: "application/json", text: await r.text() }] };
    }
  );

  server.registerResource(
    "platform-llms-txt",
    "wickedapi://platform/llms",
    {
      title: "WickedAPI llms.txt",
      description: "Terse, platform-wide summary for agents deciding what to call — auth modes, free vs. paid endpoints.",
      mimeType: "text/plain",
    },
    async (uri) => {
      const r = await fetch(`${SHOWCASE_URL}/llms.txt`);
      if (!r.ok) throw new Error(`Failed to load llms.txt: HTTP ${r.status}`);
      return { contents: [{ uri: uri.toString(), mimeType: "text/plain", text: await r.text() }] };
    }
  );

  for (const service of SERVICES) {
    server.registerResource(
      `${service.id}-status`,
      `wickedapi://${service.id}/status`,
      {
        title: `${service.name} — live catalog`,
        description: `Live endpoint catalog for ${service.name} (${service.baseUrl}), fetched fresh on every read.`,
        mimeType: "application/json",
      },
      async (uri) => {
        const r = await fetch(`${service.baseUrl}/v1/status`);
        if (!r.ok) throw new Error(`Failed to load ${service.name} status: HTTP ${r.status}`);
        return { contents: [{ uri: uri.toString(), mimeType: "application/json", text: await r.text() }] };
      }
    );
  }
}

// --- Prompts ---------------------------------------------------------------
// The "compound recipes" from the WickedAPI cookbook, turned into one-click
// prompts: each just tells the assistant which real tools to call and how
// to read the results — no fabricated synthesis, every number cited comes
// from an actual tool call made during the conversation.
function registerPrompts(server: McpServer) {
  server.registerPrompt(
    "rug_check",
    {
      title: "Rug-check before buying",
      description: "Real on-chain risk signals for a token contract, plus market sentiment (and momentum, if it's a covered asset) before you decide whether to buy.",
      argsSchema: {
        address: z.string().describe("The token contract address (EVM) or mint address (Solana)."),
        chain: z.string().describe("ethereum, bsc, base, polygon, arbitrum, optimism, avalanche, fantom, zksync, linea, scroll, or solana."),
        symbol: z.string().optional().describe("If this token maps to a covered momentum-score asset (BTC, ETH, SOL, SUI, NVDA, IBIT, COIN, QQQ, TQQQ, DIA), pass it to also pull a real momentum verdict."),
      },
    },
    ({ address, chain, symbol }) => ({
      description: "Rug-check workflow",
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Before I consider buying this token, run a real risk check — don't guess or assume anything:\n\n` +
              `1. Call the \`token_risk\` tool with address="${address}" and chain="${chain}". Report the exact ` +
              `risk_score, risk_tier, and every real flag it returns (honeypot, mint/ownership authority, holder ` +
              `and LP concentration, buy/sell tax, trust-list status) — cite the actual values, don't summarize ` +
              `away the specifics or invent ones that weren't returned.\n` +
              `2. Call the \`sentiment\` tool for the current overall crypto market mood.\n` +
              (symbol
                ? `3. Call the \`momentum_score\` tool with symbol="${symbol}" and timeframe="1h" for a real technical read.\n`
                : "") +
              `\nThen give a plain verdict: the real risk signals found (listed, not paraphrased), current market ` +
              `mood, and — only if applicable — the momentum read. State clearly this is aggregated real signals, ` +
              `never a safety guarantee.`,
          },
        },
      ],
    })
  );

  server.registerPrompt(
    "vet_dependency",
    {
      title: "Vet a DeFi dependency",
      description: "Protocol activity/treasury health plus an optional contract-level risk check, before you build on a DeFi protocol.",
      argsSchema: {
        slug: z.string().describe('The protocol\'s DefiLlama slug, e.g. "lido".'),
        token_address: z.string().optional().describe("The protocol's token contract address, if you also want a contract-level risk check."),
        chain: z.string().optional().describe("Chain for token_address (default ethereum)."),
      },
    },
    ({ slug, token_address, chain }) => ({
      description: "Dependency-vetting workflow",
      messages: [
        {
          role: "user",
          content: {
            type: "text",
            text:
              `Before I integrate this protocol as a dependency, check its real health — don't guess:\n\n` +
              `1. Call the \`protocol_health_score\` tool with slug="${slug}". Report the components (GitHub ` +
              `activity, TVL trend, treasury) exactly as returned, including any that came back null (never ` +
              `filled in).\n` +
              (token_address
                ? `2. Call the \`token_risk\` tool with address="${token_address}" and chain="${chain || "ethereum"}" ` +
                  `for a contract-level check on its token.\n`
                : "") +
              `\nSummarize: is this protocol actively maintained, is its TVL trend healthy, and (if checked) does ` +
              `its token carry any real contract-level risk flags. Cite the real numbers.`,
          },
        },
      ],
    })
  );

  server.registerPrompt(
    "morning_briefing",
    {
      title: "Morning briefing",
      description: "One daily digest from macro calendar, earnings calendar, sentiment, and a momentum verdict — all free or a fraction of a cent.",
      argsSchema: {
        symbol: z.string().optional().describe("Asset for the momentum verdict (default BTC). One of BTC, ETH, SOL, SUI, NVDA, IBIT, COIN, QQQ, TQQQ, DIA."),
      },
    },
    ({ symbol }) => {
      const sym = symbol || "BTC";
      return {
        description: "Morning briefing workflow",
        messages: [
          {
            role: "user",
            content: {
              type: "text",
              text:
                `Compile my morning briefing from real, current data — call each tool, don't estimate:\n\n` +
                `1. \`macro_calendar\` with days=7 — upcoming FOMC/CPI/jobs events.\n` +
                `2. \`earnings_calendar\` with days=3 — upcoming earnings reports.\n` +
                `3. \`sentiment\` — current crypto Fear & Greed reading.\n` +
                `4. \`momentum_score\` with symbol="${sym}" and timeframe="1D" — today's technical verdict.\n\n` +
                `Then write one concise digest combining all four, citing the real values from each call.`,
            },
          },
        ],
      };
    }
  );
}


export const SERVER_VERSION = "0.4.0";

// Everything expensive or network-bound happens once, in loadWorld(): the
// payer-wallet fetcher and the OpenAPI specs of the generated services.
// buildServer() is then synchronous and cheap, so the stateless HTTP
// transport can create a fresh McpServer per request.
export interface WickedWorld {
  doFetch: typeof fetch;
  specs: { service: ServiceDef; doc: OpenApiDoc }[];
}

export async function loadWorld(): Promise<WickedWorld> {
  const doFetch = await buildFetcher();
  const specs: WickedWorld["specs"] = [];
  for (const service of SERVICES) {
    const doc = await loadServiceSpec(service);
    if (doc) specs.push({ service, doc });
  }
  if (specs.length === 0) {
    throw new Error("No service specs loaded - check network access and WICKEDAPI_*_URL overrides.");
  }
  return { doFetch, specs };
}

export function buildServer(world: WickedWorld, hooks?: ToolHooks): { server: McpServer; summary: string } {
  const server = new McpServer({ name: "wickedapi", version: SERVER_VERSION });
  if (hooks) instrument(server as any, hooks);
  let totalTools = 0;
  const loaded: string[] = [];
  for (const { service, doc } of world.specs) {
    totalTools += registerServiceTools(server, service, doc, world.doFetch);
    loaded.push(service.name);
  }
  // Reputation, Registry, Identity, Sanity, Memory and Paywall use hand-written
  // tools (non-/v1 paths, wallet-signature auth), registered unconditionally.
  totalTools += registerSuiteTools(server);
  loaded.push("Reputation, Registry, Identity, Sanity, Memory, Paywall");

  registerResources(server);
  registerPrompts(server);
  registerSuitePrompts(server);
  return {
    server,
    summary: `${totalTools} tools across ${loaded.length} group(s) (${loaded.join(", ")}), ${2 + SERVICES.length} resources, 7 prompts`,
  };
}
