
from typing import Any

import httpx


class GroupPayError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 request_id: str | None = None, param: str | None = None, doc_url: str | None = None,
                 body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.request_id = status, code, request_id
        self.param, self.doc_url, self.body = param, doc_url, body or {}


class AuthenticationError(GroupPayError):
    pass


class PermissionDeniedError(GroupPayError):
    pass


class NotFoundError(GroupPayError):
    pass


class ConflictError(GroupPayError):
    pass


class InvalidRequestError(GroupPayError):
    pass


class RateLimitError(GroupPayError):
    pass


class ServerError(GroupPayError):
    pass


class NetworkError(GroupPayError):
    pass


_BY_STATUS = {400: InvalidRequestError, 401: AuthenticationError, 403: PermissionDeniedError, 404: NotFoundError,
              409: ConflictError, 410: ConflictError, 422: InvalidRequestError, 429: RateLimitError}


def error_from_response(resp: httpx.Response) -> GroupPayError:
    try:
        err = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    cls = _BY_STATUS.get(resp.status_code, ServerError if resp.status_code >= 500 else GroupPayError)
    return cls(err.get("message") or f"HTTP {resp.status_code}", status=resp.status_code, code=err.get("code"),
               request_id=err.get("requestId") or resp.headers.get("GP-Request-Id"), param=err.get("param"),
               doc_url=err.get("docUrl"), body=err)
