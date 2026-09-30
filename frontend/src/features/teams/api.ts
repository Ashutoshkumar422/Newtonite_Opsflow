import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { Member, Team, UserBrief } from "../../types/api";

export function useTeams() {
  return useQuery({ queryKey: ["teams"], queryFn: () => api<Team[]>("/teams"), staleTime: 60_000 });
}

export function useTeam(id: number) {
  return useQuery({ queryKey: ["teams", id], queryFn: () => api<Team>(`/teams/${id}`) });
}

export function useMembers(teamId: number | undefined) {
  return useQuery({
    queryKey: ["teams", teamId, "members"],
    queryFn: () => api<Member[]>(`/teams/${teamId}/members`),
    enabled: teamId !== undefined,
    staleTime: 60_000,
  });
}

export function useUserSearch(q: string) {
  return useQuery({
    queryKey: ["users", q],
    queryFn: () => api<UserBrief[]>("/users", { query: { q, limit: 10 } }),
    enabled: q.trim().length >= 2,
  });
}

export function useUpsertMember(teamId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { userId: number; role: "manager" | "member" }) =>
      api<Member>(`/teams/${teamId}/members/${vars.userId}`, { method: "PUT", body: { role: vars.role } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["teams"] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useRemoveMember(teamId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: number) => api<void>(`/teams/${teamId}/members/${userId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["teams"] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
