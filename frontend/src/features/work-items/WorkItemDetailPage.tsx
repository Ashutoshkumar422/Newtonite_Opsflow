import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Avatar, Button, Card, CardHeader, ErrorState, Field, Loading, Modal, Select, Textarea, cx, useToast } from "../../components/ui";
import { PriorityBadge, StatusBadge } from "../../components/ui/badges";
import { ApiError, errorMessage } from "../../lib/api";
import { STATUS_LABEL, formatDateTime, relativeTime } from "../../lib/format";
import type { Status, TransitionOption, WorkItem } from "../../types/api";
import { useCurrentUser } from "../auth/auth";
import { useMembers } from "../teams/api";
import { useAssign, useClaim, useTransition, useWorkItem } from "./api";
import { EditItemForm } from "./EditItemForm";
import { ActivityTimeline, Comments } from "./Timeline";
import { DueDate } from "./WorkItemTable";

const DESTRUCTIVE: Status[] = ["cancelled"];

export function WorkItemDetailPage() {
  const id = Number(useParams().id);
  const { data: item, error, isLoading, refetch } = useWorkItem(id);
  const [editing, setEditing] = useState(false);
  const [tab, setTab] = useState<"comments" | "history">("comments");

  if (isLoading) return <Loading label="Loading work item" />;
  if (error) {
    if (error instanceof ApiError && error.status === 404)
      return (
        <Card className="p-8 text-center">
          <div className="font-medium">Work item not found</div>
          <p className="mt-1 text-sm text-ink-500">It doesn't exist, or it belongs to a team you're not a member of.</p>
          <Link to="/work-items" className="mt-3 inline-block text-sm text-accent-700 hover:underline">
            Back to work items
          </Link>
        </Card>
      );
    return <ErrorState error={error} onRetry={() => refetch()} />;
  }
  if (!item) return null;

  return (
    <div className="mx-auto max-w-6xl">
      <nav className="mb-3 text-xs text-ink-500" aria-label="Breadcrumb">
        <Link to="/work-items" className="hover:text-ink-900">
          Work items
        </Link>{" "}
        /{" "}
        <Link to={`/work-items?team_id=${item.team.id}`} className="hover:text-ink-900">
          {item.team.name}
        </Link>{" "}
        / <span className="font-mono">{item.key}</span>
      </nav>

      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm text-ink-400">{item.key}</span>
            <StatusBadge status={item.status} />
            <PriorityBadge priority={item.priority} />
            {item.is_overdue && <span className="rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-semibold uppercase text-white">Overdue</span>}
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink-900">{item.title}</h1>
        </div>
        <ActionBar item={item} onEdit={() => setEditing(true)} editing={editing} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader title={editing ? "Edit details" : "Description"} />
            <div className="p-4">
              {editing ? (
                <EditItemForm item={item} onDone={() => setEditing(false)} />
              ) : item.description ? (
                <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-800">{item.description}</p>
              ) : (
                <p className="text-sm italic text-ink-400">No description.</p>
              )}
            </div>
          </Card>

          <Card>
            <div role="tablist" aria-label="Item collaboration" className="flex gap-4 border-b border-ink-100 px-4">
              {(["comments", "history"] as const).map((t) => (
                <button
                  key={t}
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                  className={cx(
                    "-mb-px border-b-2 py-2.5 text-sm",
                    tab === t ? "border-ink-900 font-medium text-ink-900" : "border-transparent text-ink-500 hover:text-ink-800",
                  )}
                >
                  {t === "comments" ? "Discussion" : "History"}
                </button>
              ))}
            </div>
            <div role="tabpanel">
              {tab === "comments" ? <Comments itemId={item.id} canComment={item.permissions.can_comment} /> : <ActivityTimeline itemId={item.id} />}
            </div>
          </Card>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader title="Details" />
            <dl className="divide-y divide-ink-100 text-sm">
              <Row label="Status">{STATUS_LABEL[item.status]}</Row>
              <Row label="Assignee">
                <AssigneeControl item={item} />
              </Row>
              <Row label="Reporter">
                <span className="inline-flex items-center gap-1.5">
                  <Avatar name={item.reporter.full_name} /> {item.reporter.full_name}
                </span>
              </Row>
              <Row label="Team">
                <Link className="text-accent-700 hover:underline" to={`/teams/${item.team.id}`}>
                  {item.team.name}
                </Link>
              </Row>
              <Row label="Due">
                <DueDate item={item} />
              </Row>
              <Row label="Created">
                <span title={formatDateTime(item.created_at)}>{relativeTime(item.created_at)}</span>
              </Row>
              <Row label="Updated">
                <span title={formatDateTime(item.updated_at)}>{relativeTime(item.updated_at)}</span>
              </Row>
              <Row label="Version">
                <span className="font-mono text-xs text-ink-500">v{item.version}</span>
              </Row>
            </dl>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5">
      <dt className="text-xs text-ink-500">{label}</dt>
      <dd className="min-w-0 text-right text-ink-800">{children}</dd>
    </div>
  );
}

