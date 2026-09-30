import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Field, Input, Loading } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import type { AuthConfig, Me } from "../../types/api";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<Me>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useAuthConfig() {
  return useQuery({
    queryKey: ["auth-config"],
    queryFn: () => api<AuthConfig>("/auth/config"),
    staleTime: Infinity,
  });
}

/** Current user; only call inside <RequireAuth>. */
export function useCurrentUser(): Me {
  const { data } = useMe();
  if (!data) throw new Error("useCurrentUser used outside RequireAuth");
  return data;
}

export function useIsManagerSomewhere(): boolean {
  const me = useCurrentUser();
  return me.is_admin || me.memberships.some((m) => m.role === "manager");
}

export function useLogout() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSettled: () => {
      qc.clear();
      navigate("/login", { replace: true });
    },
  });
}

/**
 * UX guard (the server enforces the same rules): unauthenticated users go to /login; users holding
 * a temporary password are kept on /account until they choose their own (the API refuses every
 * other endpoint with 403 password_change_required anyway).
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { data, isLoading, error } = useMe();
  const location = useLocation();
  if (isLoading) return <Loading label="Checking your session" />;
  if (error) return <div className="p-8 text-sm text-red-700">Cannot reach the server: {errorMessage(error)}</div>;
  if (!data) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (data.must_change_password && location.pathname !== "/account") return <Navigate to="/account" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const me = useCurrentUser();
  if (!me.is_admin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

const DEMO_ACCOUNTS = [
  ["priya@opsflow.dev", "Payments manager"],
  ["omar@opsflow.dev", "Member (Payments, Support)"],
  ["daniel@opsflow.dev", "Member (Payments, Platform)"],
  ["admin@opsflow.dev", "Administrator"],
];

const SSO_ERRORS: Record<string, string> = {
  no_account: "There is no OpsFlow account for that email. Ask an administrator to create one.",
  account_disabled: "This account has been deactivated. Contact an administrator.",
  email_not_verified: "Your identity provider has not verified your email address.",
  email_missing: "Your identity provider did not share an email address.",
  state_mismatch: "The sign-in attempt expired or was interrupted. Please try again.",
  identity_conflict: "This account is already linked to a different SSO identity.",
  provider_unreachable: "The identity provider could not be reached. Try again shortly.",
};

function loginErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.code === "too_many_attempts") {
    const secs = Number(err.details.retry_after_seconds ?? 0);
    const mins = Math.max(1, Math.ceil(secs / 60));
    return `Too many failed attempts. Sign-in is paused for about ${mins} minute${mins === 1 ? "" : "s"}.`;
  }
  return errorMessage(err);
}

export function LoginPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const config = useAuthConfig();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const login = useMutation({
    mutationFn: () => api<Me>("/auth/login", { method: "POST", body: { email, password } }),
    onSuccess: (me) => {
      qc.setQueryData(["me"], me);
      navigate(me.must_change_password ? "/account" : from, { replace: true });
    },
  });
  const { data: me } = useMe();
  if (me) return <Navigate to="/" replace />;

  const ssoError = params.get("sso_error");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <div>
            <div className="text-lg font-semibold tracking-tight">OpsFlow</div>
            <div className="text-xs text-ink-500">Operational work, coordinated</div>
          </div>
        </div>

        {ssoError && (
          <p role="alert" className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {SSO_ERRORS[ssoError] ?? "Single sign-on failed. Please try again."}
          </p>
        )}

        <div className="space-y-4 rounded-lg border border-ink-200 bg-white p-5">
          {config.data?.sso_enabled && (
            <>
              <a
                href={`/api/v1/auth/oidc/login?next=${encodeURIComponent(from)}`}
                className="flex h-9 w-full items-center justify-center gap-2 rounded-md bg-ink-900 text-sm font-medium text-white hover:bg-ink-800"
              >
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M12 3l7 4v5c0 5-3.5 8-7 9-3.5-1-7-4-7-9V7l7-4z" strokeLinejoin="round" />
                </svg>
                Continue with {config.data.sso_provider_name}
              </a>
              <div className="flex items-center gap-2 text-xs text-ink-400">
                <span className="h-px flex-1 bg-ink-200" /> or use a password <span className="h-px flex-1 bg-ink-200" />
              </div>
            </>
          )}
          <form onSubmit={submit} className="space-y-4" noValidate>
            {config.data && !config.data.password_login_enabled && (
              <p className="text-xs text-ink-500">Password sign-in is reserved for administrators.</p>
            )}
            <Field label="Work email" htmlFor="email">
              <Input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Password" htmlFor="password">
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            {login.error && (
              <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                {loginErrorMessage(login.error)}
              </p>
            )}
            <Button
              type="submit"
              variant={config.data?.sso_enabled ? "secondary" : "primary"}
              className="w-full"
              loading={login.isPending}
              disabled={!email || !password}
            >
              Sign in
            </Button>
          </form>
        </div>
        <div className="mt-4 rounded-lg border border-dashed border-ink-300 p-3 text-xs text-ink-600">
          <div className="mb-1 font-medium">Development demo accounts — password <code>opsflow-demo</code></div>
          <ul className="space-y-0.5">
            {DEMO_ACCOUNTS.map(([addr, role]) => (
              <li key={addr}>
                <button
                  type="button"
                  className="text-accent-700 underline-offset-2 hover:underline"
                  onClick={() => {
                    setEmail(addr);
                    setPassword("opsflow-demo");
                  }}
                >
                  {addr}
                </button>{" "}
                <span className="text-ink-400">· {role}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
