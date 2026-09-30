import { NavLink, Outlet, Link } from "react-router-dom";
import { Avatar, Button, cx } from "../ui";
import { useCurrentUser, useLogout } from "../../features/auth/auth";
import { NotificationBell } from "../../features/notifications/NotificationBell";

const NAV = [
  { to: "/", label: "Dashboard", end: true, icon: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" },
  { to: "/work-items", label: "Work items", end: false, icon: "M4 6h16M4 12h16M4 18h10" },
  { to: "/teams", label: "Teams", end: false, icon: "M16 11a4 4 0 1 0-8 0 4 4 0 0 0 8 0zM4 21v-1a6 6 0 0 1 16 0v1" },
];
const ADMIN_NAV = {
  to: "/admin",
  label: "Admin",
  end: false,
  icon: "M12 3l7 4v5c0 5-3.5 8-7 9-3.5-1-7-4-7-9V7l7-4z",
};

export function AppShell() {
  const me = useCurrentUser();
  const logout = useLogout();
  if (me.must_change_password) {
    // Minimal chrome while a temporary password is in use: nothing else is reachable anyway.
    return (
      <div className="min-h-full">
        <header className="flex h-14 items-center justify-between border-b border-ink-200 bg-white px-4 md:px-6">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" className="h-7 w-7" /> OpsFlow
          </span>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()}>
            Sign out
          </Button>
        </header>
        <main className="px-4 py-5 md:px-6">
          <Outlet />
        </main>
      </div>
    );
  }
  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-row items-center gap-1 border-b border-ink-200 bg-white px-3 py-2 md:w-56 md:flex-col md:items-stretch md:border-b-0 md:border-r md:px-3 md:py-4">
        <Link to="/" className="mr-3 flex items-center gap-2 px-2 md:mb-6 md:mr-0">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="font-semibold tracking-tight">OpsFlow</span>
        </Link>
        <nav className="flex flex-row gap-1 md:flex-col" aria-label="Main">
          {[...NAV, ...(me.is_admin ? [ADMIN_NAV] : [])].map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                  isActive ? "bg-ink-100 font-medium text-ink-900" : "text-ink-600 hover:bg-ink-50 hover:text-ink-900",
                )
              }
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                <path d={n.icon} strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="hidden sm:inline">{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto md:ml-0 md:mt-auto">
          <Link
            to="/account"
            title="Your account, password and sessions"
            className="hidden items-center gap-2 rounded-md px-2 py-2 hover:bg-ink-50 md:flex"
          >
            <Avatar name={me.full_name} size="md" />
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{me.full_name}</div>
              <div className="truncate text-xs text-ink-500">{me.is_admin ? "Administrator" : me.email}</div>
            </div>
          </Link>
          <Link to="/account" className="block rounded-md px-2.5 py-1 text-xs text-ink-600 hover:bg-ink-50 md:hidden">
            Account
          </Link>
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => logout.mutate()}>
            Sign out
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end gap-2 border-b border-ink-200 bg-white px-4 md:px-6">
          <Link to="/work-items/new">
            <Button variant="primary" size="sm">
              + New work item
            </Button>
          </Link>
          <NotificationBell />
        </header>
        <main className="flex-1 px-4 py-5 md:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}