function ActionBar({ item, onEdit, editing }: { item: WorkItem; onEdit: () => void; editing: boolean }) {
  const toast = useToast();
  const claim = useClaim(item.id);
  const transition = useTransition(item.id);
  const [pending, setPending] = useState<TransitionOption | null>(null);
  const [reason, setReason] = useState("");
  const p = item.permissions;

  function run(t: TransitionOption, why?: string) {
    transition.mutate(
      { version: item.version, to_status: t.to, reason: why || undefined },
      {
        onSuccess: (updated) => {
          toast("success", `${updated.key} moved to ${STATUS_LABEL[updated.status]}`);
          setPending(null);
          setReason("");
        },
        onError: (e) => {
          const msg =
            e instanceof ApiError && e.code === "version_conflict"
              ? "Someone else changed this item first. It has been refreshed — review it and try again."
              : errorMessage(e);
          toast("error", msg);
          setPending(null);
        },
      },
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {p.can_claim && (
        <Button
          variant="primary"
          loading={claim.isPending}
          onClick={() =>
            claim.mutate(undefined, {
              onSuccess: () => toast("success", "You now own this item"),
              onError: (e) => toast("error", errorMessage(e)),
            })
          }
        >
          Claim
        </Button>
      )}
      {p.transitions.map((t) => (
        <Button
          key={t.to}
          variant={DESTRUCTIVE.includes(t.to) ? "danger" : t.to === "closed" ? "primary" : "secondary"}
          loading={transition.isPending && transition.variables?.to_status === t.to}
          disabled={transition.isPending}
          onClick={() => (t.requires_reason || DESTRUCTIVE.includes(t.to) ? setPending(t) : run(t))}
        >
          {t.label}
        </Button>
      ))}
      {p.can_edit && !editing && (
        <Button variant="ghost" onClick={onEdit}>
          Edit
        </Button>
      )}

      <Modal open={!!pending} title={pending ? `${pending.label}: ${item.key}` : ""} onClose={() => setPending(null)}>
        {pending && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (pending.requires_reason && !reason.trim()) return;
              run(pending, reason.trim());
            }}
          >
            {DESTRUCTIVE.includes(pending.to) && (
              <p className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
                Cancelling is final — cancelled items cannot be reopened or edited.
              </p>
            )}
            <Field label={pending.requires_reason ? "Reason (required, recorded in history)" : "Reason (optional)"} htmlFor="reason">
              <Textarea id="reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
            </Field>
            <div className="mt-4 flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setPending(null)}>
                Back
              </Button>
              <Button
                type="submit"
                variant={DESTRUCTIVE.includes(pending.to) ? "danger" : "primary"}
                loading={transition.isPending}
                disabled={pending.requires_reason && !reason.trim()}
              >
                {pending.label}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

function AssigneeControl({ item }: { item: WorkItem }) {
  const me = useCurrentUser();
  const toast = useToast();
  const assign = useAssign(item.id);
  const canChange = item.permissions.can_assign || item.permissions.can_release;
  const members = useMembers(item.permissions.can_assign ? item.team.id : undefined);

  const onChange = (value: string) => {
    const assignee_id = value === "" ? null : Number(value);
    assign.mutate(
      { version: item.version, assignee_id },
      {
        onSuccess: (u) => toast("success", u.assignee ? `Assigned to ${u.assignee.full_name}` : "Unassigned"),
        onError: (e) => toast("error", errorMessage(e)),
      },
    );
  };

  if (item.permissions.can_assign) {
    return (
      <Select
        aria-label="Assignee"
        className="h-8 w-44 text-xs"
        value={item.assignee?.id ?? ""}
        disabled={assign.isPending || members.isLoading}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Unassigned</option>
        {members.data?.map((m) => (
          <option key={m.user.id} value={m.user.id}>
            {m.user.full_name}
            {m.user.id === me.id ? " (me)" : ""}
          </option>
        ))}
      </Select>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      {item.assignee ? (
        <>
          <Avatar name={item.assignee.full_name} /> {item.assignee.full_name}
        </>
      ) : (
        <em className="text-ink-400">Unassigned</em>
      )}
      {canChange && item.permissions.can_release && (
        <Button size="sm" variant="ghost" loading={assign.isPending} onClick={() => onChange("")}>
          Release
        </Button>
      )}
    </span>
  );
}
