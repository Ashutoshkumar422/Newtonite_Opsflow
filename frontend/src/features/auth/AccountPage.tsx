import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { PageHeader } from "../../components/layout/AppShell";
import { Button, Card, CardHeader, ErrorState, Field, Input, Loading, Modal, useToast } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import { formatDateTime, relativeTime } from "../../lib/format";
import type { Me, SessionInfo } from "../../types/api";
import { useAuthConfig, useCurrentUser, useLogout } from "./auth";

function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown device";
  if (ua === "pytest") return "Test client";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : /curl|python|httpx/i.test(ua) ? "API client" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

const METHOD_LABEL: Record<SessionInfo["auth_method"], string> = {
  password: "Password",
  sso: "Single sign-on",
  api_token: "API token",
};

export function AccountPage() {
  const me = useCurrentUser();
  const forced = me.must_change_password;
  return (
    <>
      <PageHeader title="Your account" subtitle={`${me.full_name} · ${me.email}${me.is_admin ? " · Administrator" : ""}`} />
      {forced && (
        <div role="alert" className="mb-4 max-w-2xl rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="font-medium">Choose a new password to continue</div>
          <p className="mt-1">You signed in with a temporary password set by an administrator. Pick your own password before using OpsFlow.</p>
        </div>
      )}
      <div className="grid max-w-5xl gap-4 lg:grid-cols-2">
        <PasswordCard me={me} />
        {!forced && <SessionsCard />}
      </div>
    </>
  );
}

function PasswordCard({ me }: { me: Me }) {
  const qc = useQueryClient();
  const toast = useToast();
  const config = useAuthConfig();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [clientError, setClientError] = useState<string | null>(null);
  const min = config.data?.password_min_length ?? 10;

  const change = useMutation({
    mutationFn: () =>
      api<{ other_sessions_revoked: number }>("/account/password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      }),
    onSuccess: (r) => {
      setCurrent("");
      setNext("");
      setConfirm("");
      toast(
        "success",
        r.other_sessions_revoked
          ? `Password changed. Signed out ${r.other_sessions_revoked} other session${r.other_sessions_revoked === 1 ? "" : "s"}.`
          : "Password changed.",
      );
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  if (!me.has_password) {
    return (
      <Card>
        <CardHeader title="Password" />
        <p className="p-4 text-sm text-ink-600">
          Your account signs in through single sign-on, so there is no OpsFlow password to manage. Change your password with your identity provider.
        </p>
      </Card>
    );
  }

  const serverFields = change.error instanceof ApiError ? change.error.fieldErrors() : {};
  function submit(e: FormEvent) {
    e.preventDefault();
    if (next.length < min) return setClientError(`Use at least ${min} characters.`);
    if (next !== confirm) return setClientError("The new passwords do not match.");
    setClientError(null);
    change.mutate();
  }

  return (
    <Card>
      <CardHeader title="Change password" subtitle="Changing your password signs out your other devices." />
      <form className="space-y-3 p-4" onSubmit={submit} noValidate>
        <Field label={me.must_change_password ? "Temporary password" : "Current password"} htmlFor="cur" error={serverFields.current_password}>
          <Input id="cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label="New password" htmlFor="new" hint={`At least ${min} characters. A few unrelated words work well.`} error={serverFields.new_password}>
          <Input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Confirm new password" htmlFor="confirm" error={clientError ?? undefined}>
          <Input id="confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {change.error && !Object.keys(serverFields).length && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMessage(change.error)}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" loading={change.isPending} disabled={!current || !next || !confirm}>
            Update password
          </Button>
        </div>
      </form>
    </Card>
  );
}

function SessionsCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const logout = useLogout();
  const [confirmAll, setConfirmAll] = useState(false);
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionInfo[]>("/account/sessions") });
  const revoke = useMutation({
    mutationFn: (id: string) => api<void>(`/account/sessions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast("success", "Session signed out");
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => toast("error", errorMessage(e)),
  });
  const revokeOthers = useMutation({
    mutationFn: () => api<{ revoked: number }>("/account/sessions/revoke-others", { method: "POST" }),
    onSuccess: (r) => {
      toast("success", `Signed out ${r.revoked} other session${r.revoked === 1 ? "" : "s"}`);
      setConfirmAll(false);
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => toast("error", errorMessage(e)),
  });
  const others = (sessions.data ?? []).filter((s) => !s.current).length;

  return (
    <Card>
      <CardHeader
        title="Where you're signed in"
        subtitle="Sessions end immediately when signed out here — even on a stolen device."
        action={
          others > 0 && (
            <Button size="sm" onClick={() => setConfirmAll(true)}>
              Sign out other devices
            </Button>
          )
        }
      />
      {sessions.isLoading ? (
        <Loading />
      ) : sessions.error ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <ul className="divide-y divide-ink-100">
          {sessions.data?.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink-900">
                  {describeAgent(s.user_agent)}
                  {s.current && <span className="ml-2 rounded bg-accent-50 px-1.5 py-0.5 text-[11px] font-medium text-accent-700">This device</span>}
                </div>
                <div className="text-xs text-ink-500">
                  {METHOD_LABEL[s.auth_method]} · {s.ip_address ?? "unknown address"} · signed in {relativeTime(s.created_at)} ·{" "}
                  <span title={formatDateTime(s.last_seen_at)}>active {relativeTime(s.last_seen_at)}</span>
                </div>
              </div>
              {s.current ? (
                <Button size="sm" variant="ghost" onClick={() => logout.mutate()}>
                  Sign out
                </Button>
              ) : (
                <Button size="sm" variant="ghost" loading={revoke.isPending && revoke.variables === s.id} onClick={() => revoke.mutate(s.id)}>
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <Modal open={confirmAll} title="Sign out other devices?" onClose={() => setConfirmAll(false)}>
        <p className="text-sm text-ink-600">
          {others} other session{others === 1 ? "" : "s"} will be signed out immediately. You stay signed in here.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmAll(false)}>
            Cancel
          </Button>
          <Button variant="danger" loading={revokeOthers.isPending} onClick={() => revokeOthers.mutate()}>
            Sign out others
          </Button>
        </div>
      </Modal>
    </Card>
  );
}

