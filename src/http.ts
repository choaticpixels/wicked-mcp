// Streamable-HTTP entry point for the public remote at https://mcp.wickedapi.com/mcp.
// Stateless: every request gets its own McpServer + transport (specs are
// loaded once at boot), so it scales and restarts without session loss.
// Public-endpoint additions over the stdio build: a per-IP rate limit and a
// Host allowlist (DNS-rebinding protection). Tools and upstream calls are
// identical.
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { clientKey } from "./clientIp.js";
import { rateLimited } from "./rateLimit.js";
import { landingHtml, llmsTxt, serverCard } from "./landing.js";
import { knownTools, usageHooks, usageSnapshot } from "./usage.js";
import { buildServer, loadWorld, SERVER_VERSION } from "./server.js";
import { captureServerError, flushSentry, initSentry } from "./observability.js";

initSentry(SERVER_VERSION);

const PORT = Number(process.env.PORT || 8080);
const RATE_LIMIT_PER_MINUTE = Number(process.env.RATE_LIMIT_PER_MINUTE || 30);
const MAX_BODY_BYTES = 1_000_000;
const ALLOWED_HOSTS = (
  process.env.ALLOWED_HOSTS || "mcp.wickedapi.com,wicked-mcp-production.up.railway.app,localhost:8080,127.0.0.1:8080"
)
  .split(",")
  .map((h) => h.trim().toLowerCase())
  .filter(Boolean);

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}) {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

function sendText(res: ServerResponse, contentType: string, body: string) {
  res.writeHead(200, { "content-type": `${contentType}; charset=utf-8`, "cache-control": "public, max-age=300" });
  res.end(body);
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error("payload too large"), { status: 413 });
    chunks.push(chunk as Buffer);
  }
  if (!chunks.length) return undefined;
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw Object.assign(new Error("invalid JSON"), { status: 400 });
  }
}

async function main() {
  const world = await loadWorld();
  const { summary } = buildServer(world, { onRegister: usageHooks.onRegister });
  const tools = [...knownTools];

  const http = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://x");
    if (url.pathname === "/health") return send(res, 200, { status: "ok", version: SERVER_VERSION });

    const host = (req.headers.host || "").toLowerCase();
    if (!ALLOWED_HOSTS.includes(host)) return send(res, 421, { error: "Invalid Host header" });
    if (req.method === "GET" || req.method === "HEAD") {
      if (url.pathname === "/") return sendText(res, "text/html", landingHtml(tools, SERVER_VERSION));
      if (url.pathname === "/llms.txt") return sendText(res, "text/plain", llmsTxt(tools, SERVER_VERSION));
      if (url.pathname === "/.well-known/mcp/server-card.json" || url.pathname === "/.well-known/mcp.json") {
        return send(res, 200, serverCard(tools, SERVER_VERSION), { "cache-control": "public, max-age=300" });
      }
      if (url.pathname === "/stats") return send(res, 200, usageSnapshot());
    }
    if (url.pathname !== "/mcp") return send(res, 404, { error: "Not found. The MCP endpoint is /mcp." });
    if (req.method !== "POST") {
      return send(
        res,
        405,
        { jsonrpc: "2.0", error: { code: -32000, message: "Method not allowed (stateless server: POST only)." }, id: null },
        { allow: "POST" }
      );
    }
    if (rateLimited(clientKey(req), RATE_LIMIT_PER_MINUTE)) {
      return send(
        res,
        429,
        { error: `Rate limit exceeded: ${RATE_LIMIT_PER_MINUTE} requests/minute per IP. Run this server locally (npx @wickedlabs/wicked-mcp) for unlimited use.` },
        { "retry-after": "60" }
      );
    }

    try {
      const body = await readJson(req);
      const { server } = buildServer(world, usageHooks);
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      res.on("close", () => {
        void transport.close();
        void server.close();
      });
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    } catch (err) {
      const status = (err as { status?: number }).status ?? 500;
      // Only server faults: 4xx here are client mistakes (bad JSON, oversized body).
      if (status >= 500) captureServerError(err, { path: url.pathname, method: req.method });
      if (!res.headersSent) {
        send(res, status, { jsonrpc: "2.0", error: { code: -32603, message: (err as Error).message }, id: null });
      }
    }
  });

  http.listen(PORT, "0.0.0.0", () => console.error(`[wickedapi-mcp] http on :${PORT}/mcp - ${summary}.`));
}

main().catch((err) => {
  console.error("[wickedapi-mcp] fatal:", err);
  captureServerError(err, { path: "startup" });
  void flushSentry().finally(() => process.exit(1));
});
