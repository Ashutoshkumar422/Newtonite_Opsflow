"""Database engine and session management.

Sessions are created per request (see app.api.deps.get_db). Services control transaction
boundaries explicitly: a request that mutates state commits exactly once, so the business
change, its audit record, its outbox event and its idempotency record are atomic.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.database_url,
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_pre_ping=True,
            # PostgreSQL's JIT compiler targets long analytical queries. For short OLTP queries whose
            # cost estimate crosses jit_above_cost (e.g. per-team counts) it adds ~50-90 ms of
            # compilation to a few-ms query, so it is disabled for application connections.
            connect_args={"options": "-c jit=off"} if s.db_disable_jit else {},
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_session() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
