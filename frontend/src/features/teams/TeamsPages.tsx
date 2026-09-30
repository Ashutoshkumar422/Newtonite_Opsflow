import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../../components/layout/AppShell";
import { Avatar, Button, Card, CardHeader, EmptyState, ErrorState, Field, Input, Loading, Modal, Select, Textarea, useToast } from "../../components/ui";
import { api, ApiError, errorMessage } from "../../lib/api";
import type { Member, Team } from "../../types/api";
import { useCurrentUser } from "../auth/auth";
import { useWorkItems } from "../work-items/api";
import { WorkItemTable } from "../work-items/WorkItemTable";
import { useMembers, useRemoveMember, useTeam, useTeams, useUpsertMember, useUserSearch } from "./api";

export function TeamsPage() {
  const { data, isLoading, error, refetch } = useTeams();
  const me = useCurrentUser();
  return (
    <>
      <PageHeader
        title="Teams"
        subtitle={me.is_admin ? "All teams (administrator view)" : "Teams you belong to"}
        actions={me.is_admin && <CreateTeamButton />}
      />
      {isLoading ? (
        <Loading />
      ) : error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : !data?.length ? (
        <Card>
          <EmptyState title="You're not a member of any team" body="Ask a team manager or an administrator to add you." />
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.map((t) => (
            <Link key={t.id} to={`/teams/${t.id}`} className="rounded-lg border border-ink-200 bg-white p-4 hover:border-ink-400">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{t.name}</span>
                <span className="font-mono text-xs text-ink-400">{t.key}</span>
              </div>
              <p className="mt-1 line-clamp-2 min-h-10 text-sm text-ink-500">{t.description || "—"}</p>
              <div className="mt-3 flex gap-4 text-xs text-ink-600">
                <span>{t.member_count} members</span>
                <span>{t.active_item_count} active items</span>
                {t.my_role && <span className="ml-auto rounded bg-ink-100 px-1.5 capitalize">{t.my_role}</span>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

export function TeamDetailPage() {
  const id = Number(useParams().id);
  const team = useTeam(id);
  const members = useMembers(id);
  const items = useWorkItems({ team_id: id, status: ["open", "in_progress", "blocked", "resolved"], sort: "-priority", limit: 10 });
  const me = useCurrentUser();
  const toast = useToast();
  const upsert = useUpsertMember(id);
  const remove = useRemoveMember(id);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);

  if (team.isLoading) return <Loading />;
  if (team.error) return <ErrorState error={team.error} onRetry={() => team.refetch()} />;
  const t = team.data!;

  return (
    <>
      <PageHeader
        title={
          <>
            {t.name} <span className="ml-1 font-mono text-sm font-normal text-ink-400">{t.key}</span>
          </>
        }
        subtitle={t.description}
        actions={
          <Link to={`/work-items/new?team_id=${t.id}`}>
            <Button variant="primary">New item for {t.key}</Button>
          </Link>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader
            title="Active work"
            subtitle="Highest priority first"
            action={
              <Link to={`/work-items?team_id=${t.id}`} className="text-xs text-accent-700 hover:underline">
                View all
              </Link>
            }
          />
          {items.isLoading ? <Loading /> : items.data?.items.length ? <WorkItemTable items={items.data.items} compact /> : <EmptyState title="No active work" />}
        </Card>

        <Card>
          <CardHeader
            title={`Members (${members.data?.length ?? "…"})`}
            action={
              t.can_manage && (
                <Button size="sm" onClick={() => setAdding(true)}>
                  Add member
                </Button>
              )
            }
          />
          {members.isLoading ? (
            <Loading />
          ) : (
            <ul className="divide-y divide-ink-100">
              {members.data?.map((m) => (
                <li key={m.user.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Avatar name={m.user.full_name} size="md" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {m.user.full_name} {m.user.id === me.id && <span className="text-ink-400">(you)</span>}
                    </div>
                    <div className="truncate text-xs text-ink-500">{m.user.email}</div>
                  </div>
                  {t.can_manage ? (
                    <>
                      <Select
                        aria-label={`Role for ${m.user.full_name}`}
                        className="h-8 w-32 px-2 text-xs"
                        value={m.role}
                        onChange={(e) =>
                          upsert.mutate(
                            { userId: m.user.id, role: e.target.value as Member["role"] },
                            { onSuccess: () => toast("success", "Role updated"), onError: (err) => toast("error", errorMessage(err)) },
                          )
                        }
                      >
                        <option value="manager">Manager</option>
                        <option value="member">Member</option>
                      </Select>
                      <Button size="sm" variant="ghost" aria-label={`Remove ${m.user.full_name}`} onClick={() => setRemoving(m)}>
                        ✕
                      </Button>
                    </>
                  ) : (
                    <span className="rounded bg-ink-100 px-1.5 text-xs capitalize text-ink-600">{m.role}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <AddMemberModal
        open={adding}
        onClose={() => setAdding(false)}
        existing={new Set(members.data?.map((m) => m.user.id))}
        onAdd={(userId, role) =>
          upsert.mutate(
            { userId, role },
            {
              onSuccess: () => {
                toast("success", "Member added");
                setAdding(false);
              },
              onError: (err) => toast("error", errorMessage(err)),
            },
          )
        }
        pending={upsert.isPending}
      />

      <Modal open={!!removing} title="Remove member?" onClose={() => setRemoving(null)}>
        <p className="text-sm text-ink-600">
          {removing?.user.full_name} will lose access to {t.name}'s work items. Members who still own active work must have it reassigned first.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoving(null)}>
            Keep
          </Button>
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() =>
              removing &&
              remove.mutate(removing.user.id, {
                onSuccess: () => {
                  toast("success", "Member removed");
                  setRemoving(null);
                },
                onError: (err) => {
                  toast("error", errorMessage(err));
                  setRemoving(null);
                },
              })
            }
          >
            Remove
          </Button>
        </div>
      </Modal>
    </>
  );
}

function AddMemberModal({
  open,
  onClose,
  onAdd,
  existing,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onAdd: (userId: number, role: "manager" | "member") => void;
  existing: Set<number>;
  pending: boolean;
}) {
  const [q, setQ] = useState("");
  const [role, setRole] = useState<"manager" | "member">("member");
  const users = useUserSearch(q);
  return (
    <Modal open={open} title="Add a team member" onClose={onClose}>
      <div className="flex gap-2">
        <Input aria-label="Search people" placeholder="Search by name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="Role" className="w-32" value={role} onChange={(e) => setRole(e.target.value as "manager" | "member")}>
          <option value="member">Member</option>
          <option value="manager">Manager</option>
        </Select>
      </div>
      <ul className="mt-3 max-h-64 divide-y divide-ink-100 overflow-y-auto">
        {q.trim().length < 2 && <li className="py-3 text-sm text-ink-500">Type at least two characters.</li>}
        {users.data?.map((u) => (
          <li key={u.id} className="flex items-center gap-2 py-2">
            <Avatar name={u.full_name} />
            <div className="min-w-0 flex-1 text-sm">
              <div className="truncate">{u.full_name}</div>
              <div className="truncate text-xs text-ink-500">{u.email}</div>
            </div>
            {existing.has(u.id) ? (
              <span className="text-xs text-ink-400">Already a member</span>
            ) : (
              <Button size="sm" loading={pending} onClick={() => onAdd(u.id, role)}>
                Add
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Modal>
  );
}

function CreateTeamButton() {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: () => api<Team>("/teams", { method: "POST", body: { key, name: name.trim(), description } }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ["teams"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      toast("success", `Team ${t.name} created. Add its first manager.`);
      setOpen(false);
      setKey("");
      setName("");
      setDescription("");
      navigate(`/teams/${t.id}`);
    },
  });
  const keyValid = /^[A-Z][A-Z0-9]{1,9}$/.test(key);
  const fe = create.error instanceof ApiError ? create.error.fieldErrors() : {};
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        New team
      </Button>
      <Modal open={open} title="Create a team" onClose={() => setOpen(false)}>
        <form
          className="space-y-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (keyValid && name.trim()) create.mutate();
          }}
        >
          <Field label="Name" htmlFor="team-name" error={fe.name}>
            <Input id="team-name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="e.g. Risk Operations" />
          </Field>
          <Field
            label="Key"
            htmlFor="team-key"
            error={key && !keyValid ? "2–10 characters: capital letters and digits, starting with a letter" : fe.key}
            hint="Prefix for item keys, e.g. RISK → RISK-12. Cannot be changed later."
          >
            <Input
              id="team-key"
              value={key}
              maxLength={10}
              invalid={!!key && !keyValid}
              onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
              placeholder="RISK"
            />
          </Field>
          <Field label="Description (optional)" htmlFor="team-desc">
            <Textarea id="team-desc" rows={3} value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          {create.error && !Object.keys(fe).length && (
            <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {errorMessage(create.error)}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={create.isPending} disabled={!keyValid || !name.trim()}>
              Create team
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
