"""Environment-based configuration. Every value can be overridden with an OPSFLOW_* env var."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPSFLOW_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://opsflow:opsflow@localhost:5432/opsflow"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_disable_jit: bool = True

    # Development-only default. Startup refuses to run with this value when env=production.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 8 * 60

    cookie_name: str = "opsflow_session"
    cookie_secure: bool = False  # set True behind HTTPS
    cors_origins: list[str] = ["http://localhost:5173"]

    # Behind a reverse proxy (nginx in docker compose) the client address comes from
    # X-Forwarded-For. Only enable when the API is not directly reachable by clients.
    trust_proxy_headers: bool = False

    # Login throttling (see app/services/throttle.py)
    login_max_failures_per_account: int = 5
    login_max_failures_per_ip: int = 30
    login_failure_window_minutes: int = 15
    login_lockout_minutes: int = 15

    # Password policy
    password_min_length: int = 10
    # When false only administrators may use password login (SSO-only organisations keep a
    # break-glass path for admins).
    password_login_enabled: bool = True

    # OIDC single sign-on (disabled unless issuer + client id are set)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    # Where the backend fetches discovery from, if different from the public issuer URL
    # (e.g. an internal Docker hostname). Defaults to <issuer>/.well-known/openid-configuration.
    oidc_discovery_url: str | None = None
    oidc_redirect_uri: str = "http://localhost:5173/api/v1/auth/oidc/callback"
    oidc_scopes: str = "openid email profile"
    oidc_provider_name: str = "Single sign-on"
    # Create an OpsFlow account on first SSO login (no team memberships) for these email domains.
    oidc_auto_provision: bool = False
    oidc_allowed_domains: list[str] = []

    idempotency_ttl_hours: int = 24

    outbox_max_attempts: int = 5
    outbox_batch_size: int = 50
    worker_poll_seconds: float = 2.0

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.jwt_secret.startswith("dev-only"):
        raise RuntimeError("OPSFLOW_JWT_SECRET must be set in production")
    return s
