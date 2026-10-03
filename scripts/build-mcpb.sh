#!/usr/bin/env bash
# Rebuilds the .mcpb bundle for Smithery/desktop-extension distribution.
# Uses a separate lean install (SDK + zod only) so the bundle stays small —
# the optional payer-wallet deps (viem, x402-fetch) are npx/npm-only and
# degrade gracefully at runtime if missing (see buildFetcher() in src/index.ts).
set -euo pipefail
cd "$(dirname "$0")/.."

npm run build

VERSION=$(node -p "require('./package.json').version")

rm -rf mcpb-build wickedapi-mcp.mcpb
mkdir -p mcpb-build/dist
cp -r dist/. mcpb-build/dist
cp manifest.json LICENSE README.md mcpb-build/

cat > mcpb-build/package.json << EOF
{
  "name": "wickedapi-mcp",
  "version": "$VERSION",
  "private": true,
  "type": "module",
  "main": "dist/index.js",
  "engines": { "node": ">=20" },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.29.0",
    "zod": "^4.4.3"
  }
}
EOF

(cd mcpb-build && npm install --omit=dev)
npx --yes @anthropic-ai/mcpb validate mcpb-build/manifest.json
npx --yes @anthropic-ai/mcpb pack mcpb-build wickedapi-mcp.mcpb

echo "Built: mcpb-server/wickedapi-mcp.mcpb"
