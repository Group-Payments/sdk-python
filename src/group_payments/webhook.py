
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any


class WebhookError(Exception):
    pass


def _hex(secret: str, data: bytes) -> str:
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def verify(body: bytes | str, headers: Mapping[str, str], secret: str, tolerance: int = 300,
           now: float | None = None) -> dict[str, Any]:
    raw = body.encode() if isinstance(body, str) else body
    h = {k.lower(): v for k, v in headers.items()}
    if v2 := h.get("gp-signature-v2"):
        parts = [p.split("=", 1) for p in v2.split(",") if "=" in p]
        ts = next((v for k, v in parts if k == "t"), "")
        sigs = [v for k, v in parts if k == "v1"]
        if not (ts.isascii() and ts.isdigit()) or not sigs:
            raise WebhookError("Malformed GP-Signature-V2 header")
        if abs((time.time() if now is None else now) - int(ts)) > tolerance:
            raise WebhookError("Timestamp is outside the tolerance window")
        expected = _hex(secret, ts.encode() + b"." + raw)
        if not any(hmac.compare_digest(expected.encode(), s.encode()) for s in sigs):
            raise WebhookError("Signature mismatch")
    elif v1 := h.get("gp-signature"):
        if not hmac.compare_digest(_hex(secret, raw).encode(), v1.encode()):
            raise WebhookError("Signature mismatch")
    else:
        raise WebhookError("No signature headers")
    return json.loads(raw)


def sign(body: bytes, secret: str, t: int | None = None) -> dict[str, str]:
    t = int(time.time()) if t is None else t
    return {"GP-Signature": _hex(secret, body), "GP-Signature-V2": f"t={t},v1={_hex(secret, f'{t}.'.encode() + body)}"}
