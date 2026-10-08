import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from group_payments import AsyncClient, Client, errors, webhook

KEY = "gp_test_abcdefgh_" + "x" * 32


def client(handler, **kw):
    sleeps: list[float] = []
    c = Client(KEY, base_url="https://api.test/v1", transport=httpx.MockTransport(handler), sleep=sleeps.append, **kw)
    return c, sleeps


def test_post_gets_one_idempotency_key_reused_across_retries():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(500 if len(seen) == 1 else 201, json={"id": "p1"})

    c, sleeps = client(handler)
    assert c.payments.create(amount="10.00", description="d", order_id="o-1", expires_in_minutes=30) == {"id": "p1"}
    keys = {r.headers["Idempotency-Key"] for r in seen}
    assert len(seen) == 2 and len(keys) == 1 and sleeps == [0.5]
    assert json.loads(seen[0].content) == {"amount": "10.00", "description": "d", "orderId": "o-1", "expiresInMinutes": 30}
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"


def test_retry_after_and_attempt_limit():
    def handler(req):
        return httpx.Response(429, headers={"Retry-After": "2", "GP-Request-Id": "req_x"},
                              json={"error": {"code": "rate_limited", "message": "Too many"}})

    c, sleeps = client(handler)
    with pytest.raises(errors.RateLimitError) as e:
        c.balance.get()
    assert sleeps == [2.0, 2.0] and e.value.code == "rate_limited" and e.value.request_id == "req_x"


def test_network_errors_are_retried_then_raised():
    def handler(req):
        raise httpx.ConnectError("boom")

    c, sleeps = client(handler)
    with pytest.raises(errors.NetworkError):
        c.payments.list(limit=1)
    assert sleeps == [0.5, 1.0]


def test_typed_errors_and_get_has_no_idempotency_key():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Payment not found",
                                                   "requestId": "req_abc", "docUrl": "https://x/docs/errors#not_found"}})

    c, sleeps = client(handler)
    with pytest.raises(errors.NotFoundError) as e:
        c.payments.get("p 1")
    assert (e.value.status, e.value.code, e.value.request_id) == (404, "not_found", "req_abc")
    assert seen[0].url.raw_path == b"/v1/payments/p%201" and "Idempotency-Key" not in seen[0].headers and sleeps == []


def test_paginate_follows_cursor_and_params_are_camel_case():
    pages = {None: {"items": [1, 2], "nextCursor": "c2"}, "c2": {"items": [3], "nextCursor": None}}
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=pages[req.url.params.get("cursor")])

    c, _ = client(handler)
    assert list(c.paginate("/payments", order_id="o", limit=2, **{"metadata[order_no]": "1"})) == [1, 2, 3]
    assert seen[0] == {"orderId": "o", "limit": "2", "metadata[order_no]": "1"}


def test_bad_retry_after_and_network_error_code():
    c, sleeps = client(lambda req: httpx.Response(503, headers={"Retry-After": "-5"}))
    with pytest.raises(errors.ServerError):
        c.balance.get()
    assert sleeps == [0.0, 0.0]

    def down(req):
        raise httpx.ConnectError("boom")

    c, _ = client(down, max_attempts=1)
    with pytest.raises(errors.NetworkError) as e:
        c.balance.get()
    assert e.value.code == "network_error"


async def test_async_client():
    calls = []

    async def asleep(s):
        calls.append(s)

    def handler(req):
        return httpx.Response(503) if not calls else httpx.Response(200, json={"balance": "1.00"})

    c = AsyncClient(KEY, base_url="https://api.test/v1", transport=httpx.MockTransport(handler), sleep=asleep)
    assert (await c.balance.get()) == {"balance": "1.00"} and calls == [0.5]
    await c.aclose()


def _v2(body: bytes, secrets: list[str], t: int) -> dict[str, str]:
    sigs = ",".join(f"v1={hmac.new(s.encode(), f'{t}.'.encode() + body, hashlib.sha256).hexdigest()}" for s in secrets)
    return {"GP-Signature-V2": f"t={t},{sigs}"}


def test_webhook_verify():
    body = b'{"event":"payment.succeeded","payment":{"id":"p"}}'
    assert webhook.verify(body, _v2(body, ["new", "old"], 1000), "old", now=1100)["event"] == "payment.succeeded"
    with pytest.raises(webhook.WebhookError):
        webhook.verify(body, _v2(body, ["new"], 1000), "new", now=1301)
    with pytest.raises(webhook.WebhookError):
        webhook.verify(body + b" ", _v2(body, ["new"], 1000), "new", now=1000)
    v1 = {"gp-signature": hmac.new(b"s", body, hashlib.sha256).hexdigest()}
    assert webhook.verify(body, v1, "s")["payment"] == {"id": "p"}
    signed = webhook.sign(body, "s", t=5)
    assert webhook.verify(body, signed, "s", now=5) and set(signed) == {"GP-Signature", "GP-Signature-V2"}


def test_backend_signature_vector():
    v = json.loads((Path(__file__).parent / "webhook-vector.json").read_text(encoding="utf-8"))
    body = v["body"].encode()
    for secret in v["secrets"]:
        assert webhook.verify(body, v["headers"], secret, now=v["t"])["payment"]["description"] == "Заказ №1042"
    assert webhook.verify(body, {"GP-Signature": v["headers"]["GP-Signature"]}, v["secrets"][0])
    signed = webhook.sign(body, v["secrets"][0], t=v["t"])
    assert signed["GP-Signature"] == v["headers"]["GP-Signature"]
    assert v["headers"]["GP-Signature-V2"].startswith(signed["GP-Signature-V2"] + ",")
    for bad in ({"GP-Signature-V2": "t=¹⁷,v1=x"}, {"GP-Signature-V2": f"t={v['t']},v1=подпись"}):
        with pytest.raises(webhook.WebhookError):
            webhook.verify(body, bad, v["secrets"][0], now=v["t"])


def test_rejects_malformed_keys():
    with pytest.raises(ValueError):
        Client("sk_live_123")
