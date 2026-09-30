"""Development seed data. DEVELOPMENT ONLY — never run against production.

    python -m app.seed               # seed if empty
    python -m app.seed --reset       # wipe and reseed
    python -m app.seed --bulk 20000  # additionally generate N synthetic items (scale testing)

All demo users share the password in DEMO_PASSWORD. Items are created through the real service
layer, so activity history, versions and invariants are exactly what the app would produce.
"""

import argparse
import random

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import session_factory
from app.domain.workflow import Priority, Status
from app.models import Team, TeamMembership, User, WorkItem
from app.schemas import WorkItemCreate
from app.services import work_items as svc

DEMO_PASSWORD = "opsflow-demo"  # development-only credential

USERS = [
    # email, name, admin
    ("admin@opsflow.dev", "Ada Admin", True),
    ("priya@opsflow.dev", "Priya Raman", False),
    ("daniel@opsflow.dev", "Daniel Kim", False),
    ("lena@opsflow.dev", "Lena Fischer", False),
    ("omar@opsflow.dev", "Omar Haddad", False),
    ("grace@opsflow.dev", "Grace Liu", False),
    ("tom@opsflow.dev", "Tom Becker", False),
    ("ravi@opsflow.dev", "Ravi Menon", False),
]

TEAMS = [
    ("PAY", "Payments", "Payment investigations, refunds and reconciliation"),
    ("SUP", "Customer Support", "Escalated customer issues"),
    ("ENG", "Platform Engineering", "Production incidents and engineering problems"),
    ("CMP", "Compliance", "Compliance requests and approvals"),
]

MEMBERSHIPS = {
    "PAY": {"priya": "manager", "daniel": "member", "omar": "member", "ravi": "member"},
    "SUP": {"lena": "manager", "omar": "member", "priya": "member"},
    "ENG": {"grace": "manager", "tom": "member", "daniel": "member"},
    "CMP": {"ravi": "manager", "lena": "member"},
}

ITEMS = {
    "PAY": [
        (
            "Investigate duplicate charge on order #88213",
            "Customer reports two identical card charges 3 minutes apart.",
            "high",
        ),
        (
            "Refund stuck in 'processing' for 5 days",
            "Refund initiated 5 days ago still not settled with the acquirer.",
            "critical",
        ),
        (
            "Monthly reconciliation mismatch: 1,284.50 EUR",
            "Ledger vs. PSP settlement report differs for August.",
            "high",
        ),
        (
            "Chargeback evidence for dispute DSP-4471",
            "Collect delivery proof and customer comms before the deadline.",
            "medium",
        ),
        (
            "Update FX rate source for INR payouts",
            "Current provider deprecates its v1 endpoint next quarter.",
            "low",
        ),
        (
            "Payout batch failed for 12 merchants",
            "Batch 2026-09-27 rejected with code R03 (no account).",
            "critical",
        ),
        ("Verify refund policy for partial shipments", "Support needs clarity on partial refunds.", "medium"),
        ("Suspicious velocity on merchant M-2231", "Transaction volume 8x normal in 2 hours.", "high"),
    ],
    "SUP": [
        ("Customer cannot reset password", "Reset email never arrives for enterprise SSO users.", "high"),
        ("Bulk export times out for large accounts", "Exports over 50k rows fail after 30s.", "medium"),
        ("VIP customer requests invoice correction", "VAT ID missing on last three invoices.", "medium"),
        ("Complaint about delayed delivery notifications", "Push notifications arrive hours late.", "low"),
        (
            "Escalation: account locked after MFA change",
            "Customer locked out after switching phones.",
            "critical",
        ),
        ("Translate help article on refunds to Hindi", "Requested by the India support pod.", "low"),
    ],
    "ENG": [
        ("Elevated 5xx on checkout API", "Error rate at 2.3% since deploy 4812.", "critical"),
        ("Database connection pool exhaustion at peak", "Pool saturates at 18:00 IST daily.", "high"),
        ("Rotate TLS certificate for internal gateway", "Certificate expires in 21 days.", "medium"),
        ("Flaky integration test in payouts service", "test_batch_retry fails ~5% of CI runs.", "low"),
        ("Search latency regression after index change", "p95 went from 120ms to 480ms.", "high"),
        ("Disk usage alert on log cluster", "Node 3 at 91% disk.", "medium"),
        ("Upgrade Postgres minor version", "Security patch release available.", "medium"),
    ],
    "CMP": [
        ("GDPR data export request #DSR-219", "Statutory deadline 30 days from receipt.", "high"),
        (
            "Quarterly access review for finance systems",
            "Review and approve access for 42 accounts.",
            "medium",
        ),
        ("Approve new vendor: SMS provider", "Security questionnaire received, needs sign-off.", "medium"),
        ("Sanctions screening false positive review", "Merchant flagged on name similarity.", "high"),
    ],
}


def reset(db) -> None:
    db.execute(
        text(
            "TRUNCATE notifications, outbox_events, idempotency_keys, comments, activities, security_events, "
            "auth_throttle, sessions, work_items, team_memberships, teams, users RESTART IDENTITY CASCADE"
        )
    )
    db.commit()


