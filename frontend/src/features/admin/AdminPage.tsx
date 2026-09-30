import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/AppShell";
import { Avatar, Button, Card, EmptyState, ErrorState, Field, Input, Loading, Modal, Select, cx, useToast } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import { formatDateTime, relativeTime } from "../../lib/format";
import type { OutboxStats, SecurityEventPage, UserAdmin, UserAdminPage, UserWithPassword } from "../../types/api";
import { useCurrentUser } from "../auth/auth";

type Tab = "users" | "security" | "system";

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <>
      <PageHeader title="Administration" subtitle="Accounts, access and system health." />
      <div role="tablist" aria-label="Administration" className="mb-4 flex gap-4 border-b border-ink-200">
        {(
          [
            ["users", "Users"],
            ["security", "Security log"],
            ["system", "System"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cx(
              "-mb-px border-b-2 px-1 py-2 text-sm",
              tab === t ? "border-ink-900 font-medium text-ink-900" : "border-transparent text-ink-500 hover:text-ink-800",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "users" && <UsersTab />}
      {tab === "security" && <SecurityTab />}
      {tab === "system" && <SystemTab />}
    </>
  );
}

// ---------------------------------------------------------------- users
type Pending =
  | { kind: "deactivate" | "reactivate" | "reset" | "revoke" | "grant" | "demote"; user: UserAdmin }
  | null;

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function UsersTab() {
  const me = useCurrentUser();
  const qc = useQueryClient();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [showInactive, setShowInactive] = useState(true);
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [secret, setSecret] = useState<{ user: UserAdmin; password: string; reason: string } | null>(null);

  const users = useInfiniteQuery({
    queryKey: ["admin-users", q, showInactive],
    queryFn: ({ pageParam }) =>
      api<UserAdminPage>("/admin/users", { query: { q, include_inactive: showInactive, limit: 50, cursor: pageParam ?? undefined } }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });
  const rows = users.data?.pages.flatMap((p) => p.items) ?? [];
  const total = users.data?.pages[0]?.total;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin-users"] });
    qc.invalidateQueries({ queryKey: ["security-events"] });
  };

  const act = useMutation({
    mutationFn: async (p: NonNullable<Pending>): Promise<{ message: string; secret?: string }> => {
      const u = p.user;
      switch (p.kind) {
        case "deactivate": {
          const r = await api<UserAdmin>(`/admin/users/${u.id}`, { method: "PATCH", body: { is_active: false } });
          return {
            message:
              r.active_assigned_items > 0
                ? `${u.full_name} deactivated. They still own ${r.active_assigned_items} active item(s) — reassign them.`
                : `${u.full_name} deactivated and signed out everywhere.`,
          };
        }
        case "reactivate":
          await api(`/admin/users/${u.id}`, { method: "PATCH", body: { is_active: true } });
          return { message: `${u.full_name} reactivated.` };
        case "grant":
          await api(`/admin/users/${u.id}`, { method: "PATCH", body: { is_admin: true } });
          return { message: `${u.full_name} is now an administrator.` };
        case "demote":
          await api(`/admin/users/${u.id}`, { method: "PATCH", body: { is_admin: false } });
          return { message: `${u.full_name} is no longer an administrator.` };
        case "reset": {
          const r = await api<UserWithPassword>(`/admin/users/${u.id}/reset-password`, { method: "POST" });
          return { message: `Password reset for ${u.full_name}.`, secret: r.temporary_password ?? undefined };
        }
        case "revoke": {
          const r = await api<{ revoked: number }>(`/admin/users/${u.id}/revoke-sessions`, { method: "POST" });
          return { message: `Signed ${u.full_name} out of ${r.revoked} session(s).` };
        }
      }
    },
    onSuccess: (r, p) => {
      toast("success", r.message);
      if (r.secret) setSecret({ user: p.user, password: r.secret, reason: "reset" });
      setPending(null);
      refresh();
    },
    onError: (e) => {
      toast("error", errorMessage(e));
      setPending(null);
    },
  });

  const CONFIRM: Record<NonNullable<Pending>["kind"], { title: string; body: (u: UserAdmin) => string; cta: string; danger?: boolean }> = {
    deactivate: {
      title: "Deactivate account?",
      body: (u) =>
        `${u.full_name} will be signed out of ${u.active_session_count} session(s) immediately and can no longer sign in.` +
        (u.active_assigned_items ? ` They own ${u.active_assigned_items} active item(s) that should be reassigned.` : ""),
      cta: "Deactivate",
      danger: true,
    },
    reactivate: { title: "Reactivate account?", body: (u) => `${u.full_name} will be able to sign in again.`, cta: "Reactivate" },
    reset: {
      title: "Reset password?",
      body: (u) => `A temporary password will be generated for ${u.full_name}. All their sessions end now and they must choose a new password at next sign-in.`,
      cta: "Reset password",
      danger: true,
    },
    revoke: { title: "Sign out everywhere?", body: (u) => `End all ${u.active_session_count} active session(s) of ${u.full_name}.`, cta: "Sign out", danger: true },
    grant: { title: "Grant administrator role?", body: (u) => `${u.full_name} will be able to manage every team and all accounts.`, cta: "Make admin" },
    demote: { title: "Remove administrator role?", body: (u) => `${u.full_name} will keep their team roles but lose administration rights.`, cta: "Remove admin", danger: true },
  };

  return (
    <>
      <Card className="mb-4 p-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input aria-label="Search users" placeholder="Search by name or email" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-sm" />
          <label className="flex items-center gap-2 text-sm text-ink-600">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} className="h-4 w-4" />
            Include deactivated
          </label>
          <span className="text-xs text-ink-500">{total != null && `${total} account${total === 1 ? "" : "s"}`}</span>
          <Button variant="primary" className="ml-auto" onClick={() => setCreating(true)}>
            New user
          </Button>
        </div>
      </Card>

      <Card>
        {users.isLoading ? (
          <Loading label="Loading users" />
        ) : users.error ? (
          <ErrorState error={users.error} onRetry={() => users.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No users match" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-ink-100 text-xs uppercase tracking-wide text-ink-400">
                <tr>
                  <th className="px-4 py-2 font-medium">User</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="hidden px-3 py-2 font-medium md:table-cell">Sign-in</th>
                  <th className="hidden px-3 py-2 font-medium lg:table-cell">Last sign-in</th>
                  <th className="hidden px-3 py-2 text-right font-medium md:table-cell">Teams</th>
                  <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">Sessions</th>
                  <th className="px-4 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {rows.map((u) => (
                  <tr key={u.id} className={cx(!u.is_active && "bg-ink-50 text-ink-500")}>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <Avatar name={u.full_name} size="md" />
                        <div className="min-w-0">
                          <div className="truncate font-medium text-ink-900">
                            {u.full_name} {u.id === me.id && <span className="font-normal text-ink-400">(you)</span>}
                          </div>
                          <div className="truncate text-xs text-ink-500">{u.email}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {u.is_active ? <Pill tone="green">Active</Pill> : <Pill tone="gray">Deactivated</Pill>}
                        {u.is_admin && <Pill tone="violet">Admin</Pill>}
                        {u.must_change_password && u.is_active && <Pill tone="amber">Temp password</Pill>}
                      </div>
                    </td>
                    <td className="hidden px-3 py-2.5 text-xs text-ink-600 md:table-cell">
                      {[u.has_password && "Password", u.sso_linked && "SSO"].filter(Boolean).join(" · ") || "—"}
                    </td>
                    <td className="hidden whitespace-nowrap px-3 py-2.5 text-xs text-ink-600 lg:table-cell" title={u.last_login_at ? formatDateTime(u.last_login_at) : ""}>
                      {u.last_login_at ? relativeTime(u.last_login_at) : "Never"}
                    </td>
                    <td className="hidden px-3 py-2.5 text-right tabular md:table-cell">{u.team_count}</td>
                    <td className="hidden px-3 py-2.5 text-right tabular lg:table-cell">{u.active_session_count}</td>
                    <td className="px-4 py-2.5">
                      <UserActions user={u} self={u.id === me.id} onPick={(kind) => setPending({ kind, user: u })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {users.hasNextPage && (
          <div className="border-t border-ink-100 p-3 text-center">
            <Button size="sm" loading={users.isFetchingNextPage} onClick={() => users.fetchNextPage()}>
              Load more
            </Button>
          </div>
        )}
      </Card>

      <Modal open={!!pending} title={pending ? CONFIRM[pending.kind].title : ""} onClose={() => setPending(null)}>
        {pending && (
          <>
            <p className="text-sm text-ink-600">{CONFIRM[pending.kind].body(pending.user)}</p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setPending(null)}>
                Cancel
              </Button>
              <Button variant={CONFIRM[pending.kind].danger ? "danger" : "primary"} loading={act.isPending} onClick={() => act.mutate(pending)}>
                {CONFIRM[pending.kind].cta}
              </Button>
            </div>
          </>
        )}
      </Modal>

      <CreateUserModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(r) => {
          setCreating(false);
          refresh();
          toast("success", `Created ${r.user.full_name}`);
          if (r.temporary_password) setSecret({ user: r.user, password: r.temporary_password, reason: "created" });
        }}
      />
      <OneTimeSecretModal secret={secret} onClose={() => setSecret(null)} />
    </>
  );
}

function Pill({ tone, children }: { tone: "green" | "gray" | "violet" | "amber"; children: React.ReactNode }) {
  const tones = {
    green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    gray: "bg-ink-100 text-ink-500 ring-ink-200",
    violet: "bg-violet-50 text-violet-700 ring-violet-200",
    amber: "bg-amber-50 text-amber-800 ring-amber-200",
  };
  return <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone])}>{children}</span>;
}

