"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-29
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- identity
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    email           VARCHAR(254) NOT NULL,
    full_name       VARCHAR(120) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email)),
    CONSTRAINT ck_users_name_nonempty CHECK (length(btrim(full_name)) > 0)
);
CREATE INDEX ix_users_name_trgm ON users USING gin (full_name gin_trgm_ops);

CREATE TABLE teams (
    id              BIGSERIAL PRIMARY KEY,
    key             VARCHAR(10) NOT NULL,
    name            VARCHAR(100) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    item_seq        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_teams_key UNIQUE (key),
    CONSTRAINT uq_teams_name UNIQUE (name),
    CONSTRAINT ck_teams_key_format CHECK (key ~ '^[A-Z][A-Z0-9]{1,9}$'),
    CONSTRAINT ck_teams_item_seq CHECK (item_seq >= 0)
);

CREATE TABLE team_memberships (
    team_id         BIGINT NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role            VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id),
    CONSTRAINT ck_membership_role CHECK (role IN ('manager', 'member'))
);
-- "which teams is this user in" — used on every authorization check and listing
CREATE INDEX ix_memberships_user ON team_memberships (user_id, team_id);

-- ---------------------------------------------------------------- work items
CREATE TABLE work_items (
    id              BIGSERIAL PRIMARY KEY,
    team_id         BIGINT NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    number          INTEGER NOT NULL,
    title           VARCHAR(200) NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          VARCHAR(20) NOT NULL DEFAULT 'open',
    priority        SMALLINT NOT NULL DEFAULT 1,
    reporter_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assignee_id     BIGINT REFERENCES users(id) ON DELETE RESTRICT,
    due_date        DATE,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector   TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'B')
    ) STORED,
    CONSTRAINT uq_work_items_team_number UNIQUE (team_id, number),
    CONSTRAINT ck_work_items_title_nonempty CHECK (length(btrim(title)) > 0),
    CONSTRAINT ck_work_items_status CHECK (
        status IN ('open', 'in_progress', 'blocked', 'resolved', 'closed', 'cancelled')),
    CONSTRAINT ck_work_items_priority CHECK (priority BETWEEN 0 AND 3),
    CONSTRAINT ck_work_items_version CHECK (version >= 1),
    -- Workflow invariant enforced by the database itself: owned states need an owner.
    CONSTRAINT ck_work_items_owned_states_have_assignee CHECK (
        status NOT IN ('in_progress', 'blocked', 'resolved') OR assignee_id IS NOT NULL)
);
-- Listing / keyset pagination per team, default sort
CREATE INDEX ix_work_items_team_updated ON work_items (team_id, updated_at DESC, id DESC);
CREATE INDEX ix_work_items_updated ON work_items (updated_at DESC, id DESC);
CREATE INDEX ix_work_items_created ON work_items (created_at DESC, id DESC);
CREATE INDEX ix_work_items_priority ON work_items (priority DESC, updated_at DESC, id DESC);
CREATE INDEX ix_work_items_due_sort ON work_items ((coalesce(due_date, DATE '9999-12-31')), id);
-- "My work" and assignee filters
CREATE INDEX ix_work_items_assignee_status ON work_items (assignee_id, status);
CREATE INDEX ix_work_items_reporter ON work_items (reporter_id);
-- Status breakdown per team (dashboard)
CREATE INDEX ix_work_items_team_status ON work_items (team_id, status);
-- Overdue detection only needs active items with a due date
CREATE INDEX ix_work_items_due_active ON work_items (due_date)
    WHERE due_date IS NOT NULL AND status NOT IN ('closed', 'cancelled');
-- Search
CREATE INDEX ix_work_items_search ON work_items USING gin (search_vector);
CREATE INDEX ix_work_items_title_trgm ON work_items USING gin (title gin_trgm_ops);

-- ---------------------------------------------------------------- audit + collaboration
CREATE TABLE activities (
    id              BIGSERIAL PRIMARY KEY,
    work_item_id    BIGINT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    actor_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    action          VARCHAR(40) NOT NULL,
    changes         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_activities_item ON activities (work_item_id, id DESC);

-- Audit rows are append-only. Enforced in the database, not just by the absence of an endpoint.
CREATE FUNCTION forbid_activity_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'activities are append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_activities_append_only
    BEFORE UPDATE OR DELETE ON activities
    FOR EACH ROW EXECUTE FUNCTION forbid_activity_mutation();

CREATE TABLE comments (
    id              BIGSERIAL PRIMARY KEY,
    work_item_id    BIGINT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    author_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_at       TIMESTAMPTZ,
    CONSTRAINT ck_comments_body CHECK (length(btrim(body)) BETWEEN 1 AND 10000)
);
CREATE INDEX ix_comments_item ON comments (work_item_id, id);

-- ---------------------------------------------------------------- reliability
CREATE TABLE idempotency_keys (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key             VARCHAR(255) NOT NULL,
    request_hash    CHAR(64) NOT NULL,
    method          VARCHAR(10) NOT NULL,
    path            VARCHAR(500) NOT NULL,
    response_status INTEGER,
    response_body   JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_idempotency_user_key UNIQUE (user_id, key)
);
CREATE INDEX ix_idempotency_expires ON idempotency_keys (expires_at);

CREATE TABLE outbox_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(60) NOT NULL,
    payload         JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    CONSTRAINT ck_outbox_status CHECK (status IN ('pending', 'processed', 'failed'))
);
CREATE INDEX ix_outbox_pending ON outbox_events (available_at, id) WHERE status = 'pending';

CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id        BIGINT NOT NULL REFERENCES outbox_events(id) ON DELETE CASCADE,
    work_item_id    BIGINT REFERENCES work_items(id) ON DELETE CASCADE,
    kind            VARCHAR(40) NOT NULL,
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at         TIMESTAMPTZ,
    -- Idempotent consumer: processing the same event twice cannot notify twice.
    CONSTRAINT uq_notification_event_user UNIQUE (event_id, user_id)
);
CREATE INDEX ix_notifications_user ON notifications (user_id, id DESC);
CREATE INDEX ix_notifications_unread ON notifications (user_id) WHERE read_at IS NULL;
"""

DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS outbox_events;
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS comments;
DROP TRIGGER IF EXISTS trg_activities_append_only ON activities;
DROP TABLE IF EXISTS activities;
DROP FUNCTION IF EXISTS forbid_activity_mutation();
DROP TABLE IF EXISTS work_items;
DROP TABLE IF EXISTS team_memberships;
DROP TABLE IF EXISTS teams;
DROP TABLE IF EXISTS users;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
