import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/AppShell";
import { Button, Card, Field, Input, Select, Textarea, useToast } from "../../components/ui";
import { api, ApiError, errorMessage, newIdempotencyKey } from "../../lib/api";
import { PRIORITY_LABEL, todayIso } from "../../lib/format";
import { PRIORITIES, type Priority, type WorkItem } from "../../types/api";
import { useCurrentUser } from "../auth/auth";
import { useTeams } from "../teams/api";

export function NewWorkItemPage() {
  const me = useCurrentUser();
  const teams = useTeams();
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const writable = (teams.data ?? []).filter((t) => t.my_role !== null || me.is_admin);

  const [teamId, setTeamId] = useState(params.get("team_id") ?? "");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<Priority>("medium");
  const [dueDate, setDueDate] = useState("");
  const [assignToMe, setAssignToMe] = useState(false);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  // One key per form: a double-click or an automatic retry after a timeout cannot create two items.
  const [idempotencyKey] = useState(newIdempotencyKey);

  const create = useMutation({
    mutationFn: () =>
      api<WorkItem>("/work-items", {
        method: "POST",
        idempotencyKey,
        body: {
          team_id: Number(teamId),
          title: title.trim(),
          description,
          priority,
          due_date: dueDate || null,
          assignee_id: assignToMe ? me.id : null,
        },
      }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["work-items"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast("success", `Created ${item.key}`);
      navigate(`/work-items/${item.id}`, { replace: true });
    },
  });

  const serverErrors = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  const errors = { ...serverErrors, ...clientErrors };

  function submit(e: FormEvent) {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!teamId) errs.team_id = "Choose a team";
    if (!title.trim()) errs.title = "A short title is required";
    if (title.length > 200) errs.title = "Keep the title under 200 characters";
    if (dueDate && dueDate < todayIso()) errs.due_date = "Due date cannot be in the past";
    setClientErrors(errs);
    if (Object.keys(errs).length === 0) create.mutate();
  }

  return (
    <>
      <PageHeader title="New work item" subtitle="Describe what needs investigation, action or approval." />
      <Card className="max-w-2xl p-5">
        <form onSubmit={submit} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Team" htmlFor="team" error={errors.team_id}>
              <Select id="team" value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-invalid={!!errors.team_id}>
                <option value="">Select a team…</option>
                {writable.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.key})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Priority" htmlFor="priority">
              <Select id="priority" value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {PRIORITY_LABEL[p]}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Title" htmlFor="title" error={errors.title}>
            <Input
              id="title"
              value={title}
              maxLength={200}
              invalid={!!errors.title}
              placeholder="e.g. Refund stuck in processing for order #88213"
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <Field label="Description" htmlFor="description" hint="Why does this exist? What is known so far?" error={errors.description}>
            <Textarea id="description" rows={6} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Due date (optional)" htmlFor="due" error={errors.due_date}>
              <Input id="due" type="date" min={todayIso()} value={dueDate} invalid={!!errors.due_date} onChange={(e) => setDueDate(e.target.value)} />
            </Field>
            <label className="flex items-center gap-2 self-end pb-2 text-sm text-ink-700">
              <input type="checkbox" checked={assignToMe} onChange={(e) => setAssignToMe(e.target.checked)} className="h-4 w-4 rounded border-ink-300" />
              Assign to me
            </label>
          </div>
          {create.error && !Object.keys(serverErrors).length && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {create.error instanceof ApiError && create.error.code === "idempotency_key_reused"
                ? "This form was already submitted. Check the work item list before creating it again."
                : errorMessage(create.error)}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => navigate(-1)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={create.isPending}>
              Create work item
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
