import logging
import signal
import socket
import threading
import time

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.infrastructure.redis import redis_client
from app.infrastructure.storage import check_bucket
from app.uploads.media import claim_next_job, execute_claimed_job
from app.uploads.service import sweep_uploads
from app.worker.heartbeat import postgres_reachable, write_heartbeat

logger = logging.getLogger(__name__)
_SWEEP_SECONDS = 60


def main() -> None:
    configure_logging()
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()
        logger.info("worker shutdown requested")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if not postgres_reachable():
        raise SystemExit(1)
    if check_bucket() != "ok":
        logger.error("worker could not reach object storage")
        raise SystemExit(1)

    threading.Thread(target=_heartbeat_loop, args=(stop,), daemon=True).start()
    worker_id = socket.gethostname()
    logger.info("worker started")
    last_sweep = 0.0
    while not stop.is_set():
        now = time.monotonic()
        if now - last_sweep >= _SWEEP_SECONDS:
            _sweep()
            last_sweep = now
        if _claim_and_run(worker_id):
            continue
        stop.wait(1)
    logger.info("worker stopped")


def _heartbeat_loop(stop: threading.Event) -> None:
    client = redis_client()
    while not stop.is_set():
        try:
            write_heartbeat(client)
            logger.info("worker heartbeat updated")
        except Exception:
            logger.warning("worker heartbeat update failed")
        stop.wait(15)


def _sweep() -> None:
    session = SessionLocal()
    try:
        sweep_uploads(session)
    except Exception:
        session.rollback()
        logger.warning("upload sweep failed")
    finally:
        session.close()


def _claim_and_run(worker_id: str) -> bool:
    session = SessionLocal()
    try:
        job_id = claim_next_job(session, worker_id)
    except Exception:
        session.rollback()
        logger.warning("processing claim failed")
        return False
    finally:
        session.close()
    if job_id is None:
        return False
    session = SessionLocal()
    try:
        execute_claimed_job(session, job_id)
    except Exception:
        session.rollback()
        logger.warning("processing job failed")
    finally:
        session.close()
    return True


if __name__ == "__main__":
    main()
