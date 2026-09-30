"""Identity & access hardening: revocable sessions, login throttling, security audit log,
forced password change, SSO subject linking.

Revision ID: 0002_identity_security
Revises: 0001_initial
Create Date: 2026-09-29
"""

from alembic import op

revision = "0002_identity_security"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- ---------------------------------------------------------------- users
ALTER TABLE users
    ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN password_changed_at  TIMESTAMPTZ,
    ADD COLUMN oidc_issuer          VARCHAR(500),
    ADD COLUMN oidc_subject         VARCHAR(255),
    ADD COLUMN last_login_at        TIMESTAMPTZ,
    ADD COLUMN deactivated_at       TIMESTAMPTZ;
-- One OpsFlow account per external identity.
CREATE UNIQUE INDEX uq_users_oidc_identity ON users (oidc_issuer, oidc_subject)
    WHERE oidc_subject IS NOT NULL;
ALTER TABLE users ADD CONSTRAINT ck_users_oidc_pair
    CHECK ((oidc_issuer IS NULL) = (oidc_subject IS NULL));

-- ---------------------------------------------------------------- sessions
-- Every access token carries a session id (sid). A token is only valid while its session row is
-- active, which makes logout, "sign out other devices" and admin revocation immediate.
CREATE TABLE sessions (
    id              UUID PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    auth_method     VARCHAR(20) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    revoked_reason  VARCHAR(40),
    ip_address      VARCHAR(64),
    user_agent      VARCHAR(300),
    CONSTRAINT ck_sessions_method CHECK (auth_method IN ('password', 'sso', 'api_token')),
    CONSTRAINT ck_sessions_expiry CHECK (expires_at > created_at)
);
CREATE INDEX ix_sessions_user_active ON sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX ix_sessions_expires ON sessions (expires_at);

-- ---------------------------------------------------------------- login throttling
-- Fixed-window failure counters keyed by "email:<address>" or "ip:<address>". Stored in
-- PostgreSQL so limits hold across every API replica without another moving part.
CREATE TABLE auth_throttle (
    key                 VARCHAR(320) PRIMARY KEY,
    failures            INTEGER NOT NULL,
    window_started_at   TIMESTAMPTZ NOT NULL,
    locked_until        TIMESTAMPTZ,
    CONSTRAINT ck_throttle_failures CHECK (failures >= 0)
);

-- ---------------------------------------------------------------- security audit log
CREATE TABLE security_events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type      VARCHAR(40) NOT NULL,
    actor_id        BIGINT REFERENCES users(id) ON DELETE RESTRICT,
    target_user_id  BIGINT REFERENCES users(id) ON DELETE RESTRICT,
    ip_address      VARCHAR(64),
    details         JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_security_events_time ON security_events (id DESC);
CREATE INDEX ix_security_events_target ON security_events (target_user_id, id DESC);
CREATE INDEX ix_security_events_type ON security_events (event_type, id DESC);

CREATE FUNCTION forbid_security_event_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'security_events are append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_security_events_append_only
    BEFORE UPDATE OR DELETE ON security_events
    FOR EACH ROW EXECUTE FUNCTION forbid_security_event_mutation();
"""

DOWNGRADE_SQL = r"""
DROP TRIGGER IF EXISTS trg_security_events_append_only ON security_events;
DROP TABLE IF EXISTS security_events;
DROP FUNCTION IF EXISTS forbid_security_event_mutation();
DROP TABLE IF EXISTS auth_throttle;
DROP TABLE IF EXISTS sessions;
DROP INDEX IF EXISTS uq_users_oidc_identity;
ALTER TABLE users
    DROP CONSTRAINT IF EXISTS ck_users_oidc_pair,
    DROP COLUMN IF EXISTS deactivated_at,
    DROP COLUMN IF EXISTS last_login_at,
    DROP COLUMN IF EXISTS oidc_subject,
    DROP COLUMN IF EXISTS oidc_issuer,
    DROP COLUMN IF EXISTS password_changed_at,
    DROP COLUMN IF EXISTS must_change_password;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
