// Discovery surfaces served by the HTTP deployment: a human landing page,
// /llms.txt for crawlers and agents, and an MCP server card. All three are
// generated from the tools actually registered, so they cannot drift from
// what the server exposes.
const SITE = "https://mcp.wickedapi.com";

const GROUPS: { id: string; label: string; prefix?: string; blurb: string }[] = [
  { id: "protocol_health", label: "Protocol Health Oracle", prefix: "protocol_health_", blurb: "Score any DeFi protocol before you integrate it." },
  { id: "token_risk", label: "Token Risk Oracle", prefix: "token_risk", blurb: "Free rug / honeypot check for EVM and Solana tokens." },
  { id: "broker_sync", label: "Broker Sync", prefix: "broker_sync_", blurb: "Positions and balances across 30+ brokerages." },
  { id: "reputation", label: "Wicked Reputation", prefix: "reputation_", blurb: "On-chain agent stake, tiers and slashing history. Free." },
  { id: "registry", label: "Wicked Registry", prefix: "registry_", blurb: "Real uptime, latency and schema scores for x402/MCP tools." },
  { id: "identity", label: "Wicked Identity", prefix: "identity_", blurb: "Know-Your-Agent verification with signed assertions." },
  { id: "sanity", label: "Wicked Sanity", prefix: "sanity_", blurb: "Hallucination check against your source or live web evidence." },
  { id: "memory", label: "Wicked Memory", prefix: "memory_", blurb: "Persistent, wallet-scoped agent memory with semantic search." },
  { id: "paywall", label: "x402 Paywall", prefix: "paywall_", blurb: "Put any endpoint behind pay-per-call USDC." },
];

export const PROMPT_NAMES = ["rug_check", "vet_dependency", "morning_briefing", "fact_check", "verify_agent", "find_reliable_tool", "agent_memory_guide"];

function grouped(tools: string[]) {
  const claimed = new Set<string>();
  const out = GROUPS.map((g) => {
    const names = tools.filter((t) => t.startsWith(g.prefix!));
    names.forEach((n) => claimed.add(n));
    return { ...g, tools: names };
  });
  const trading = tools.filter((t) => !claimed.has(t));
  return [{ id: "trading", label: "Trading Data API", blurb: "Prices, OHLCV, indicators, funding/OI, macro, prediction markets, momentum score.", tools: trading }, ...out];
}

export function llmsTxt(tools: string[], version: string): string {
  const g = grouped(tools);
  return [
    "# Wicked MCP",
    "",
    `> One MCP server for the whole Wicked suite: ${tools.length} tools across ${g.length} services - market data, DeFi and token risk, brokerage sync, agent identity, reputation, tool registry, hallucination checks, persistent memory and x402 paywalls. Version ${version}.`,
    "",
    "## Connect",
    `- Remote (streamable HTTP, no install): ${SITE}/mcp`,
    "- Local (stdio): `npx -y wickedapi-mcp`",
    "- Claude Code: `claude mcp add --transport http wicked " + SITE + "/mcp`",
    "- No signup needed. Priced calls return a real x402 payment challenge (USDC on Base); set WICKEDAPI_API_KEY or a payer wallet locally for free-tier / auto-pay.",
    "- The public remote is rate limited per IP; run it locally with `npx` for unlimited use.",
    "",
    "## Services and tools",
    ...g.map((s) => `- ${s.label} (${s.tools.length}): ${s.blurb} Tools: ${s.tools.join(", ")}`),
    "",
    "## Workflow prompts",
    ...PROMPT_NAMES.map((p) => `- ${p}`),
    "",
    "## Links",
    "- Platform: https://wickedapi.com (service directory at https://wickedapi.com/services.json)",
    "- Source: https://github.com/choaticpixels/wicked-mcp",
    "- npm: https://www.npmjs.com/package/wickedapi-mcp",
    "- Registry: io.github.choaticpixels/wicked-mcp on https://registry.modelcontextprotocol.io",
    "- Server card: " + SITE + "/.well-known/mcp/server-card.json",
    "",
    "Wallet-signed tools (identity, memory, registry registration) never sign for you: you supply the signature from your own wallet.",
    "",
  ].join("\n");
}

