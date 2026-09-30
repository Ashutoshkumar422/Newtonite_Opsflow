import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey, type Query } from "../../lib/api";
import type { Activity, Comment, Page, Priority, Status, WorkItem, WorkItemSummary } from "../../types/api";

export const keys = {
  all: ["work-items"] as const,
  list: (q: Query) => ["work-items", "list", q] as const,
  detail: (id: number) => ["work-items", "detail", id] as const,
  activity: (id: number) => ["work-items", "activity", id] as const,
  comments: (id: number) => ["work-items", "comments", id] as const,
};

export function useWorkItems(query: Query, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: keys.list(query),
    queryFn: ({ signal }) => api<Page<WorkItemSummary>>("/work-items", { query, signal }),
    placeholderData: keepPreviousData,
    enabled: opts.enabled ?? true,
  });
}

export function useWorkItem(id: number) {
  return useQuery({
    queryKey: keys.detail(id),
    queryFn: ({ signal }) => api<WorkItem>(`/work-items/${id}`, { signal }),
    // Poll so a viewer notices changes made by others (stale-view mitigation).
    refetchInterval: 15_000,
  });
}

export function useActivity(id: number) {
  return useQuery({
    queryKey: keys.activity(id),
    queryFn: ({ signal }) => api<Page<Activity>>(`/work-items/${id}/activity`, { query: { limit: 100 }, signal }),
  });
}

export function useComments(id: number) {
  return useQuery({
    queryKey: keys.comments(id),
    queryFn: ({ signal }) => api<Page<Comment>>(`/work-items/${id}/comments`, { query: { limit: 200 }, signal }),
  });
}

/**
 * The server response is the source of truth: after any mutation the item cache is replaced with
 * what the server returned (never with what we *hoped* would happen), and dependent views refresh.
 */
function useItemMutation<TVars>(id: number, run: (vars: TVars) => Promise<WorkItem>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: (item) => {
      qc.setQueryData(keys.detail(id), item);
      qc.invalidateQueries({ queryKey: keys.activity(id) });
      qc.invalidateQueries({ queryKey: ["work-items", "list"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: () => {
      // Whatever happened, re-sync with the server's view of the item.
      qc.invalidateQueries({ queryKey: keys.detail(id) });
    },
  });
}

export interface ItemPatch {
  title?: string;
  description?: string;
  priority?: Priority;
  due_date?: string | null;
}

export function useUpdateItem(id: number) {
  return useItemMutation(id, (vars: { version: number; patch: ItemPatch }) =>
    api<WorkItem>(`/work-items/${id}`, { method: "PATCH", body: { version: vars.version, ...vars.patch } }),
  );
}

export function useClaim(id: number) {
  return useItemMutation(id, () =>
    api<WorkItem>(`/work-items/${id}/claim`, { method: "POST", idempotencyKey: newIdempotencyKey() }),
  );
}

export function useAssign(id: number) {
  return useItemMutation(id, (vars: { version: number; assignee_id: number | null }) =>
    api<WorkItem>(`/work-items/${id}/assign`, { method: "POST", body: vars, idempotencyKey: newIdempotencyKey() }),
  );
}

export function useTransition(id: number) {
  return useItemMutation(id, (vars: { version: number; to_status: Status; reason?: string }) =>
    api<WorkItem>(`/work-items/${id}/transitions`, {
      method: "POST",
      body: vars,
      idempotencyKey: newIdempotencyKey(),
    }),
  );
}

export function useAddComment(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { body: string; key: string }) =>
      api<Comment>(`/work-items/${id}/comments`, { method: "POST", body: { body: vars.body }, idempotencyKey: vars.key }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.comments(id) });
      qc.invalidateQueries({ queryKey: keys.activity(id) });
    },
  });
}

export function useEditComment(itemId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { commentId: number; body: string }) =>
      api<Comment>(`/comments/${vars.commentId}`, { method: "PATCH", body: { body: vars.body } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.comments(itemId) }),
  });
}
