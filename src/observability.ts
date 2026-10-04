// Sentry error monitoring for the public HTTP remote only (src/http.ts). The
// stdio build (src/index.ts, shipped via npm and .mcpb) never imports this, so
// nothing is ever reported from users' own machines.
//
// Errors only: no tracing, no request bodies. MCP tool arguments can carry
// wallet signatures and Wicked Memory content, so events are scrubbed of
// anything request-shaped before they leave the process.
import * as Sentry from "@sentry/node";

const SENSITIVE_HEADERS = new Set([
  "x-api-key",
  "x-admin-api-key",
  "authorization",
  "cookie",
  "x-payment",
  "payment-signature",
]);

export function scrubEvent<T extends Sentry.ErrorEvent>(event: T): T {
  const req = event.request;
  if (req) {
    delete req.data; // JSON-RPC params: signatures, memory content
    delete req.query_string;
    delete req.cookies;
    if (req.headers) {
      for (const k of Object.keys(req.headers)) {
        if (SENSITIVE_HEADERS.has(k.toLowerCase())) req.headers[k] = "[redacted]";
      }
    }
  }
  return event;
}

export function initSentry(version: string): boolean {
  const dsn = process.env.SENTRY_DSN;
  if (!dsn) return false;
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT || "production",
    release: `wickedapi-mcp@${version}`,
    tracesSampleRate: 0,
    // v11 collects headers, bodies, query strings and stack-frame variables by
    // default. Turn everything off; beforeSend below is defense in depth.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      stackFrameVariables: false,
      databaseQueryData: false,
      queues: false,
    },
    beforeSend: scrubEvent,
  });
  return true;
}

export function captureServerError(err: unknown, context: { path: string; method?: string }): void {
  Sentry.captureException(err, { tags: { path: context.path, method: context.method ?? "?" } });
}

export async function flushSentry(timeoutMs = 2000): Promise<void> {
  await Sentry.flush(timeoutMs);
}
