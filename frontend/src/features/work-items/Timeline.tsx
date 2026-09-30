import { useState } from "react";
import { Avatar, Button, EmptyState, ErrorState, Loading, Textarea, useToast } from "../../components/ui";
import { StatusBadge } from "../../components/ui/badges";
import { errorMessage, newIdempotencyKey } from "../../lib/api";
import { PRIORITY_LABEL, formatDate, formatDateTime, relativeTime } from "../../lib/format";
import type { Activity, Priority, Status } from "../../types/api";
import { useActivity, useAddComment, useComments, useEditComment } from "./api";

type Change = { from: unknown; to: unknown };

function renderValue(field: string, v: unknown) {
  if (v === null || v === undefined || v === "") return <em className="text-ink-400">none</em>;
  if (field === "status") return <StatusBadge status={v as Status} />;
  if (field === "priority") return <strong>{PRIORITY_LABEL[v as Priority] ?? String(v)}</strong>;
  if (field === "assignee") return <strong>{(v as { name: string }).name}</strong>;
  if (field === "due_date") return <strong>{formatDate(String(v))}</strong>;
  const s = String(v);
  return <strong className="break-words">{s.length > 80 ? `${s.slice(0, 80)}…` : s}</strong>;
}

const VERB: Record<string, string> = {
  created: "created this item",
  updated: "updated",
  claimed: "claimed this item",
  assigned: "changed the assignee",
  unassigned: "unassigned this item",
  status_changed: "changed the status",
  commented: "commented",
};

function ActivityRow({ a }: { a: Activity }) {
  const changes = Object.entries(a.changes).filter(
    ([k, v]) => typeof v === "object" && v !== null && "to" in (v as object) && k !== "reason",
  ) as [string, Change][];
  const reason = a.changes.reason as string | undefined;
  return (
    <li className="relative flex gap-3 pb-5 last:pb-0">
      <span className="absolute left-3 top-7 -ml-px h-[calc(100%-1.5rem)] w-px bg-ink-200" aria-hidden />
      <Avatar name={a.actor.full_name} />
      <div className="min-w-0 flex-1 text-sm">
        <div className="text-ink-700">
          <span className="font-medium text-ink-900">{a.actor.full_name}</span> {VERB[a.action] ?? a.action}
          <time className="ml-2 text-xs text-ink-400" dateTime={a.created_at} title={formatDateTime(a.created_at)}>
            {relativeTime(a.created_at)}
          </time>
        </div>
        {a.action !== "created" && a.action !== "commented" && changes.length > 0 && (
          <ul className="mt-1 space-y-1 text-xs text-ink-600">
            {changes.map(([field, c]) => (
              <li key={field} className="flex flex-wrap items-center gap-1.5">
                <span className="capitalize text-ink-500">{field.replace("_", " ")}:</span>
                {renderValue(field, c.from)} <span aria-label="changed to">→</span> {renderValue(field, c.to)}
              </li>
            ))}
          </ul>
        )}
        {a.action === "commented" && typeof a.changes.excerpt === "string" && (
          <p className="mt-1 truncate text-xs text-ink-500">“{a.changes.excerpt}”</p>
        )}
        {reason && <p className="mt-1 rounded bg-ink-50 px-2 py-1 text-xs text-ink-700">Reason: {reason}</p>}
      </div>
    </li>
  );
}

export function ActivityTimeline({ itemId }: { itemId: number }) {
  const { data, isLoading, error, refetch } = useActivity(itemId);
  if (isLoading) return <Loading label="Loading history" />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (!data?.items.length) return <EmptyState title="No history yet" />;
  return (
    <ol className="px-4 py-4" aria-label="Activity history">
      {data.items.map((a) => (
        <ActivityRow key={a.id} a={a} />
      ))}
    </ol>
  );
}

export function Comments({ itemId, canComment }: { itemId: number; canComment: boolean }) {
  const { data, isLoading, error, refetch } = useComments(itemId);
  const add = useAddComment(itemId);
  const edit = useEditComment(itemId);
  const toast = useToast();
  const [body, setBody] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);

  function post() {
    if (!body.trim()) return;
    add.mutate(
      { body: body.trim(), key },
      {
        onSuccess: () => {
          setBody("");
          setKey(newIdempotencyKey()); // next comment is a new logical operation
        },
        onError: (e) => toast("error", errorMessage(e)),
      },
    );
  }

  return (
    <div>
      {isLoading ? (
        <Loading label="Loading comments" />
      ) : error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : data?.items.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-ink-500">No comments yet.</p>
      ) : (
        <ul className="divide-y divide-ink-100">
          {data?.items.map((c) => (
            <li key={c.id} className="flex gap-3 px-4 py-3">
              <Avatar name={c.author.full_name} />
              <div className="min-w-0 flex-1">
                <div className="text-xs text-ink-500">
                  <span className="font-medium text-ink-900">{c.author.full_name}</span> · {relativeTime(c.created_at)}
                  {c.edited_at && <span title={formatDateTime(c.edited_at)}> · edited</span>}
                  {c.can_edit && editing?.id !== c.id && (
                    <button className="ml-2 text-accent-700 hover:underline" onClick={() => setEditing({ id: c.id, body: c.body })}>
                      Edit
                    </button>
                  )}
                </div>
                {editing?.id === c.id ? (
                  <div className="mt-2 space-y-2">
                    <Textarea
                      aria-label="Edit comment"
                      rows={3}
                      value={editing.body}
                      onChange={(e) => setEditing({ id: c.id, body: e.target.value })}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="primary"
                        loading={edit.isPending}
                        disabled={!editing.body.trim()}
                        onClick={() =>
                          edit.mutate(
                            { commentId: c.id, body: editing.body.trim() },
                            { onSuccess: () => setEditing(null), onError: (e) => toast("error", errorMessage(e)) },
                          )
                        }
                      >
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink-800">{c.body}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {canComment && (
        <div className="border-t border-ink-100 p-4">
          <Textarea
            aria-label="Add a comment"
            rows={3}
            placeholder="Add an update, question or decision…"
            value={body}
            maxLength={10000}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) post();
            }}
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-ink-400">Ctrl/⌘ + Enter to post</span>
            <Button size="sm" variant="primary" loading={add.isPending} disabled={!body.trim()} onClick={post}>
              Comment
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
