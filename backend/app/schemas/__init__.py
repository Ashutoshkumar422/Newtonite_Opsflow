"""Request/response schemas (API contract). Validation lives here; business rules live in services."""

from datetime import date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.workflow import Priority, Status

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = Field(
        None, description="Opaque cursor for the next page; null when there are no more results"
    )
    total: int | None = None


# ---------------------------------------------------------------- users / auth
class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    email: str


class MembershipOut(BaseModel):
    team_id: int
    team_key: str
    team_name: str
    role: str


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str
    is_admin: bool
    memberships: list[MembershipOut]
    must_change_password: bool = False
    has_password: bool = True
    sso_linked: bool = False
    session_id: str | None = None
    auth_method: str | None = None


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- teams
class TeamBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str
    name: str


class TeamOut(TeamBrief):
    description: str
    my_role: str | None
    member_count: int
    active_item_count: int
    can_manage: bool


class TeamCreate(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$", description="2-10 uppercase chars, e.g. PAY")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=2000)


class MemberOut(BaseModel):
    user: UserBrief
    role: str
    created_at: datetime


class MemberUpsert(BaseModel):
    role: str = Field(pattern="^(manager|member)$")


# ---------------------------------------------------------------- work items
def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


class WorkItemCreate(BaseModel):
    team_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=20000)
    priority: Priority = Priority.MEDIUM
    due_date: date | None = None
    assignee_id: int | None = Field(
        None, description="Only a manager may assign someone else; members may assign themselves"
    )

    _strip_title = field_validator("title", mode="before")(_strip)

    @field_validator("title")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("title must not be blank")
        return v


class WorkItemUpdate(BaseModel):
    """Partial update. `version` is the version the client last saw (optimistic concurrency).
    Fields omitted are unchanged; `due_date: null` clears the due date."""

    version: int = Field(ge=1)
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20000)
    priority: Priority | None = None
    due_date: date | None = None

    _strip_title = field_validator("title", mode="before")(_strip)


class AssignIn(BaseModel):
    version: int = Field(ge=1)
    assignee_id: int | None = Field(description="null to unassign")


class TransitionIn(BaseModel):
    version: int = Field(ge=1)
    to_status: Status
    reason: str | None = Field(None, max_length=2000)


class TransitionOption(BaseModel):
    to: str
    label: str
    requires_reason: bool


class ItemPermissions(BaseModel):
    """Derived from the same policy functions the server enforces. For UI affordances only."""

    can_edit: bool
    can_claim: bool
    can_assign: bool
    can_release: bool
    can_comment: bool
    transitions: list[TransitionOption]


class WorkItemSummary(BaseModel):
    id: int
    key: str
    number: int
    title: str
    status: Status
    priority: Priority
    team: TeamBrief
    reporter: UserBrief
    assignee: UserBrief | None
    due_date: date | None
    is_overdue: bool
    version: int
    created_at: datetime
    updated_at: datetime


class WorkItemOut(WorkItemSummary):
    description: str
    permissions: ItemPermissions


class ActivityOut(BaseModel):
    id: int
    action: str
    actor: UserBrief
    changes: dict[str, Any]
    created_at: datetime


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)

    _strip_body = field_validator("body", mode="before")(_strip)


class CommentOut(BaseModel):
    id: int
    author: UserBrief
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool


# ---------------------------------------------------------------- dashboard / notifications
class StatusCounts(BaseModel):
    active: int
    open: int
    in_progress: int
    blocked: int
    resolved: int
    overdue: int
    high_priority: int
    unassigned: int
    assigned_to_me: int


class TeamCounts(StatusCounts):
    team: TeamBrief


class DashboardOut(BaseModel):
    totals: StatusCounts
    by_team: list[TeamCounts]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    message: str
    work_item_id: int | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(Page[NotificationOut]):
    unread_count: int


class OutboxStats(BaseModel):
    pending: int
    processed: int
    failed: int
    oldest_pending_age_seconds: float | None
    recent_failures: list[dict[str, Any]]


# ---------------------------------------------------------------- identity & administration
class AuthConfigOut(BaseModel):
    password_login_enabled: bool
    sso_enabled: bool
    sso_provider_name: str | None
    password_min_length: int


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ChangePasswordOut(BaseModel):
    other_sessions_revoked: int


class SessionOut(BaseModel):
    id: str
    auth_method: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool


class UserAdminOut(BaseModel):
    id: int
    email: str
    full_name: str
    is_admin: bool
    is_active: bool
    must_change_password: bool
    sso_linked: bool
    has_password: bool
    created_at: datetime
    last_login_at: datetime | None
    deactivated_at: datetime | None
    team_count: int
    active_session_count: int
    active_assigned_items: int


class UserAdminPage(BaseModel):
    items: list[UserAdminOut]
    next_cursor: int | None
    total: int


class UserCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=120)
    is_admin: bool = False
    password: str | None = Field(
        None, max_length=200, description="Omit to generate a one-time temporary password"
    )


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=120)
    is_admin: bool | None = None
    is_active: bool | None = None


class UserWithPasswordOut(BaseModel):
    user: UserAdminOut
    temporary_password: str | None = Field(
        None, description="Shown exactly once; the user must change it at next sign-in"
    )


class RevokedOut(BaseModel):
    revoked: int


class SecurityEventOut(BaseModel):
    id: int
    occurred_at: datetime
    event_type: str
    actor: UserBrief | None
    target: UserBrief | None
    ip_address: str | None
    details: dict[str, Any]


class SecurityEventPage(BaseModel):
    items: list[SecurityEventOut]
    next_cursor: int | None
