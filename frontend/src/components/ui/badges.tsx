import { PRIORITY_LABEL, STATUS_LABEL } from "../../lib/format";
import type { Priority, Status } from "../../types/api";
import { cx } from "./index";

const STATUS_STYLE: Record<Status, string> = {
  open: "bg-sky-50 text-sky-700 ring-sky-200",
  in_progress: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  blocked: "bg-red-50 text-red-700 ring-red-200",
  resolved: "bg-amber-50 text-amber-800 ring-amber-200",
  closed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  cancelled: "bg-ink-100 text-ink-500 ring-ink-200",
};

const STATUS_DOT: Record<Status, string> = {
  open: "bg-sky-500",
  in_progress: "bg-indigo-500",
  blocked: "bg-red-500",
  resolved: "bg-amber-500",
  closed: "bg-emerald-500",
  cancelled: "bg-ink-400",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        STATUS_STYLE[status],
      )}
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", STATUS_DOT[status])} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

// Priority uses shape (bar count) as well as colour, so it is readable without colour vision.
const PRIORITY_BARS: Record<Priority, number> = { low: 1, medium: 2, high: 3, critical: 4 };
const PRIORITY_COLOR: Record<Priority, string> = {
  low: "bg-ink-300",
  medium: "bg-amber-400",
  high: "bg-orange-500",
  critical: "bg-red-600",
};

export function PriorityBadge({ priority, withLabel = true }: { priority: Priority; withLabel?: boolean }) {
  const n = PRIORITY_BARS[priority];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-ink-700" title={`${PRIORITY_LABEL[priority]} priority`}>
      <span className="flex items-end gap-px" aria-hidden>
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className={cx("w-[3px] rounded-sm", i <= n ? PRIORITY_COLOR[priority] : "bg-ink-200")}
            style={{ height: `${4 + i * 2.5}px` }}
          />
        ))}
      </span>
      {withLabel ? PRIORITY_LABEL[priority] : <span className="sr-only">{PRIORITY_LABEL[priority]}</span>}
    </span>
  );
}
