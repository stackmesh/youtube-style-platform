import logging
import time

from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine
from app.infrastructure.redis import HEARTBEAT_KEY

logger = logging.getLogger(__name__)


def postgres_reachable() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("worker could not reach postgres")
        return False
    return True


def write_heartbeat(client: Redis) -> None:
    client.set(HEARTBEAT_KEY, str(time.time()), ex=get_settings().worker_heartbeat_ttl_seconds)
