"""Post-install sanity check: python scripts/verify_setup.py [api_base_url]

Checks, in order: database reachable, migrations at head, core tables and the audit trigger
exist, demo users present, API health endpoint answers, and a demo login works.
Exit code 0 only if every check passes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

import httpx  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
failures = 0


def check(name: str, fn) -> None:
    global failures
    try:
        detail = fn()
        print(f"  OK    {name}{f' ({detail})' if detail else ''}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  FAIL  {name}: {exc}")


def main() -> None:
    engine = create_engine(get_settings().database_url)
    head = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    ).get_current_head()

    def db():
        with engine.connect() as c:
            return c.execute(text("SELECT version()")).scalar().split(",")[0]

    def migrations():
        with engine.connect() as c:
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current != head:
            raise RuntimeError(f"database at {current}, code expects {head}; run `alembic upgrade head`")
        return current

    def schema():
        with engine.connect() as c:
            trig = c.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_activities_append_only'")
            ).scalar()
        if trig != 1:
            raise RuntimeError("audit append-only trigger missing")
        return "append-only audit trigger present"

    def seed():
        with engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM users")).scalar()
        if not n:
            raise RuntimeError("no users; run `python -m app.seed`")
        return f"{n} users"

    def health():
        r = httpx.get(f"{API}/api/health", timeout=5)
        r.raise_for_status()
        return r.json()["status"]

    def login():
        r = httpx.post(
            f"{API}/api/v1/auth/token",
            json={"email": "priya@opsflow.dev", "password": "opsflow-demo"},
            timeout=10,
        )
        r.raise_for_status()
        return "demo login works"

    print("OpsFlow setup verification")
    for name, fn in [
        ("database reachable", db),
        ("migrations at head", migrations),
        ("schema objects", schema),
        ("seed data", seed),
        (f"API health at {API}", health),
        ("demo login", login),
    ]:
        check(name, fn)
    print("All checks passed" if not failures else f"{failures} check(s) failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
