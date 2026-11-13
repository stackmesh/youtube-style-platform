import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.channels import router as channels_router
from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.moderation import router as moderation_router
from app.api.playback import router as playback_router
from app.api.playlists import router as playlists_router
from app.api.search import router as search_router
from app.api.social import router as social_router
from app.api.uploads import router as uploads_router
from app.api.videos import router as videos_router
from app.core.config import get_settings
from app.core.exceptions import AppError, error_payload
from app.core.logging import configure_logging, request_id_var
from app.core.middleware import BodyLimitMiddleware, RequestIdMiddleware, TrustedHostMiddleware
from app.infrastructure.storage import assert_private_bucket

logger = logging.getLogger(__name__)
_ADMIN_ENV_VARS = (
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "S3_ADMIN_ACCESS_KEY",
    "S3_ADMIN_SECRET_KEY",
)


def refuse_root_credentials() -> None:
    present = [name for name in _ADMIN_ENV_VARS if os.environ.get(name)]
    if present:
        logger.error("refusing to start because storage admin credentials are in the environment")
        raise SystemExit(1)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    refuse_root_credentials()
    settings = get_settings()
    if settings.storage_startup_check:
        assert_private_bucket()
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    docs_url = "/docs" if settings.docs_enabled else None
    redoc_url = "/redoc" if settings.docs_enabled else None
    openapi_url = "/openapi.json" if settings.docs_enabled else None
    app = FastAPI(
        title="Video platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    app.add_middleware(TrustedHostMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(channels_router)
    app.include_router(videos_router)
    app.include_router(uploads_router)
    app.include_router(playback_router)
    app.include_router(social_router)
    app.include_router(playlists_router)
    app.include_router(moderation_router)
    app.include_router(images_router)
    app.include_router(search_router)
    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, request_id_var.get(), exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = "Not found." if exc.status_code == 404 else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message, request_id_var.get()),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "issue": str(error["type"]),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_ERROR",
                "Request validation failed.",
                request_id_var.get(),
                details,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, _exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        request_id = request_id_var.get() or request.scope.get("state", {}).get("request_id")
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "INTERNAL_ERROR",
                "Something went wrong.",
                request_id,
            ),
        )


app = create_app()
