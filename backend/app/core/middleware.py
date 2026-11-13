import logging
import re
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import error_payload
from app.core.logging import request_id_var

logger = logging.getLogger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
JSON_BODY_LIMIT = 256 * 1024
REQUEST_BODY_LIMIT = 2_000_000


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = MutableHeaders(scope=scope)
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else str(uuid4())
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id

        token = request_id_var.set(request_id)
        status_code = 500
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status_code, started
            if message["type"] == "http.response.start":
                started = True
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            if started:
                raise
            logger.exception("unhandled error")
            response = JSONResponse(
                status_code=500,
                content=error_payload(
                    "INTERNAL_ERROR",
                    "Something went wrong.",
                    request_id,
                ),
            )
            await response(scope, receive, send_with_id)
        finally:
            logger.info(
                "request completed method=%s path=%s status=%s",
                scope.get("method", ""),
                scope.get("path", ""),
                status_code,
            )
            request_id_var.reset(token)


class BodyLimitMiddleware:
    """Stop oversized JSON and image bodies before the handler reads them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = MutableHeaders(scope=scope)
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        limit = JSON_BODY_LIMIT if content_type == "application/json" else REQUEST_BODY_LIMIT
        declared = headers.get("content-length")
        if declared is not None:
            try:
                announced = int(declared)
            except ValueError:
                announced = limit + 1
            if announced > limit:
                await _reject_body(scope, receive, send)
                return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body") or b"")
            more = bool(message.get("more_body"))
            if len(body) > limit:
                await _reject_body(scope, receive, send)
                return
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class TrustedHostMiddleware:
    """Reject requests whose Host header is outside the production allow-list.

    An empty TRUSTED_HOSTS value leaves local development and the test client unchanged.
    Health checks from inside the container use 127.0.0.1 and stay allowed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from app.core.config import get_settings

        allowed = set(get_settings().trusted_host_list)
        if not allowed:
            await self.app(scope, receive, send)
            return
        allowed.update({"127.0.0.1", "localhost"})
        headers = MutableHeaders(scope=scope)
        host = headers.get("host", "").split(":", 1)[0].strip().lower()
        if host not in allowed:
            response = JSONResponse(
                status_code=400,
                content=error_payload(
                    "UNTRUSTED_HOST",
                    "The host header is not allowed.",
                    request_id_var.get(),
                ),
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _reject_body(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=413,
        content=error_payload(
            "PAYLOAD_TOO_LARGE",
            "The request body is too large.",
            request_id_var.get(),
        ),
    )
    await response(scope, receive, send)
