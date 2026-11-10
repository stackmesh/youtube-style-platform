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