def seed(db, rng: random.Random) -> None:
    pw = hash_password(DEMO_PASSWORD)
    users: dict[str, User] = {}
    for email, name, admin in USERS:
        u = User(email=email, full_name=name, password_hash=pw, is_admin=admin, is_active=True)
        db.add(u)
        users[email.split("@")[0]] = u
    teams: dict[str, Team] = {}
    for key, name, desc in TEAMS:
        t = Team(key=key, name=name, description=desc, item_seq=0)
        db.add(t)
        teams[key] = t
    db.flush()
    for key, members in MEMBERSHIPS.items():
        for handle, role in members.items():
            db.add(TeamMembership(team_id=teams[key].id, user_id=users[handle].id, role=role))
    db.commit()

    for key, items in ITEMS.items():
        team = teams[key]
        members = MEMBERSHIPS[key]
        manager = users[next(h for h, r in members.items() if r == "manager")]
        people = [users[h] for h in members]
        for i, (title, desc, prio) in enumerate(items):
            reporter = rng.choice(people)
            item, _ = svc.create_item(
                db,
                reporter,
                WorkItemCreate(team_id=team.id, title=title, description=desc, priority=Priority(prio)),
            )
            db.commit()
            # Walk items through a realistic spread of states via the real workflow.
            stage = i % 6
            if stage >= 1:
                owner = rng.choice(people)
                item, _, _ = svc.claim_item(db, owner, item.id)
                db.commit()
                if stage >= 2:
                    item, _ = svc.transition_item(db, owner, item.id, Status.IN_PROGRESS, None, item.version)
                    db.commit()
                if stage == 3:
                    item, _ = svc.transition_item(
                        db, owner, item.id, Status.BLOCKED, "Waiting on an external party", item.version
                    )
                    db.commit()
                if stage >= 4:
                    item, _ = svc.transition_item(db, owner, item.id, Status.RESOLVED, None, item.version)
                    db.commit()
                if stage == 5:
                    item, _ = svc.transition_item(db, manager, item.id, Status.CLOSED, None, item.version)
                    db.commit()
            if i % 2 == 0:
                svc.add_comment(db, rng.choice(people), item.id, "Looking into this — will update shortly.")
                db.commit()
        # A couple of overdue and due-soon items (seed-only direct write; the API forbids past dates)
        ids = (
            db.execute(
                select(WorkItem.id).where(
                    WorkItem.team_id == team.id, WorkItem.status.in_(["open", "in_progress", "blocked"])
                )
            )
            .scalars()
            .all()
        )
        for n, item_id in enumerate(ids[:3]):
            db.execute(
                text("UPDATE work_items SET due_date = current_date + :d WHERE id = :id"),
                {"d": [-3, -1, 5][n], "id": item_id},
            )
        db.commit()


def bulk(db, n: int, rng: random.Random) -> None:
    """Fast synthetic load via set-based SQL (each row still satisfies every CHECK)."""
    # Only teams with members can have reporters/assignees.
    team_ids = db.execute(select(Team.id).where(Team.memberships.any())).scalars().all()
    for team_id in team_ids:
        per_team = n // len(team_ids)
        db.execute(
            text(
                """
                WITH t AS (
                    UPDATE teams SET item_seq = item_seq + :n WHERE id = :team RETURNING item_seq - :n AS base
                ),
                members AS (
                    SELECT array_agg(user_id ORDER BY user_id) AS ids FROM team_memberships WHERE team_id = :team
                ),
                ins AS (
                    INSERT INTO work_items (team_id, number, title, description, status, priority,
                                            reporter_id, assignee_id, due_date, version, created_at, updated_at)
                    SELECT :team, t.base + g,
                           'Synthetic operational task #' || g,
                           'Generated for scale testing. Keywords: ' ||
                               (ARRAY['refund','latency','invoice','outage','access review','chargeback'])[1 + g % 6],
                           s.status,
                           g % 4,
                           m.ids[1 + g % array_length(m.ids, 1)],
                           CASE WHEN s.status = 'open' AND g % 3 = 0 THEN NULL
                                ELSE m.ids[1 + (g + 1) % array_length(m.ids, 1)] END,
                           CASE WHEN g % 7 = 0 THEN current_date + (g % 30) - 10 END,
                           1,
                           now() - (g || ' minutes')::interval,
                           now() - (g || ' minutes')::interval
                    FROM generate_series(1, :n) g, t, members m,
                         LATERAL (SELECT (ARRAY['open','in_progress','blocked','resolved','closed','cancelled'])[1 + g % 6] AS status) s
                    RETURNING id, reporter_id, created_at
                )
                INSERT INTO activities (work_item_id, actor_id, action, changes, created_at)
                SELECT id, reporter_id, 'created', '{"note": "synthetic"}'::jsonb, created_at FROM ins
                """
            ),
            {"team": team_id, "n": per_team},
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    parser.add_argument("--bulk", type=int, default=0, help="also generate N synthetic work items")
    args = parser.parse_args()
    if get_settings().is_production:
        raise SystemExit("Refusing to seed a production environment")

    rng = random.Random(42)
    with session_factory()() as db:
        if args.reset:
            reset(db)
        if db.execute(select(func.count()).select_from(User)).scalar_one() == 0:
            seed(db, rng)
            print(f"Seeded demo data. Password for all demo users: {DEMO_PASSWORD}")
        else:
            print("Users already exist; skipping base seed (use --reset to reseed)")
        if args.bulk:
            bulk(db, args.bulk, rng)
            print(f"Generated {args.bulk} synthetic work items")


if __name__ == "__main__":
    main()
