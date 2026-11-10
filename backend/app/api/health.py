import logging
import time
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine
from app.infrastructure.redis import HEARTBEAT_KEY, redis_client
from app.infrastructure.storage import check_bucket

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])
CheckName = Literal["ok", "fail"]


class LiveResponse(BaseModel):
    status: Literal["live"]


class ReadyChecks(BaseModel):
    postgres: CheckName
    redis: CheckName
    s3: CheckName
    worker: CheckName


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: ReadyChecks


def check_postgres() -> CheckName:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("postgres readiness check failed")
        return "fail"
    return "ok"


def check_redis() -> CheckName:
    try:
        if redis_client().ping() is not True:
            return "fail"
    except Exception:
        logger.warning("redis readiness check failed")
        return "fail"
    return "ok"


def check_worker() -> CheckName:
    settings = get_settings()
    try:
        raw = redis_client().get(HEARTBEAT_KEY)
    except Exception:
        logger.warning("worker heartbeat read failed")
        return "fail"
    if raw is None:
        return "fail"
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        seen_at = float(raw)
    except (TypeError, ValueError):
        return "fail"
    age = time.time() - seen_at
    if age > settings.worker_heartbeat_max_age_seconds:
        return "fail"
    return "ok"


def readiness() -> ReadyResponse:
    checks = ReadyChecks(
        postgres=check_postgres(),
        redis=check_redis(),
        s3=check_bucket(),
        worker=check_worker(),
    )
    ready = all(value == "ok" for value in checks.model_dump().values())
    return ReadyResponse(status="ready" if ready else "not_ready", checks=checks)


@router.get("/health/live", response_model=LiveResponse)
def live() -> LiveResponse:
    return LiveResponse(status="live")


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
def ready() -> JSONResponse:
    body = readiness()
    status_code = 200 if body.status == "ready" else 503
    return JSONResponse(status_code=status_code, content=body.model_dump())
