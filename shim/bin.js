#!/usr/bin/env node
// `wickedapi-mcp` moved to `@wickedlabs/wicked-mcp`. This forwards to it so
// existing `npx -y wickedapi-mcp` configs keep working. stderr only: stdout is
// the MCP protocol stream.
console.error("[wickedapi-mcp] This package has moved to @wickedlabs/wicked-mcp. Update your config to `npx -y @wickedlabs/wicked-mcp`.");
await import("@wickedlabs/wicked-mcp/dist/index.js");
