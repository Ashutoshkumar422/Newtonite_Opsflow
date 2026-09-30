import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/AppShell";
import { Button, Card, EmptyState, ErrorState, Input, Loading, Select, Spinner, cx } from "../../components/ui";
import { PRIORITY_LABEL, STATUS_LABEL } from "../../lib/format";
import { ACTIVE_STATUSES, PRIORITIES, STATUSES, type Priority, type Status } from "../../types/api";
import { useCurrentUser } from "../auth/auth";
import { useTeams } from "../teams/api";
import { useWorkItems } from "./api";
import { WorkItemTable } from "./WorkItemTable";

const SORTS = [
  ["-updated_at", "Recently updated"],
  ["-created_at", "Newest"],
  ["-priority", "Priority"],
  ["due_date", "Due date"],
] as const;
const PAGE_SIZE = 25;

/**
 * Filters live in the URL (shareable, back-button friendly). The server does all filtering,
 * searching and paging; the browser only ever holds one page.
 */
export function WorkItemsPage() {
  const [params, setParams] = useSearchParams();
  const me = useCurrentUser();
  const teams = useTeams();

  const status = params.getAll("status") as Status[];
  const priority = params.getAll("priority") as Priority[];
  const filters = {
    q: params.get("q") ?? "",
    team_id: params.get("team_id") ?? "",
    assignee: params.get("assignee") ?? "",
    overdue: params.get("overdue") === "true",
    sort: params.get("sort") ?? "-updated_at",
  };

  // Debounced search box → URL
  const [search, setSearch] = useState(filters.q);
  useEffect(() => setSearch(filters.q), [filters.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (search !== filters.q) update({ q: search || null });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // Keyset pagination: keep the cursors we've followed so "Previous" works.
  const filterKey = params.toString();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  useEffect(() => setCursors([null]), [filterKey]);
  const cursor = cursors[cursors.length - 1];

  const query = useMemo(
    () => ({
      q: filters.q || undefined,
      team_id: filters.team_id || undefined,
      assignee: filters.assignee || undefined,
      status: status.length ? status : undefined,
      priority: priority.length ? priority : undefined,
      overdue: filters.overdue || undefined,
      sort: filters.sort,
      limit: PAGE_SIZE,
      cursor: cursor ?? undefined,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filterKey, cursor],
  );
  const { data, error, isLoading, isFetching, refetch } = useWorkItems(query);

  function update(changes: Record<string, string | string[] | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      next.delete(k);
      if (Array.isArray(v)) v.forEach((x) => next.append(k, x));
      else if (v) next.set(k, v);
    }
    setParams(next, { replace: true });
  }

  function toggle(key: "status" | "priority", value: string, current: string[]) {
    update({ [key]: current.includes(value) ? current.filter((x) => x !== value) : [...current, value] });
  }

  const hasFilters = params.toString().length > 0 && !(params.size === 1 && params.has("sort"));
  const page = cursors.length;

  return (
    <>
      <PageHeader
        title="Work items"
        subtitle={data?.total != null ? `${data.total.toLocaleString()} matching item${data.total === 1 ? "" : "s"}` : " "}
        actions={
          <Link to="/work-items/new">
            <Button variant="primary">New work item</Button>
          </Link>
        }
      />

      <Card className="mb-4 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-56 flex-1">
            <Input
              aria-label="Search work items"
              placeholder="Search title and description, or jump to a key like PAY-12"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {isFetching && !isLoading && <Spinner className="absolute right-3 top-2.5 h-4 w-4 text-ink-400" />}
          </div>
          <Select aria-label="Team" value={filters.team_id} onChange={(e) => update({ team_id: e.target.value })} className="w-auto">
            <option value="">All my teams</option>
            {teams.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Assignee" value={filters.assignee} onChange={(e) => update({ assignee: e.target.value })} className="w-auto">
            <option value="">Anyone</option>
            <option value="me">Assigned to me</option>
            <option value="none">Unassigned</option>
          </Select>
          <Select aria-label="Sort" value={filters.sort} onChange={(e) => update({ sort: e.target.value })} className="w-auto">
            {SORTS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </Select>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Status filter">
          {STATUSES.map((s) => (
            <Chip key={s} active={status.includes(s)} onClick={() => toggle("status", s, status)}>
              {STATUS_LABEL[s]}
            </Chip>
          ))}
          <Chip
            active={status.length === ACTIVE_STATUSES.length && ACTIVE_STATUSES.every((s) => status.includes(s))}
            onClick={() => update({ status: ACTIVE_STATUSES })}
          >
            All active
          </Chip>
          <span className="mx-1 h-4 w-px bg-ink-200" aria-hidden />
          {PRIORITIES.map((p) => (
            <Chip key={p} active={priority.includes(p)} onClick={() => toggle("priority", p, priority)}>
              {PRIORITY_LABEL[p]}
            </Chip>
          ))}
          <span className="mx-1 h-4 w-px bg-ink-200" aria-hidden />
          <Chip active={filters.overdue} onClick={() => update({ overdue: filters.overdue ? null : "true" })}>
            Overdue
          </Chip>
          {hasFilters && (
            <button className="ml-auto text-xs text-ink-500 hover:text-ink-900" onClick={() => setParams({}, { replace: true })}>
              Clear filters
            </button>
          )}
        </div>
      </Card>

      <Card>
        {isLoading ? (
          <Loading label="Loading work items" />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data || data.items.length === 0 ? (
          <EmptyState
            title={hasFilters ? "No work items match these filters" : "No work items yet"}
            body={
              hasFilters
                ? "Try removing a filter or searching for something else."
                : me.memberships.length
                  ? "Create the first work item for your team."
                  : "You are not a member of any team yet. Ask a team manager to add you."
            }
          />
        ) : (
          <div className={cx(isFetching && "opacity-70 transition-opacity")}>
            <WorkItemTable items={data.items} />
          </div>
        )}
        <div className="flex items-center justify-between border-t border-ink-100 px-4 py-2.5 text-xs text-ink-500">
          <span>Page {page}</span>
          <div className="flex gap-2">
            <Button size="sm" disabled={page === 1} onClick={() => setCursors((c) => c.slice(0, -1))}>
              ← Previous
            </Button>
            <Button
              size="sm"
              disabled={!data?.next_cursor}
              onClick={() => data?.next_cursor && setCursors((c) => [...c, data.next_cursor])}
            >
              Next →
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
        active ? "border-ink-900 bg-ink-900 text-white" : "border-ink-200 bg-white text-ink-600 hover:border-ink-400",
      )}
    >
      {children}
    </button>
  );
}
