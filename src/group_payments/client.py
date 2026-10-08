
import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from urllib.parse import quote

import httpx

from group_payments.errors import NetworkError, error_from_response

VERSION = "0.1.2"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def retry_delay(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None and (ra := resp.headers.get("Retry-After")):
        try:
            return max(0.0, min(float(ra), 30.0))
        except ValueError:
            pass
    return min(0.5 * 2 ** (attempt - 1), 8.0)


def _camel(d: dict[str, Any] | None) -> dict[str, Any]:
    out = {}
    for k, v in (d or {}).items():
        if "[" in k:
            out[k] = v
            continue
        head, *rest = k.split("_")
        out[head + "".join(p[:1].upper() + p[1:] for p in rest)] = v
    return out


def _clean(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in _camel(params).items() if v is not None}


def _p(x: Any) -> str:
    return quote(str(x), safe="")


class _Base:
    request: Callable[..., Any]

    def __init__(self, api_key: str, base_url: str | None, max_attempts: int) -> None:
        if not api_key.startswith(("gp_test_", "gp_live_")):
            raise ValueError("api_key must start with gp_test_ or gp_live_")
        self._key = api_key
        url = base_url or os.environ.get("GP_BASE_URL")
        if not url:
            raise ValueError("base_url is required (or set GP_BASE_URL)")
        self.base_url = url.rstrip("/")
        self.max_attempts = max_attempts
        self.payments, self.refunds, self.payment_links = Payments(self), Refunds(self), PaymentLinks(self)
        self.payouts, self.balance, self.events, self.test = Payouts(self), Balance(self), Events(self), Sandbox(self)

    def _headers(self, method: str, idempotency_key: str | None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._key}", "Accept": "application/json",
             "User-Agent": f"group-payments-python/{VERSION}"}
        if method == "POST":
            h["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        return h


class Client(_Base):
    def __init__(self, api_key: str, *, base_url: str | None = None, timeout: float = 30.0, max_attempts: int = 3,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], Any] = time.sleep) -> None:
        super().__init__(api_key, base_url, max_attempts)
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._sleep = sleep

    def request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
                idempotency_key: str | None = None) -> Any:
        headers = self._headers(method, idempotency_key)
        for attempt in range(1, self.max_attempts + 1):
            resp = None
            try:
                resp = self._http.request(method, self.base_url + path, json=json, params=_clean(params),
                                          headers=headers)
            except httpx.TransportError as e:
                if attempt == self.max_attempts:
                    raise NetworkError(str(e) or type(e).__name__, code="network_error") from e
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else None
                if resp.status_code not in RETRY_STATUSES or attempt == self.max_attempts:
                    raise error_from_response(resp)
            self._sleep(retry_delay(attempt, resp))
        raise AssertionError("unreachable")

    def paginate(self, path: str, **params: Any) -> Iterator[Any]:
        while True:
            page = self.request("GET", path, params=params)
            yield from page["items"]
            if not page.get("nextCursor"):
                return
            params = {**params, "cursor": page["nextCursor"]}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class AsyncClient(_Base):
    def __init__(self, api_key: str, *, base_url: str | None = None, timeout: float = 30.0, max_attempts: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        super().__init__(api_key, base_url, max_attempts)
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._sleep = sleep

    async def request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
                      idempotency_key: str | None = None) -> Any:
        headers = self._headers(method, idempotency_key)
        for attempt in range(1, self.max_attempts + 1):
            resp = None
            try:
                resp = await self._http.request(method, self.base_url + path, json=json, params=_clean(params),
                                                headers=headers)
            except httpx.TransportError as e:
                if attempt == self.max_attempts:
                    raise NetworkError(str(e) or type(e).__name__, code="network_error") from e
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else None
                if resp.status_code not in RETRY_STATUSES or attempt == self.max_attempts:
                    raise error_from_response(resp)
            await self._sleep(retry_delay(attempt, resp))
        raise AssertionError("unreachable")

    async def paginate(self, path: str, **params: Any) -> AsyncIterator[Any]:
        while True:
            page = await self.request("GET", path, params=params)
            for item in page["items"]:
                yield item
            if not page.get("nextCursor"):
                return
            params = {**params, "cursor": page["nextCursor"]}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()


