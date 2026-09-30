"""Outbox worker process: `python -m app.worker`.

Runs independently of the API. Several instances can run concurrently (SKIP LOCKED).
"""

import logging
import signal
import time

from app.core.config import get_settings
from app.db.session import session_factory
from app.services.idempotency import purge_expired
from app.services.outbox import process_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("opsflow.worker")

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = get_settings()
    factory = session_factory()
    log.info("outbox worker started (poll=%ss)", settings.worker_poll_seconds)
    last_purge = 0.0
    while _running:
        try:
            n = process_batch(factory)
            if time.monotonic() - last_purge > 3600:
                with factory() as db:
                    purged = purge_expired(db)
                    db.commit()
                log.info("purged %s expired idempotency keys", purged)
                last_purge = time.monotonic()
        except Exception:  # noqa: BLE001 — keep the loop alive (e.g. DB restart)
            log.exception("worker iteration failed; retrying")
            n = 0
        if n == 0:
            time.sleep(settings.worker_poll_seconds)
    log.info("outbox worker stopped")


if __name__ == "__main__":
    main()