export function serverCard(tools: string[], version: string) {
  return {
    $schema: "https://static.modelcontextprotocol.io/schemas/mcp-server-card/v1.json",
    version: "1.0",
    protocolVersion: "2025-06-18",
    serverInfo: { name: "io.github.choaticpixels/wicked-mcp", title: "Wicked MCP", version },
    description: `The whole Wicked suite for agents: ${tools.length} tools across market data, DeFi/token risk, identity, reputation, registry, sanity checks, memory and paywalls. x402-native.`,
    documentationUrl: "https://github.com/choaticpixels/wicked-mcp",
    transport: { type: "streamable-http", endpoint: "/mcp" },
    capabilities: { tools: { listChanged: false }, resources: {}, prompts: {} },
    authentication: { required: false, note: "Optional keys unlock free tiers; otherwise paid calls return an x402 challenge." },
    packages: [{ registry: "npm", name: "wickedapi-mcp", transport: "stdio" }],
    tools: tools.map((name) => ({ name })),
  };
}

const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);

export function landingHtml(tools: string[], version: string): string {
  const g = grouped(tools);
  const rows = g
    .map(
      (s) =>
        `<details><summary><b>${esc(s.label)}</b> <span>${s.tools.length} tools</span><em>${esc(s.blurb)}</em></summary><code>${s.tools.map(esc).join(" &middot; ")}</code></details>`
    )
    .join("\n");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wicked MCP</title><meta name="description" content="One MCP server for the whole Wicked suite: ${tools.length} tools for agents.">
<style>:root{color-scheme:light dark;--bg:#fff;--fg:#14171f;--mut:#5b6472;--card:#f4f6fa;--acc:#6d28d9}@media(prefers-color-scheme:dark){:root{--bg:#0e1117;--fg:#e8ebf2;--mut:#98a2b3;--card:#181d27;--acc:#a78bfa}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}main{max-width:760px;margin:0 auto;padding:32px 16px}h1{margin:0 0 4px}p.l{color:var(--mut);margin-top:0}
pre{background:var(--card);padding:12px 14px;border-radius:8px;overflow:auto;font-size:14px}details{background:var(--card);border-radius:8px;padding:10px 14px;margin:8px 0}summary{cursor:pointer}summary span{color:var(--acc);margin:0 8px;font-size:14px}summary em{display:block;color:var(--mut);font-style:normal;font-size:14px}
code{display:block;margin-top:8px;font-size:13px;color:var(--mut);word-break:break-word}a{color:var(--acc)}footer{color:var(--mut);font-size:14px;margin-top:24px}</style></head><body><main>
<h1>Wicked MCP</h1><p class="l">${tools.length} tools. ${g.length} services. One server for agents. v${esc(version)}</p>
<h3>Connect in one line</h3>
<pre>claude mcp add --transport http wicked ${SITE}/mcp</pre>
<pre>{ "mcpServers": { "wicked": { "url": "${SITE}/mcp" } } }</pre>
<pre>npx -y wickedapi-mcp   # local, stdio - unlimited, supports your API key or payer wallet</pre>
<p>No signup. Priced calls return a real x402 payment challenge (USDC on Base). The public endpoint is rate limited per IP.</p>
<h3>What's inside</h3>
${rows}
<footer><a href="https://wickedapi.com">wickedapi.com</a> &middot; <a href="https://github.com/choaticpixels/wicked-mcp">GitHub</a> &middot; <a href="https://www.npmjs.com/package/wickedapi-mcp">npm</a> &middot; <a href="/llms.txt">llms.txt</a> &middot; <a href="/.well-known/mcp/server-card.json">server card</a> &middot; <a href="/stats">usage</a></footer>
</main></body></html>`;
}
