"""Wicked Memory MCP tools: the request built for signing must be byte-identical to the request
sent, and the signed message must match the service's (agent_memory/src/authMessage.ts) format.

Run:  pip install -r requirements-dev.txt && pytest
"""
import asyncio
import hashlib
import json

import httpx
import pytest

import server

WALLET = "0xAbC0000000000000000000000000000000000001"
MID = "11111111-2222-4333-8444-555555555555"


def test_message_format_matches_service():
    body = b'{"content":"hi"}'
    msg = server._memory_message(WALLET, "post", "/memories", "1700000000", "n" * 16, body)
    assert msg == "\n".join(
        [
            "Wicked Memory auth",
            f"wallet: {WALLET.lower()}",
            "method: POST",
            "path: /memories",
            "timestamp: 1700000000",
            f"nonce: {'n' * 16}",
            f"body-sha256: {hashlib.sha256(body).hexdigest()}",
        ]
    )


def test_empty_body_hashes_the_empty_string():
    msg = server._memory_message(WALLET, "GET", "/memory-deletions", "1", "n" * 16, None)
    assert msg.endswith(hashlib.sha256(b"").hexdigest())


def test_build_each_operation():
    assert server._memory_build("store", {"content": "x", "tags": ["a"], "source": None}) == (
        "POST",
        "/memories",
        b'{"content":"x","tags":["a"]}',
    )
    method, path, raw = server._memory_build("search", {"q": "what db", "limit": 5, "tags": ["a", "b"], "include_superseded": True})
    assert (method, raw) == ("GET", None)
    assert path == "/memories/search?q=what%20db&limit=5&tags=a%2Cb&include_superseded=true"
    assert server._memory_build("get", {"memory_id": MID}) == ("GET", f"/memories/{MID}", None)
    assert server._memory_build("update", {"memory_id": MID, "content": "y"}) == ("PATCH", f"/memories/{MID}", b'{"content":"y"}')
    assert server._memory_build("history", {"memory_id": MID})[1] == f"/memories/{MID}/history"
    assert server._memory_build("delete", {"memory_id": MID, "reason": "gdpr", "scope": "version"}) == (
        "DELETE",
        f"/memories/{MID}?scope=version&reason=gdpr",
        None,
    )
    assert server._memory_build("deletions", {}) == ("GET", "/memory-deletions", None)
    # prepare (scope omitted) and the tool (scope defaults to "chain") must build the same path
    assert server._memory_build("delete", {"memory_id": MID}) == server._memory_build("delete", {"memory_id": MID, "scope": "chain"})
    assert server._memory_build("delete", {"memory_id": MID})[1] == f"/memories/{MID}?scope=chain"


@pytest.mark.parametrize(
    "op,params",
    [("nope", {}), ("store", {}), ("search", {}), ("get", {"memory_id": "../../etc/passwd"}), ("delete", {"memory_id": "x"})],
)
def test_bad_input_is_rejected(op, params):
    with pytest.raises(ValueError):
        server._memory_build(op, params)


def test_prepare_returns_signable_message_and_reports_errors():
    out = asyncio.run(server.memory_prepare("store", WALLET, {"content": "hello"}))
    assert out["message_to_sign"].startswith("Wicked Memory auth\nwallet: " + WALLET.lower())
    assert out["request"] == {"method": "POST", "path": "/memories"}
    assert len(out["nonce"]) == 32
    bad = asyncio.run(server.memory_prepare("search", WALLET, {}))
    assert bad["http_status"] == 400


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_sent_request_is_the_signed_request(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["method"], seen["url"], seen["body"], seen["headers"] = request.method, request.url, request.content, request.headers
        return httpx.Response(201, json={"data": {"id": MID}})

    _patch_client(monkeypatch, handler)
    params = {"content": "héllo wörld", "tags": ["a"]}
    prepared = asyncio.run(server.memory_prepare("store", WALLET, params))
    out = asyncio.run(
        server.memory_store(WALLET, "héllo wörld", prepared["timestamp"], prepared["nonce"], "0xsig", tags=["a"])
    )
    assert out["http_status"] == 201
    assert seen["method"] == "POST" and seen["url"].path == "/memories"
    # the bytes the server will hash == the bytes memory_prepare hashed into the message
    assert f"body-sha256: {hashlib.sha256(seen['body']).hexdigest()}" in prepared["message_to_sign"]
    assert json.loads(seen["body"]) == {"content": "héllo wörld", "tags": ["a"]}
    assert seen["headers"]["x-wallet-signature"] == "0xsig"
    assert seen["headers"]["x-wallet-nonce"] == prepared["nonce"]


def test_search_path_on_the_wire_matches_signed_path(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["raw_path"] = request.url.raw_path.decode()
        seen["headers"] = request.headers
        return httpx.Response(402, json={"error": "pay"}, headers={"payment-required": "e30="})

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr(server, "MEMORY_API_KEY", "")
    args = dict(q="what db & why?", tags=["a b", "c"], limit=3)
    prepared = asyncio.run(server.memory_prepare("search", WALLET, args))
    out = asyncio.run(server.memory_search(WALLET, timestamp=prepared["timestamp"], nonce=prepared["nonce"], signature="0xs", **args))
    assert seen["raw_path"] == prepared["request"]["path"]
    assert f"path: {seen['raw_path']}" in prepared["message_to_sign"]
    assert out["http_status"] == 402 and out["payment_required"] == {}
    assert "x-api-key" not in seen["headers"]


def test_search_sends_api_key_and_payment_signature(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["headers"] = request.headers
        return httpx.Response(200, json={"data": []})

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr(server, "MEMORY_API_KEY", "k123")
    asyncio.run(server.memory_search(WALLET, q="x", timestamp="1", nonce="n" * 16, signature="0xs", payment_signature="PAY"))
    assert seen["headers"]["x-api-key"] == "k123"
    assert seen["headers"]["payment-signature"] == "PAY"


def test_all_memory_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"memory_prepare", "memory_store", "memory_search", "memory_get", "memory_update", "memory_history", "memory_delete", "memory_deletions"} <= names
