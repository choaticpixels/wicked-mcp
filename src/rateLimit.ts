const WINDOW_MS = 60_000;
const PRUNE_ABOVE_ENTRIES = 1000;

// In-memory fixed-window limiter; fine for the single instance this runs as.
// Move the counters to shared storage before running more than one instance.
const hits = new Map<string, number[]>();
let lastPrune = 0;

export function rateLimited(key: string, limit: number = Number(process.env.RATE_LIMIT_PER_MINUTE || 30), now = Date.now()): boolean {
  if (hits.size >= PRUNE_ABOVE_ENTRIES && now - lastPrune > 30_000) {
    lastPrune = now;
    for (const [k, h] of hits) if (!h.length || now - h[h.length - 1] > WINDOW_MS) hits.delete(k);
  }
  const bucket = (hits.get(key) ?? []).filter((t) => now - t <= WINDOW_MS);
  if (bucket.length >= limit) {
    hits.set(key, bucket);
    return true;
  }
  bucket.push(now);
  hits.set(key, bucket);
  return false;
}