class _Resource:

    def __init__(self, c: _Base) -> None:
        self._c = c


class Payments(_Resource):
    def create(self, *, idempotency_key: str | None = None, **body: Any) -> Any:
        return self._c.request("POST", "/payments", json=_camel(body), idempotency_key=idempotency_key)

    def get(self, payment_id: str) -> Any:
        return self._c.request("GET", f"/payments/{_p(payment_id)}")

    def list(self, **params: Any) -> Any:
        return self._c.request("GET", "/payments", params=params)

    def cancel(self, payment_id: str, *, idempotency_key: str | None = None) -> Any:
        return self._c.request("POST", f"/payments/{_p(payment_id)}/cancel", idempotency_key=idempotency_key)

    def refund(self, payment_id: str, *, idempotency_key: str | None = None, **body: Any) -> Any:
        return self._c.request("POST", f"/payments/{_p(payment_id)}/refunds", json=_camel(body),
                               idempotency_key=idempotency_key)


class Refunds(_Resource):
    def get(self, refund_id: str) -> Any:
        return self._c.request("GET", f"/refunds/{_p(refund_id)}")

    def list(self, **params: Any) -> Any:
        return self._c.request("GET", "/refunds", params=params)


class PaymentLinks(_Resource):
    def create(self, *, idempotency_key: str | None = None, **body: Any) -> Any:
        return self._c.request("POST", "/payment-links", json=_camel(body), idempotency_key=idempotency_key)

    def get(self, link_id: str) -> Any:
        return self._c.request("GET", f"/payment-links/{_p(link_id)}")

    def list(self, **params: Any) -> Any:
        return self._c.request("GET", "/payment-links", params=params)

    def deactivate(self, link_id: str, *, idempotency_key: str | None = None) -> Any:
        return self._c.request("POST", f"/payment-links/{_p(link_id)}/deactivate", idempotency_key=idempotency_key)


class Payouts(_Resource):
    def create(self, *, idempotency_key: str | None = None, **body: Any) -> Any:
        return self._c.request("POST", "/payouts", json=_camel(body), idempotency_key=idempotency_key)

    def create_batch(self, items: list[dict[str, Any]], *, idempotency_key: str | None = None) -> Any:
        return self._c.request("POST", "/payouts/batch", json={"items": [_camel(i) for i in items]},
                               idempotency_key=idempotency_key)

    def get(self, payout_id: str) -> Any:
        return self._c.request("GET", f"/payouts/{_p(payout_id)}")

    def get_batch(self, batch_id: str) -> Any:
        return self._c.request("GET", f"/payouts/batch/{_p(batch_id)}")

    def list(self, **params: Any) -> Any:
        return self._c.request("GET", "/payouts", params=params)

    def fees(self) -> Any:
        return self._c.request("GET", "/payouts/fees")

    def rates(self) -> Any:
        return self._c.request("GET", "/payouts/rates")


class Balance(_Resource):
    def get(self) -> Any:
        return self._c.request("GET", "/balance")

    def transactions(self, **params: Any) -> Any:
        return self._c.request("GET", "/balance/transactions", params=params)


class Events(_Resource):
    def get(self, event_id: str) -> Any:
        return self._c.request("GET", f"/events/{_p(event_id)}")

    def list(self, **params: Any) -> Any:
        return self._c.request("GET", "/events", params=params)

    def resend(self, event_id: str, endpoint_id: str | None = None) -> Any:
        return self._c.request("POST", f"/events/{_p(event_id)}/resend",
                               json={"endpointId": endpoint_id} if endpoint_id else {})


class Sandbox(_Resource):
    def trigger(self, event: str) -> Any:
        return self._c.request("POST", "/test/trigger", json={"event": event})
