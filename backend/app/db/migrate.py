import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)
_LOCK_ID = 84_215_045


def upgrade_head() -> None:
    settings = get_settings()
    cfg = Config("alembic.ini")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _LOCK_ID})
        connection.commit()
        try:
            logger.info("applying database migrations")
            command.upgrade(cfg, "head")
            logger.info("database migrations applied")
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _LOCK_ID})
            connection.commit()
    engine.dispose()


def main() -> None:
    configure_logging()
    upgrade_head()


if __name__ == "__main__":
    main()
