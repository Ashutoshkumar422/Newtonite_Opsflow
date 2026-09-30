import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cx } from "../../components/ui";
import { api } from "../../lib/api";
import { relativeTime } from "../../lib/format";
import type { NotificationPage } from "../../types/api";

export function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<NotificationPage>("/notifications", { query: { limit: 15 } }),
    refetchInterval: 20_000,
  });
  const markRead = useMutation({
    mutationFn: (id: number) => api<void>(`/notifications/${id}/read`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const markAll = useMutation({
    mutationFn: () => api<void>("/notifications/read-all", { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const unread = data?.unread_count ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
        aria-expanded={open}
        className="relative rounded-md p-2 text-ink-500 hover:bg-ink-100 hover:text-ink-900"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" strokeLinecap="round" />
        </svg>
        {unread > 0 && (
          <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-96 overflow-hidden rounded-lg border border-ink-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-ink-100 px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            {unread > 0 && (
              <button className="text-xs text-accent-700 hover:underline" onClick={() => markAll.mutate()}>
                Mark all read
              </button>
            )}
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {data?.items.length === 0 && <li className="px-4 py-8 text-center text-sm text-ink-500">You're all caught up.</li>}
            {data?.items.map((n) => (
              <li key={n.id}>
                <button
                  className={cx(
                    "block w-full border-b border-ink-50 px-4 py-2.5 text-left text-sm hover:bg-ink-50",
                    !n.read_at && "bg-accent-50/60",
                  )}
                  onClick={() => {
                    if (!n.read_at) markRead.mutate(n.id);
                    setOpen(false);
                    if (n.work_item_id) navigate(`/work-items/${n.work_item_id}`);
                  }}
                >
                  <div className="text-ink-800">{n.message}</div>
                  <div className="mt-0.5 text-xs text-ink-400">{relativeTime(n.created_at)}</div>
                </button>
              </li>
            ))}
          </ul>
          <p className="border-t border-ink-100 px-4 py-2 text-[11px] text-ink-400">
            Delivered asynchronously by the outbox worker.
          </p>
        </div>
      )}
    </div>
  );
}
