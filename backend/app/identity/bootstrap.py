import logging

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.identity.service import ensure_bootstrap_admin

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    session = SessionLocal()
    try:
        ensure_bootstrap_admin(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
