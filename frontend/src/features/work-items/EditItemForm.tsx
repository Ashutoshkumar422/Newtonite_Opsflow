import { useState } from "react";
import { Button, Field, Input, Select, Textarea, cx } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { PRIORITY_LABEL, formatDate, todayIso } from "../../lib/format";
import { PRIORITIES, type Priority, type WorkItem } from "../../types/api";
import { useUpdateItem, type ItemPatch } from "./api";

type Draft = { title: string; description: string; priority: Priority; due_date: string };
const FIELDS: (keyof Draft)[] = ["title", "description", "priority", "due_date"];
const LABEL: Record<keyof Draft, string> = { title: "Title", description: "Description", priority: "Priority", due_date: "Due date" };

function toDraft(item: WorkItem): Draft {
  return { title: item.title, description: item.description, priority: item.priority, due_date: item.due_date ?? "" };
}

function diff(base: Draft, draft: Draft): ItemPatch {
  const patch: ItemPatch = {};
  for (const f of FIELDS) {
    if (base[f] === draft[f]) continue;
    if (f === "due_date") patch.due_date = draft.due_date || null;
    else if (f === "priority") patch.priority = draft.priority;
    else patch[f] = draft[f];
  }
  return patch;
}

function show(field: keyof Draft, value: string) {
  if (!value) return <span className="italic text-ink-400">empty</span>;
  if (field === "priority") return PRIORITY_LABEL[value as Priority];
  if (field === "due_date") return formatDate(value);
  return value.length > 120 ? `${value.slice(0, 120)}…` : value;
}

/**
 * Optimistic-concurrency-aware editor.
 *
 * `base` is the server state when editing began; only fields the user actually changed are sent,
 * together with base.version. If someone else saved in the meantime the server answers 409 with
 * its current state, and we show both versions side by side. The user's draft is never discarded
 * silently: they can re-apply their changes on top of the latest version, or discard them.
 */
export function EditItemForm({ item, onDone }: { item: WorkItem; onDone: () => void }) {
  const [base, setBase] = useState<WorkItem>(item);
  const [draft, setDraft] = useState<Draft>(() => toDraft(item));
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const update = useUpdateItem(item.id);

  const patch = diff(toDraft(base), draft);
  const dirty = Object.keys(patch).length > 0;
  const changedUnderneath = !conflict && item.version > base.version;
  const fieldErrors = update.error instanceof ApiError ? update.error.fieldErrors() : {};
  const titleError = !draft.title.trim() ? "Title is required" : fieldErrors.title;
  const dueError = draft.due_date && draft.due_date !== base.due_date && draft.due_date < todayIso() ? "Due date cannot be in the past" : fieldErrors.due_date;

  function save(version: number, p: ItemPatch) {
    update.mutate(
      { version, patch: p },
      {
        onSuccess: () => onDone(),
        onError: (err) => {
          if (err instanceof ApiError && err.code === "version_conflict") {
            setConflict(err.details.current as WorkItem);
          }
        },
      },
    );
  }

  function reapplyMine() {
    // Keep the user's edited fields, rebase everything else on the latest server state.
    const latest = conflict!;
    setBase(latest);
    setConflict(null);
    save(latest.version, patch);
  }

  function discardMine() {
    const latest = conflict ?? item;
    setBase(latest);
    setDraft(toDraft(latest));
    setConflict(null);
    update.reset();
  }

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!titleError && !dueError && dirty) save(base.version, patch);
      }}
    >
      {changedUnderneath && (
        <div role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Someone updated this item (now version {item.version}) since you started editing. Saving will show you what changed.
        </div>
      )}

      {conflict && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm">
          <div className="font-medium text-red-900">Your changes were not saved — this item changed while you were editing.</div>
          <p className="mt-1 text-red-800">Compare the latest version with yours, then choose how to continue.</p>
          <table className="mt-3 w-full text-left text-xs">
            <thead className="text-ink-500">
              <tr>
                <th className="py-1 pr-2 font-medium">Field</th>
                <th className="py-1 pr-2 font-medium">Latest (v{conflict.version})</th>
                <th className="py-1 font-medium">Yours</th>
              </tr>
            </thead>
            <tbody className="align-top">
              {FIELDS.filter((f) => f in patch || toDraft(conflict)[f] !== toDraft(base)[f]).map((f) => {
                const theirs = toDraft(conflict)[f];
                const mine = draft[f];
                return (
                  <tr key={f} className="border-t border-red-100">
                    <td className="py-1.5 pr-2 font-medium text-ink-700">{LABEL[f]}</td>
                    <td className={cx("py-1.5 pr-2", theirs !== toDraft(base)[f] && "font-medium text-ink-900")}>{show(f, theirs)}</td>
                    <td className={cx("py-1.5", f in patch && "font-medium text-ink-900")}>{f in patch ? show(f, mine) : <span className="text-ink-400">unchanged</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="primary" onClick={reapplyMine} loading={update.isPending}>
              Apply my changes on top of latest
            </Button>
            <Button type="button" size="sm" onClick={discardMine}>
              Discard mine, use latest
            </Button>
          </div>
        </div>
      )}

      <Field label="Title" htmlFor="edit-title" error={titleError}>
        <Input id="edit-title" value={draft.title} maxLength={200} invalid={!!titleError} onChange={(e) => set("title", e.target.value)} />
      </Field>
      <Field label="Description" htmlFor="edit-desc">
        <Textarea id="edit-desc" rows={7} value={draft.description} onChange={(e) => set("description", e.target.value)} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Priority" htmlFor="edit-priority">
          <Select id="edit-priority" value={draft.priority} onChange={(e) => set("priority", e.target.value as Priority)}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Due date" htmlFor="edit-due" error={dueError}>
          <Input id="edit-due" type="date" value={draft.due_date} invalid={!!dueError} onChange={(e) => set("due_date", e.target.value)} />
        </Field>
      </div>
      {update.error && !(update.error instanceof ApiError && update.error.code === "version_conflict") && !Object.keys(fieldErrors).length && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {update.error.message}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={!dirty || !!conflict || !!titleError || !!dueError} loading={update.isPending && !conflict}>
          Save changes
        </Button>
      </div>
    </form>
  );
}
