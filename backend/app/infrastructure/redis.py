import redis

from app.core.config import get_settings

HEARTBEAT_KEY = "worker:heartbeat"


def redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
