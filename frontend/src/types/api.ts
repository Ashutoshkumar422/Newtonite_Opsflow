// Mirrors backend/app/schemas. Kept hand-written and small; the OpenAPI spec is the reference.

export type Status = "open" | "in_progress" | "blocked" | "resolved" | "closed" | "cancelled";
export type Priority = "low" | "medium" | "high" | "critical";

export const STATUSES: Status[] = ["open", "in_progress", "blocked", "resolved", "closed", "cancelled"];
export const ACTIVE_STATUSES: Status[] = ["open", "in_progress", "blocked", "resolved"];
export const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];

export interface UserBrief {
  id: number;
  full_name: string;
  email: string;
}

export interface Membership {
  team_id: number;
  team_key: string;
  team_name: string;
  role: "manager" | "member";
}

export interface Me {
  id: number;
  email: string;
  full_name: string;
  is_admin: boolean;
  memberships: Membership[];
  must_change_password: boolean;
  has_password: boolean;
  sso_linked: boolean;
  session_id: string | null;
  auth_method: "password" | "sso" | "api_token" | null;
}

export interface TeamBrief {
  id: number;
  key: string;
  name: string;
}

export interface Team extends TeamBrief {
  description: string;
  my_role: "manager" | "member" | null;
  member_count: number;
  active_item_count: number;
  can_manage: boolean;
}

export interface Member {
  user: UserBrief;
  role: "manager" | "member";
  created_at: string;
}

export interface TransitionOption {
  to: Status;
  label: string;
  requires_reason: boolean;
}

export interface Permissions {
  can_edit: boolean;
  can_claim: boolean;
  can_assign: boolean;
  can_release: boolean;
  can_comment: boolean;
  transitions: TransitionOption[];
}

export interface WorkItemSummary {
  id: number;
  key: string;
  number: number;
  title: string;
  status: Status;
  priority: Priority;
  team: TeamBrief;
  reporter: UserBrief;
  assignee: UserBrief | null;
  due_date: string | null;
  is_overdue: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkItem extends WorkItemSummary {
  description: string;
  permissions: Permissions;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number | null;
}

export interface Activity {
  id: number;
  action: string;
  actor: UserBrief;
  changes: Record<string, unknown>;
  created_at: string;
}

export interface Comment {
  id: number;
  author: UserBrief;
  body: string;
  created_at: string;
  edited_at: string | null;
  can_edit: boolean;
}

export interface StatusCounts {
  active: number;
  open: number;
  in_progress: number;
  blocked: number;
  resolved: number;
  overdue: number;
  high_priority: number;
  unassigned: number;
  assigned_to_me: number;
}

export interface Dashboard {
  totals: StatusCounts;
  by_team: (StatusCounts & { team: TeamBrief })[];
}

export interface Notification {
  id: number;
  kind: string;
  message: string;
  work_item_id: number | null;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPage extends Page<Notification> {
  unread_count: number;
}

// ---------------------------------------------------------------- identity & administration
export interface AuthConfig {
  password_login_enabled: boolean;
  sso_enabled: boolean;
  sso_provider_name: string | null;
  password_min_length: number;
}

export interface SessionInfo {
  id: string;
  auth_method: "password" | "sso" | "api_token";
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  current: boolean;
}

export interface UserAdmin {
  id: number;
  email: string;
  full_name: string;
  is_admin: boolean;
  is_active: boolean;
  must_change_password: boolean;
  sso_linked: boolean;
  has_password: boolean;
  created_at: string;
  last_login_at: string | null;
  deactivated_at: string | null;
  team_count: number;
  active_session_count: number;
  active_assigned_items: number;
}

export interface UserAdminPage {
  items: UserAdmin[];
  next_cursor: number | null;
  total: number;
}

export interface UserWithPassword {
  user: UserAdmin;
  temporary_password: string | null;
}

export interface SecurityEvent {
  id: number;
  occurred_at: string;
  event_type: string;
  actor: UserBrief | null;
  target: UserBrief | null;
  ip_address: string | null;
  details: Record<string, unknown>;
}

export interface SecurityEventPage {
  items: SecurityEvent[];
  next_cursor: number | null;
}

export interface OutboxStats {
  pending: number;
  processed: number;
  failed: number;
  oldest_pending_age_seconds: number | null;
  recent_failures: { id: number; event_type: string; attempts: number; last_error: string | null }[];
}
