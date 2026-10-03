#!/usr/bin/env node
// stdio entry point: `npx wickedapi-mcp` for Claude Desktop, Cursor, .mcpb, etc.
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { buildServer, loadWorld } from "./server.js";

async function main() {
  const { server, summary } = buildServer(await loadWorld());
  await server.connect(new StdioServerTransport());
  console.error(`[wickedapi-mcp] ready - ${summary}.`);
}

main().catch((err) => {
  console.error("[wickedapi-mcp] fatal:", err);
  process.exit(1);
});
