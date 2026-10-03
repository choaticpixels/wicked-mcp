// Boots the real HTTP server and talks to it with the SDK's own client.
// Needs network access (it loads the live services' OpenAPI specs).
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const PORT = 18080;
let proc;

test.before(async () => {
  proc = spawn(process.execPath, ["dist/http.js"], { env: { ...process.env, PORT: String(PORT), RATE_LIMIT_PER_MINUTE: "1000", ALLOWED_HOSTS: `localhost:${PORT},127.0.0.1:${PORT}` }, stdio: ["ignore", "ignore", "pipe"] });
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("server did not start")), 60_000);
    proc.stderr.on("data", (d) => String(d).includes("http on") && (clearTimeout(t), resolve()));
    proc.on("exit", (c) => reject(new Error("server exited " + c)));
  });
});
test.after(() => proc?.kill());

test("serves all 70 tools over streamable HTTP and answers a live call", async () => {
  const c = new Client({ name: "e2e", version: "1" });
  await c.connect(new StreamableHTTPClientTransport(new URL(`http://localhost:${PORT}/mcp`)));
  const { tools } = await c.listTools();
  assert.equal(tools.length, 70);
  for (const p of ["reputation_", "registry_", "identity_", "sanity_", "memory_", "paywall_", "protocol_health", "broker_sync"]) {
    assert.ok(tools.some((t) => t.name.startsWith(p)), `missing ${p}*`);
  }
  const r = await c.callTool({ name: "reputation_tiers", arguments: {} });
  assert.equal(JSON.parse(r.content[0].text).http_status, 200);
  await c.close();
});

test("rejects bad Host, non-POST and unknown paths; /health is open", async () => {
  const base = `http://localhost:${PORT}`;
  assert.equal((await fetch(`${base}/health`)).status, 200);
  assert.equal((await fetch(`${base}/mcp`)).status, 405);
  assert.equal((await fetch(`${base}/nope`)).status, 404);
  const status = await new Promise((resolve, reject) => {
    const r = http.request({ host: "127.0.0.1", port: PORT, path: "/mcp", method: "POST", headers: { host: "evil.example", "content-type": "application/json" } }, (res) => {
      res.resume();
      resolve(res.statusCode);
    });
    r.on("error", reject);
    r.end("{}");
  });
  assert.equal(status, 421);
});
