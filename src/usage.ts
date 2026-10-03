// Per-tool usage accounting for the public HTTP deployment. Counts only: tool
// name, success/error and latency. Arguments, results and caller IPs are never
// recorded. Counters are in-memory (reset on deploy); each call is also written
// as one JSON log line so Railway's log history is the durable record.
export interface ToolHooks {
  onRegister?: (name: string) => void;
  onToolCall?: (name: string, ms: number, isError: boolean) => void;
}

const startedAt = Date.now();
const counts = new Map<string, { calls: number; errors: number; totalMs: number }>();
export const knownTools = new Set<string>();

export const usageHooks: ToolHooks = {
  onRegister: (name) => knownTools.add(name),
  onToolCall: (name, ms, isError) => {
    const c = counts.get(name) ?? { calls: 0, errors: 0, totalMs: 0 };
    c.calls++;
    c.totalMs += ms;
    if (isError) c.errors++;
    counts.set(name, c);
    console.log(JSON.stringify({ evt: "tool_call", tool: name, error: isError, ms }));
  },
};

export function usageSnapshot() {
  const tools = [...counts.entries()]
    .map(([tool, c]) => ({ tool, calls: c.calls, errors: c.errors, avg_ms: Math.round(c.totalMs / c.calls) }))
    .sort((a, b) => b.calls - a.calls);
  return {
    since: new Date(startedAt).toISOString(),
    note: "In-memory since last deploy; durable history is in the service logs.",
    total_calls: tools.reduce((n, t) => n + t.calls, 0),
    tools,
  };
}

// Wrap server.registerTool so every handler reports to the hooks. Must run
// before any tools are registered.
export function instrument(server: { registerTool: (...a: any[]) => any }, hooks: ToolHooks) {
  const original = server.registerTool.bind(server);
  server.registerTool = (name: string, config: unknown, handler: (...a: any[]) => Promise<any>) => {
    hooks.onRegister?.(name);
    return original(name, config, async (...args: any[]) => {
      const t0 = Date.now();
      let isError = true;
      try {
        const result = await handler(...args);
        isError = Boolean(result?.isError);
        return result;
      } finally {
        hooks.onToolCall?.(name, Date.now() - t0, isError);
      }
    });
  };
}
