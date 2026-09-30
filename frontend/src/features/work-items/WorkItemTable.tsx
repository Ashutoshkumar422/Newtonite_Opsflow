import { Link } from "react-router-dom";
import { Avatar, cx } from "../../components/ui";
import { PriorityBadge, StatusBadge } from "../../components/ui/badges";
import { formatDate, relativeTime } from "../../lib/format";
import type { WorkItemSummary } from "../../types/api";

export function DueDate({ item }: { item: Pick<WorkItemSummary, "due_date" | "is_overdue"> }) {
  if (!item.due_date) return <span className="text-ink-300">—</span>;
  return (
    <span className={cx("whitespace-nowrap tabular", item.is_overdue ? "font-medium text-red-600" : "text-ink-600")}>
      {item.is_overdue && <span className="sr-only">Overdue: </span>}
      {formatDate(item.due_date)}
    </span>
  );
}

export function Assignee({ user }: { user: WorkItemSummary["assignee"] }) {
  if (!user) return <span className="text-xs italic text-ink-400">Unassigned</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm text-ink-700">
      <Avatar name={user.full_name} /> {user.full_name}
    </span>
  );
}

/** Dense table for scanning many items. Columns collapse on narrow screens. */
export function WorkItemTable({ items, compact = false }: { items: WorkItemSummary[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-ink-100 text-xs font-medium uppercase tracking-wide text-ink-400">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">
              Item
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Status
            </th>
            <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">
              Priority
            </th>
            {!compact && (
              <th scope="col" className="hidden px-3 py-2 font-medium md:table-cell">
                Assignee
              </th>
            )}
            <th scope="col" className={cx("hidden px-3 py-2 font-medium lg:table-cell", compact && "pr-4")}>
              Due
            </th>
            {!compact && (
              <th scope="col" className="hidden px-4 py-2 text-right font-medium md:table-cell">
                Updated
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {items.map((it) => (
            <tr key={it.id} className="group hover:bg-ink-50">
              <td className="w-full max-w-0 px-4 py-2.5">
                <Link to={`/work-items/${it.id}`} className="flex min-w-0 items-baseline gap-2" title={it.title}>
                  <span className="shrink-0 font-mono text-xs text-ink-400">{it.key}</span>
                  <span className="truncate font-medium text-ink-900 group-hover:text-accent-700">{it.title}</span>
                </Link>
              </td>
              <td className="whitespace-nowrap px-3 py-2.5">
                <StatusBadge status={it.status} />
              </td>
              <td className="hidden px-3 py-2.5 sm:table-cell">
                <PriorityBadge priority={it.priority} withLabel={!compact} />
              </td>
              {!compact && (
                <td className="hidden px-3 py-2.5 md:table-cell">
                  <Assignee user={it.assignee} />
                </td>
              )}
              <td className="hidden px-3 py-2.5 lg:table-cell">
                <DueDate item={it} />
              </td>
              {!compact && (
                <td className="hidden whitespace-nowrap px-4 py-2.5 text-right text-xs text-ink-500 md:table-cell" title={it.updated_at}>
                  {relativeTime(it.updated_at)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
