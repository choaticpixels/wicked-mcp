// Who is calling, for rate limiting. Ported from the Python http_app.py so
// behaviour is unchanged after the cutover.
//
// Behind Railway's edge proxy the TCP peer is always an internal address, so
// the limiter keys on the proxy-set X-Real-IP header instead. That is only
// safe because the proxy sets the header itself; set TRUSTED_CLIENT_IP_HEADER
// to empty to fall back to the TCP peer when running without a proxy, or to
// cf-connecting-ip if the domain is ever proxied through Cloudflare.
import { isIP } from "node:net";
import type { IncomingMessage } from "node:http";

const TRUSTED_HEADER = (process.env.TRUSTED_CLIENT_IP_HEADER ?? "x-real-ip").trim().toLowerCase();

function expandIPv6(ip: string): number[] | null {
  let s = ip.split("%")[0];
  let tail: number[] = [];
  const v4 = s.match(/(\d+\.\d+\.\d+\.\d+)$/);
  if (v4) {
    const o = v4[1].split(".").map(Number);
    tail = [(o[0] << 8) | o[1], (o[2] << 8) | o[3]];
    s = s.slice(0, -v4[1].length) + "0:0";
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const rest = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = 8 - head.length - rest.length;
  if ((halves.length === 1 && fill !== 0) || fill < 0) return null;
  const groups = [...head, ...Array(halves.length === 2 ? fill : 0).fill("0"), ...rest].map((g) => parseInt(g, 16));
  if (groups.length !== 8 || groups.some((g) => Number.isNaN(g))) return null;
  if (tail.length) {
    groups[6] = tail[0];
    groups[7] = tail[1];
  }
  return groups;
}

/** Canonical rate-limit key, or null if `raw` isn't an IP. IPv4-mapped IPv6
 *  collapses to the IPv4 address; IPv6 is grouped by /64 so rotating addresses
 *  inside one prefix can't dodge the limit. */
export function normalizeIp(raw: string): string | null {
  const s = raw.trim();
  const kind = isIP(s);
  if (kind === 4) return s;
  if (kind !== 6) return null;
  const g = expandIPv6(s);
  if (!g) return null;
  if (g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff) {
    return `${g[6] >> 8}.${g[6] & 255}.${g[7] >> 8}.${g[7] & 255}`;
  }
  return `${g.slice(0, 4).map((x) => x.toString(16)).join(":")}::/64`;
}

export function clientKey(req: IncomingMessage): string {
  if (TRUSTED_HEADER) {
    const raw = req.headers[TRUSTED_HEADER];
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (value) {
      const key = normalizeIp(value);
      if (key) return key;
    }
  }
  const peer = req.socket.remoteAddress ?? "unknown";
  return normalizeIp(peer) ?? peer;
}
