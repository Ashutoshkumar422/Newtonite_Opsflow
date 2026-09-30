#!/bin/sh
# Container entrypoint. Roles: api | worker | migrate | seed | test
set -e

wait_for_db() {
  python - <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.core.config import get_settings
url = get_settings().database_url
for attempt in range(60):
    try:
        with create_engine(url).connect() as c:
            c.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print(f"waiting for database ({attempt + 1}/60): {e.__class__.__name__}", flush=True)
        time.sleep(1)
sys.exit("database never became available")
PY
}

case "$1" in
  api)
    wait_for_db
    alembic upgrade head
    if [ "${OPSFLOW_SEED_ON_START:-false}" = "true" ]; then python -m app.seed; fi
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers "${OPSFLOW_WEB_WORKERS:-2}"
    ;;
  worker)
    wait_for_db
    exec python -m app.worker
    ;;
  migrate)
    wait_for_db
    exec alembic upgrade head
    ;;
  seed)
    wait_for_db
    shift
    exec python -m app.seed "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
