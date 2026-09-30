import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/layout/AppShell";
import { Card, CardHeader, EmptyState, ErrorState, Loading, cx } from "../../components/ui";
import { api, type Query } from "../../lib/api";
import { ACTIVE_STATUSES, type Dashboard, type StatusCounts } from "../../types/api";
import { useCurrentUser, useIsManagerSomewhere } from "../auth/auth";
import { useWorkItems } from "../work-items/api";
import { WorkItemTable } from "../work-items/WorkItemTable";

type Tile = { key: keyof StatusCounts; label: string; href: string; tone?: "alert" | "warn" };

const TILES: Tile[] = [
  { key: "active", label: "Active", href: "/work-items?status=open&status=in_progress&status=blocked&status=resolved" },
  { key: "open", label: "Open", href: "/work-items?status=open" },
  { key: "in_progress", label: "In progress", href: "/work-items?status=in_progress" },
  { key: "blocked", label: "Blocked", href: "/work-items?status=blocked", tone: "alert" },
  { key: "overdue", label: "Overdue", href: "/work-items?overdue=true", tone: "alert" },
  { key: "high_priority", label: "High / critical", href: "/work-items?priority=high&priority=critical&status=open&status=in_progress&status=blocked&status=resolved", tone: "warn" },
  { key: "resolved", label: "Awaiting approval", href: "/work-items?status=resolved", tone: "warn" },
  { key: "assigned_to_me", label: "Assigned to me", href: "/work-items?assignee=me&status=open&status=in_progress&status=blocked&status=resolved" },
];

export function DashboardPage() {
  const me = useCurrentUser();
  const isManager = useIsManagerSomewhere();
  const summary = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/dashboard/summary"),
    refetchInterval: 30_000,
  });

  return (
    <>
      <PageHeader title={`Good to see you, ${me.full_name.split(" ")[0]}`} subtitle="What needs attention across your teams." />

      {summary.isLoading ? (
        <Loading label="Loading overview" />
      ) : summary.error ? (
        <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
      ) : summary.data ? (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-8">
            {TILES.map((t) => {
              const n = summary.data.totals[t.key];
              return (
                <Link
                  key={t.key}
                  to={t.href}
                  className="rounded-lg border border-ink-200 bg-white px-3 py-3 transition-colors hover:border-ink-400"
                >
                  <div className="text-xs text-ink-500">{t.label}</div>
                  <div
                    className={cx(
                      "mt-1 text-2xl font-semibold tabular",
                      n > 0 && t.tone === "alert" && "text-red-600",
                      n > 0 && t.tone === "warn" && "text-amber-600",
                    )}
                  >
                    {n.toLocaleString()}
                  </div>
                </Link>
              );
            })}
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ItemPanel
              title="My active work"
              subtitle="Assigned to you, highest priority first"
              query={{ assignee: "me", status: ACTIVE_STATUSES, sort: "-priority", limit: 6 }}
              more="/work-items?assignee=me&status=open&status=in_progress&status=blocked&status=resolved&sort=-priority"
              empty="Nothing assigned to you. Claim an unassigned item to get started."
            />
            <ItemPanel
              title="Overdue"
              subtitle="Past their due date and not finished"
              query={{ overdue: true, sort: "due_date", limit: 6 }}
              more="/work-items?overdue=true&sort=due_date"
              empty="Nothing is overdue."
            />
            {isManager && (
              <ItemPanel
                title="Awaiting your approval"
                subtitle="Resolved items a manager needs to close or reject"
                query={{ status: ["resolved"], sort: "-updated_at", limit: 6 }}
                more="/work-items?status=resolved"
                empty="No resolutions waiting for approval."
              />
            )}
            <ItemPanel
              title="Unassigned & urgent"
              subtitle="High or critical items nobody owns yet"
              query={{ assignee: "none", priority: ["critical", "high"], status: ["open"], sort: "-priority", limit: 6 }}
              more="/work-items?assignee=none&priority=critical&priority=high&status=open"
              empty="Every urgent item has an owner."
            />
            <ItemPanel
              title="Recently updated"
              query={{ sort: "-updated_at", limit: 6 }}
              more="/work-items"
              empty="No work items yet."
            />
            <Card>
              <CardHeader title="By team" />
              {summary.data.by_team.length === 0 ? (
                <EmptyState title="You're not in any team yet" />
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase tracking-wide text-ink-400">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium">Team</th>
                      <th className="px-2 py-2 text-right font-medium">Active</th>
                      <th className="px-2 py-2 text-right font-medium">Blocked</th>
                      <th className="px-2 py-2 text-right font-medium">Overdue</th>
                      <th className="px-4 py-2 text-right font-medium">Unassigned</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-100 tabular">
                    {summary.data.by_team.map((t) => (
                      <tr key={t.team.id} className="hover:bg-ink-50">
                        <td className="px-4 py-2">
                          <Link to={`/work-items?team_id=${t.team.id}`} className="font-medium hover:text-accent-700">
                            {t.team.name}
                          </Link>
                        </td>
                        <td className="px-2 py-2 text-right">{t.active}</td>
                        <td className={cx("px-2 py-2 text-right", t.blocked > 0 && "text-red-600")}>{t.blocked}</td>
                        <td className={cx("px-2 py-2 text-right", t.overdue > 0 && "font-medium text-red-600")}>{t.overdue}</td>
                        <td className="px-4 py-2 text-right">{t.unassigned}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        </>
      ) : null}
    </>
  );
}

function ItemPanel({ title, subtitle, query, more, empty }: { title: string; subtitle?: string; query: Query; more: string; empty: string }) {
  const { data, isLoading, error, refetch } = useWorkItems(query);
  return (
    <Card>
      <CardHeader
        title={
          <>
            {title}
            {data?.total != null && <span className="ml-2 rounded-full bg-ink-100 px-1.5 py-0.5 text-xs font-medium text-ink-600">{data.total}</span>}
          </>
        }
        subtitle={subtitle}
        action={
          <Link to={more} className="text-xs text-accent-700 hover:underline">
            View all
          </Link>
        }
      />
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : data?.items.length ? (
        <WorkItemTable items={data.items} compact />
      ) : (
        <p className="px-4 py-6 text-center text-sm text-ink-500">{empty}</p>
      )}
    </Card>
  );
}
