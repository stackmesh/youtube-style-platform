import time
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.exceptions import AppError
from app.infrastructure.redis import redis_client


def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Sliding window. Redis failure closes the route instead of allowing it."""
    now = time.time()
    member = f"{now:.6f}:{uuid4().hex}"
    try:
        client = redis_client()
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        _, _, count, _ = pipe.execute()
    except RedisError as exc:
        error = AppError("SERVICE_UNAVAILABLE", "Rate limiter is unavailable.", 503)
        raise error from exc
    if int(count) > limit:
        error = AppError("RATE_LIMITED", "Too many requests.", 429)
        error.headers["Retry-After"] = str(window_seconds)
        raise error
