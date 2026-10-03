import test from "node:test";
import assert from "node:assert/strict";
import { normalizeIp } from "../dist/clientIp.js";
import { rateLimited } from "../dist/rateLimit.js";
import { __test } from "../dist/suite.js";

test("normalizeIp: v4, mapped v6, /64 grouping, junk", () => {
  assert.equal(normalizeIp("203.0.113.9"), "203.0.113.9");
  assert.equal(normalizeIp("::ffff:203.0.113.9"), "203.0.113.9");
  assert.equal(normalizeIp("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), normalizeIp("2001:db8:1:2::1"));
  assert.notEqual(normalizeIp("2001:db8:1:2::1"), normalizeIp("2001:db8:1:3::1"));
  assert.equal(normalizeIp("not-an-ip"), null);
  assert.equal(normalizeIp(""), null);
});

test("rateLimited: blocks after the limit, per key, window expires", () => {
  const t0 = 1_000_000;
  for (let i = 0; i < 3; i++) assert.equal(rateLimited("a", 3, t0 + i), false);
  assert.equal(rateLimited("a", 3, t0 + 10), true);
  assert.equal(rateLimited("b", 3, t0 + 10), false);
  assert.equal(rateLimited("a", 3, t0 + 61_000), false);
});

test("memoryBuild: matches the signed Python server byte-for-byte (vectors from wicked-mcp 0.1)", () => {
  const s = __test.memoryBuild("search", { q: "it's (a) test*! a/b", tags: ["a/b", "c,d"], limit: 5, include_superseded: false });
  assert.equal(s.path, "/memories/search?q=it%27s%20%28a%29%20test%2A%21%20a%2Fb&limit=5&tags=a%2Fb%2Cc%2Cd&include_superseded=false");
  const d = __test.memoryBuild("delete", { memory_id: "123E4567-E89B-12D3-A456-426614174000", reason: "a&b c/d" });
  assert.equal(d.path, "/memories/123e4567-e89b-12d3-a456-426614174000?scope=chain&reason=a%26b%20c%2Fd");
  assert.throws(() => __test.memoryBuild("get", { memory_id: "nope" }), /UUID/);
  assert.throws(() => __test.memoryBuild("bogus", {}), /unknown operation/);
});