function UserActions({ user, self, onPick }: { user: UserAdmin; self: boolean; onPick: (k: NonNullable<Pending>["kind"]) => void }) {
  const [open, setOpen] = useState(false);
  const items: [NonNullable<Pending>["kind"], string, boolean][] = [
    ["reset", "Reset password", user.is_active],
    ["revoke", "Sign out everywhere", user.active_session_count > 0],
    ["grant", "Make administrator", !user.is_admin && user.is_active],
    ["demote", "Remove administrator", user.is_admin && !self],
    ["deactivate", "Deactivate", user.is_active && !self],
    ["reactivate", "Reactivate", !user.is_active],
  ];
  const visible = items.filter(([, , show]) => show);
  return (
    <div className="relative flex justify-end">
      <Button size="sm" variant="ghost" aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${user.full_name}`} onClick={() => setOpen((o) => !o)}>
        Manage ▾
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} aria-hidden />
          <ul role="menu" className="absolute right-0 top-8 z-40 w-48 overflow-hidden rounded-md border border-ink-200 bg-white py-1 shadow-lg">
            {visible.length === 0 && <li className="px-3 py-2 text-xs text-ink-400">No actions available</li>}
            {visible.map(([kind, label]) => (
              <li key={kind}>
                <button
                  role="menuitem"
                  className={cx(
                    "block w-full px-3 py-1.5 text-left text-sm hover:bg-ink-50",
                    (kind === "deactivate" || kind === "demote") && "text-red-700",
                  )}
                  onClick={() => {
                    setOpen(false);
                    onPick(kind);
                  }}
                >
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function CreateUserModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (r: UserWithPassword) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [mode, setMode] = useState<"generate" | "set">("generate");
  const [password, setPassword] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api<UserWithPassword>("/admin/users", {
        method: "POST",
        body: { email, full_name: name, is_admin: isAdmin, password: mode === "set" ? password : null },
      }),
    onSuccess: (r) => {
      setEmail("");
      setName("");
      setIsAdmin(false);
      setPassword("");
      setMode("generate");
      onCreated(r);
    },
  });
  const fe = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <Modal open={open} title="Create a user" onClose={onClose}>
      <form className="space-y-3" onSubmit={submit} noValidate>
        <Field label="Work email" htmlFor="nu-email" error={fe.email}>
          <Input id="nu-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} invalid={!!fe.email} />
        </Field>
        <Field label="Full name" htmlFor="nu-name" error={fe.full_name}>
          <Input id="nu-name" value={name} onChange={(e) => setName(e.target.value)} invalid={!!fe.full_name} />
        </Field>
        <Field label="Initial password" htmlFor="nu-mode">
          <Select id="nu-mode" value={mode} onChange={(e) => setMode(e.target.value as "generate" | "set")}>
            <option value="generate">Generate a one-time temporary password</option>
            <option value="set">Set a temporary password myself</option>
          </Select>
        </Field>
        {mode === "set" && (
          <Field label="Temporary password" htmlFor="nu-pw" error={fe.new_password} hint="The user must replace it at first sign-in.">
            <Input id="nu-pw" type="text" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" />
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm text-ink-700">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} className="h-4 w-4" />
          Administrator
        </label>
        <p className="text-xs text-ink-500">Add the person to teams from the team's page after creating the account.</p>
        {create.error && !Object.keys(fe).length && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMessage(create.error)}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={create.isPending} disabled={!email || !name || (mode === "set" && !password)}>
            Create user
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function OneTimeSecretModal({ secret, onClose }: { secret: { user: UserAdmin; password: string; reason: string } | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [secret]);
  return (
    <Modal open={!!secret} title="Temporary password" onClose={onClose}>
      {secret && (
        <>
          <p className="text-sm text-ink-600">
            Give this to <strong>{secret.user.full_name}</strong> through a secure channel. It is shown <strong>only once</strong>; they must choose their own password when they sign in.
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-md border border-ink-200 bg-ink-50 p-3">
            <code className="flex-1 select-all break-all font-mono text-base" data-testid="temporary-password">
              {secret.password}
            </code>
            <Button
              size="sm"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(secret.password);
                  setCopied(true);
                } catch {
                  setCopied(false);
                }
              }}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- security log
const EVENT_LABEL: Record<string, string> = {
  login_succeeded: "Signed in",
  login_failed: "Failed sign-in",
  login_locked: "Sign-in locked",
  logout: "Signed out",
  session_revoked: "Session revoked",
  sessions_revoked: "Sessions revoked",
  password_changed: "Password changed",
  password_reset: "Password reset",
  user_created: "User created",
  user_updated: "User updated",
  user_deactivated: "User deactivated",
  user_reactivated: "User reactivated",
  admin_granted: "Admin granted",
  admin_revoked: "Admin removed",
  team_created: "Team created",
  sso_login: "SSO sign-in",
  sso_linked: "SSO identity linked",
  sso_provisioned: "SSO account provisioned",
  sso_rejected: "SSO sign-in rejected",
};
const ALERT_EVENTS = new Set(["login_failed", "login_locked", "sso_rejected", "user_deactivated", "password_reset"]);

function SecurityTab() {
  const [type, setType] = useState("");
  const events = useInfiniteQuery({
    queryKey: ["security-events", type],
    queryFn: ({ pageParam }) =>
      api<SecurityEventPage>("/admin/security-events", { query: { event_type: type || undefined, cursor: pageParam ?? undefined, limit: 50 } }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const rows = events.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-3">
        <Select aria-label="Event type" className="w-56" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All events</option>
          {Object.entries(EVENT_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        <span className="text-xs text-ink-500">Append-only: entries can't be edited or deleted, even directly in the database.</span>
      </div>
      {events.isLoading ? (
        <Loading />
      ) : events.error ? (
        <ErrorState error={events.error} onRetry={() => events.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState title="No events" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-ink-100 text-xs uppercase tracking-wide text-ink-400">
              <tr>
                <th className="px-4 py-2 font-medium">When</th>
                <th className="px-3 py-2 font-medium">Event</th>
                <th className="px-3 py-2 font-medium">Actor</th>
                <th className="px-3 py-2 font-medium">Subject</th>
                <th className="hidden px-3 py-2 font-medium md:table-cell">IP</th>
                <th className="hidden px-4 py-2 font-medium lg:table-cell">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {rows.map((e) => (
                <tr key={e.id}>
                  <td className="whitespace-nowrap px-4 py-2 text-xs text-ink-600" title={formatDateTime(e.occurred_at)}>
                    {relativeTime(e.occurred_at)}
                  </td>
                  <td className={cx("whitespace-nowrap px-3 py-2 font-medium", ALERT_EVENTS.has(e.event_type) ? "text-red-700" : "text-ink-800")}>
                    {EVENT_LABEL[e.event_type] ?? e.event_type}
                  </td>
                  <td className="px-3 py-2 text-ink-700">{e.actor?.full_name ?? <span className="text-ink-400">—</span>}</td>
                  <td className="px-3 py-2 text-ink-700">
                    {e.target?.full_name ?? (typeof e.details.email === "string" ? <span className="font-mono text-xs">{e.details.email}</span> : <span className="text-ink-400">—</span>)}
                  </td>
                  <td className="hidden px-3 py-2 font-mono text-xs text-ink-500 md:table-cell">{e.ip_address ?? "—"}</td>
                  <td className="hidden max-w-xs truncate px-4 py-2 font-mono text-xs text-ink-500 lg:table-cell" title={JSON.stringify(e.details)}>
                    {Object.entries(e.details)
                      .filter(([k]) => k !== "email")
                      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                      .join(" ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {events.hasNextPage && (
        <div className="border-t border-ink-100 p-3 text-center">
          <Button size="sm" loading={events.isFetchingNextPage} onClick={() => events.fetchNextPage()}>
            Load older events
          </Button>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- system
function SystemTab() {
  const stats = useQuery({ queryKey: ["outbox-stats"], queryFn: () => api<OutboxStats>("/admin/outbox"), refetchInterval: 10_000 });
  if (stats.isLoading) return <Loading />;
  if (stats.error) return <ErrorState error={stats.error} onRetry={() => stats.refetch()} />;
  const s = stats.data!;
  const lagging = (s.oldest_pending_age_seconds ?? 0) > 60;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Notification delivery (outbox)</h2>
        <p className="mt-1 text-xs text-ink-500">Events are written with each change and delivered by the background worker.</p>
        <dl className="mt-4 grid grid-cols-3 gap-3 text-center">
          {(
            [
              ["Pending", s.pending, lagging ? "text-amber-600" : ""],
              ["Delivered", s.processed, ""],
              ["Failed", s.failed, s.failed ? "text-red-600" : ""],
            ] as [string, number, string][]
          ).map(([label, n, tone]) => (
            <div key={label} className="rounded-md border border-ink-200 p-3">
              <dt className="text-xs text-ink-500">{label}</dt>
              <dd className={cx("mt-1 text-2xl font-semibold tabular", tone)}>{n}</dd>
            </div>
          ))}
        </dl>
        <p className={cx("mt-3 text-xs", lagging ? "text-amber-700" : "text-ink-500")}>
          {s.oldest_pending_age_seconds == null
            ? "Nothing waiting."
            : `Oldest pending event: ${Math.round(s.oldest_pending_age_seconds)} s${lagging ? " — is the worker running?" : ""}`}
        </p>
      </Card>
      <Card className="p-4">
        <h2 className="text-sm font-semibold">Recent delivery failures</h2>
        {s.recent_failures.length === 0 ? (
          <p className="mt-3 text-sm text-ink-500">None. Failed events are retried with backoff and listed here after 5 attempts.</p>
        ) : (
          <ul className="mt-3 space-y-2 text-xs">
            {s.recent_failures.map((f) => (
              <li key={f.id} className="rounded border border-red-100 bg-red-50 p-2 font-mono text-red-800">
                #{f.id} {f.event_type} · {f.attempts} attempts · {f.last_error}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-4 text-xs text-ink-500">
          API documentation:{" "}
          <Link to="/api/docs" reloadDocument className="text-accent-700 hover:underline">
            /api/docs
          </Link>
        </p>
      </Card>
    </div>
  );
}
