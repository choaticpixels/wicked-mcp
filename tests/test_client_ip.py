"""Rate limiting must key on the real caller (Railway's X-Real-IP), not the TCP
peer (always Railway's own proxy), and must not be gameable through the header.

Run:  pip install -r requirements-dev.txt && pytest
"""
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import http_app


async def _ok(request):
    return PlainTextResponse("ok")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(http_app, "RATE_LIMIT_PER_MINUTE", 3)
    app = Starlette(routes=[Route("/", _ok)])
    app.add_middleware(http_app.RateLimitMiddleware)
    return TestClient(app)


def _hit(client, ip=None, n=1):
    headers = {"x-real-ip": ip} if ip is not None else {}
    return [client.get("/", headers=headers).status_code for _ in range(n)]


def test_each_real_ip_gets_its_own_bucket(client):
    assert _hit(client, "203.0.113.1", 4) == [200, 200, 200, 429]
    # A different caller is unaffected by the first one hitting its limit.
    assert _hit(client, "203.0.113.2", 3) == [200, 200, 200]
    # ...and the first is still limited.
    assert _hit(client, "203.0.113.1") == [429]


def test_without_the_header_callers_share_the_tcp_peer_bucket(client):
    assert _hit(client, None, 4) == [200, 200, 200, 429]


@pytest.mark.parametrize("junk", ["not-an-ip", "", "999.1.1.1", "1.2.3.4, 5.6.7.8", "'; DROP TABLE"])
def test_junk_header_values_cannot_mint_fresh_buckets(client, junk):
    # Every junk value falls back to the same peer bucket, so rotating them
    # buys an attacker nothing.
    results = [client.get("/", headers={"x-real-ip": f"{junk}{i}"}).status_code for i in range(4)]
    assert results == [200, 200, 200, 429]


def test_ipv6_is_grouped_by_64_prefix(client):
    same_block = ["2001:db8:aaaa:1::1", "2001:db8:aaaa:1::2", "2001:db8:aaaa:1:ffff::9", "2001:db8:aaaa:1::3"]
    assert [client.get("/", headers={"x-real-ip": ip}).status_code for ip in same_block] == [200, 200, 200, 429]
    assert _hit(client, "2001:db8:aaaa:2::1") == [200]  # a different /64 is a different caller


def test_ipv4_mapped_ipv6_matches_the_plain_ipv4(client):
    assert _hit(client, "198.51.100.7", 3) == [200, 200, 200]
    assert _hit(client, "::ffff:198.51.100.7") == [429]


def test_header_can_be_disabled_for_deployments_without_a_trusted_proxy(client, monkeypatch):
    monkeypatch.setattr(http_app, "_TRUSTED_CLIENT_IP_HEADER", "")
    # With no trusted proxy, a client-chosen header must be ignored entirely.
    results = [client.get("/", headers={"x-real-ip": f"203.0.113.{i}"}).status_code for i in range(4)]
    assert results == [200, 200, 200, 429]


def test_a_different_trusted_header_can_be_configured(client, monkeypatch):
    monkeypatch.setattr(http_app, "_TRUSTED_CLIENT_IP_HEADER", "cf-connecting-ip")
    ok = [client.get("/", headers={"cf-connecting-ip": "203.0.113.9"}).status_code for _ in range(3)]
    assert ok == [200, 200, 200]
    # x-real-ip no longer identifies the caller, so this request lands in the
    # shared TCP-peer bucket, not 203.0.113.9's (which is now exhausted).
    assert client.get("/", headers={"x-real-ip": "203.0.113.9"}).status_code == 200


def test_expired_buckets_are_pruned_so_memory_cannot_grow_forever(monkeypatch):
    monkeypatch.setattr(http_app, "_PRUNE_ABOVE_ENTRIES", 10)
    mw = http_app.RateLimitMiddleware(app=lambda *a, **k: None)
    now = 10_000.0
    for i in range(50):
        mw._hits[f"10.0.0.{i}"].append(now - 120)  # long outside the 60s window
    mw._hits["10.9.9.9"].append(now - 5)  # still active
    mw._prune(now)
    assert list(mw._hits) == ["10.9.9.9"]
