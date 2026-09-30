"""Test fixtures.

Tests run against a real PostgreSQL database (OPSFLOW_TEST_DATABASE_URL), because the behaviours
under test — row locks, compare-and-set updates, unique-index blocking, CHECK constraints,
triggers, SKIP LOCKED — are database semantics that SQLite would not reproduce.

The schema is created by running the real Alembic migration (so the migration is tested too).
Each test starts from empty tables (TRUNCATE), and data is committed for real so that several
connections/threads can observe each other, exactly as in production.
"""

import os

TEST_DB = os.environ.get(
    "OPSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://opsflow:opsflow@localhost:5432/opsflow_test",
)
os.environ["OPSFLOW_DATABASE_URL"] = TEST_DB
os.environ.setdefault("OPSFLOW_JWT_SECRET", "test-secret-not-for-production-use-0123456789")

import itertools  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine, session_factory  # noqa: E402
from app.domain.workflow import Priority  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Team, TeamMembership, User, WorkItem  # noqa: E402
from app.schemas import WorkItemCreate  # noqa: E402
from app.services import sessions as sessions_svc  # noqa: E402
from app.services import work_items as svc  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLES = (
    "notifications, outbox_events, idempotency_keys, comments, activities, "
    "security_events, auth_throttle, sessions, work_items, team_memberships, teams, users"
)
PASSWORD = "correct-horse-battery"
_PW_HASH = hash_password(PASSWORD)
_seq = itertools.count(1)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = TEST_DB
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


class Api:
    """Thin wrapper: api.post(user, path, json=...) authenticates as `user` via bearer token."""

    def __init__(self) -> None:
        self.client = TestClient(app, raise_server_exceptions=False)
        self._tokens = {}

    def token(self, user: User) -> str:
        """A real server-side session per (client, user), exactly like a login would create."""
        if user.id not in self._tokens:
            self._tokens[user.id] = issue_token(user)
        return self._tokens[user.id]

    _tokens: dict[int, str]

    def headers(self, user: User | None, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token(user)}"} if user else {}
        h.update(extra or {})
        return h

    def request(self, method: str, user: User | None, path: str, **kw: Any):
        headers = self.headers(user, kw.pop("headers", None))
        return self.client.request(method, "/api/v1" + path, headers=headers, **kw)

    def get(self, user, path, **kw):
        return self.request("GET", user, path, **kw)

    def post(self, user, path, **kw):
        return self.request("POST", user, path, **kw)

    def patch(self, user, path, **kw):
        return self.request("PATCH", user, path, **kw)

    def put(self, user, path, **kw):
        return self.request("PUT", user, path, **kw)

    def delete(self, user, path, **kw):
        return self.request("DELETE", user, path, **kw)


def issue_token(user: User, method: str = "api_token") -> str:
    with session_factory()() as s:
        db_user = s.get(User, user.id)
        assert db_user is not None
        _, token = sessions_svc.create_session(s, db_user, method, ip="127.0.0.1", user_agent="pytest")
        s.commit()
        return token


@pytest.fixture
def api() -> Api:
    return Api()


class World:
    """Factory for users/teams/items committed to the database."""

    def user(self, name: str | None = None, *, admin: bool = False) -> User:
        n = next(_seq)
        name = name or f"user{n}"
        with session_factory()() as s:
            u = User(
                email=f"{name.lower().replace(' ', '.')}.{n}@test.dev",
                full_name=name,
                password_hash=_PW_HASH,
                is_admin=admin,
                is_active=True,
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            s.expunge(u)
            return u

    def team(self, key: str | None = None, *, manager: User | None = None, members: list[User] = ()) -> Team:  # type: ignore[assignment]
        n = next(_seq)
        key = key or f"T{n}"
        with session_factory()() as s:
            t = Team(key=key, name=f"Team {key}", description="", item_seq=0)
            s.add(t)
            s.flush()
            if manager:
                s.add(TeamMembership(team_id=t.id, user_id=manager.id, role="manager"))
            for m in members:
                s.add(TeamMembership(team_id=t.id, user_id=m.id, role="member"))
            s.commit()
            s.refresh(t)
            s.expunge(t)
            return t

    def add(self, team: Team, user: User, role: str = "member") -> None:
        with session_factory()() as s:
            s.add(TeamMembership(team_id=team.id, user_id=user.id, role=role))
            s.commit()

    def item(
        self,
        team: Team,
        reporter: User,
        *,
        title: str = "Something needs attention",
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        assignee: User | None = None,
    ) -> WorkItem:
        with session_factory()() as s:
            user = s.get(User, reporter.id)
            assert user is not None
            item, _ = svc.create_item(
                s,
                user,
                WorkItemCreate(
                    team_id=team.id,
                    title=title,
                    description=description,
                    priority=priority,
                    assignee_id=assignee.id if assignee else None,
                ),
            )
            s.commit()
            s.refresh(item)
            s.expunge_all()
            return item

    def fetch(self, item_id: int) -> WorkItem:
        with session_factory()() as s:
            item = s.get(WorkItem, item_id)
            assert item is not None
            s.expunge_all()
            return item

    def scalar(self, sql: str, **params: Any) -> Any:
        with get_engine().connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def execute(self, sql: str, **params: Any) -> None:
        """Committed raw SQL — used to set up states the API deliberately forbids."""
        with get_engine().begin() as conn:
            conn.execute(text(sql), params)


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def ops(world: World):
    """A typical team: manager, two members, an outsider in another team, and an admin."""
    manager = world.user("Mia Manager")
    alice = world.user("Alice Member")
    bob = world.user("Bob Member")
    outsider = world.user("Oscar Outsider")
    admin = world.user("Ada Admin", admin=True)
    team = world.team("OPS", manager=manager, members=[alice, bob])
    other = world.team("FIN", manager=outsider)
    return type(
        "Ops",
        (),
        dict(
            manager=manager,
            alice=alice,
            bob=bob,
            outsider=outsider,
            admin=admin,
            team=team,
            other=other,
        ),
    )
