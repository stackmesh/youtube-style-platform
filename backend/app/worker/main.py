import logging
import signal
import time

from app.core.logging import configure_logging
from app.infrastructure.redis import redis_client
from app.infrastructure.storage import check_bucket
from app.worker.heartbeat import postgres_reachable, write_heartbeat

logger = logging.getLogger(__name__)
_INTERVAL_SECONDS = 15


def main() -> None:
    configure_logging()
    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True
        logger.info("worker shutdown requested")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if not postgres_reachable():
        raise SystemExit(1)
    if check_bucket() != "ok":
        logger.error("worker could not reach object storage")
        raise SystemExit(1)

    client = redis_client()
    logger.info("worker started")
    while not stop:
        try:
            write_heartbeat(client)
            logger.info("worker heartbeat updated")
        except Exception:
            logger.warning("worker heartbeat update failed")
        for _ in range(_INTERVAL_SECONDS):
            if stop:
                break
            time.sleep(1)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
