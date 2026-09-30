import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { ToastProvider } from "./components/ui";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { LoginPage, RequireAdmin, RequireAuth } from "./features/auth/auth";
import { AccountPage } from "./features/auth/AccountPage";
import { AdminPage } from "./features/admin/AdminPage";
import { TeamDetailPage, TeamsPage } from "./features/teams/TeamsPages";
import { NewWorkItemPage } from "./features/work-items/NewWorkItemPage";
import { WorkItemDetailPage } from "./features/work-items/WorkItemDetailPage";
import { WorkItemsPage } from "./features/work-items/WorkItemsPage";
import { ApiError } from "./lib/api";
import "./index.css";

function onAuthError(error: unknown) {
  // Session expired mid-use: drop cached data and send the user to login.
  if (error instanceof ApiError && error.status === 401 && window.location.pathname !== "/login") {
    queryClient.setQueryData(["me"], null);
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onAuthError }),
  mutationCache: new MutationCache({ onError: onAuthError }),
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Retry transient failures, but never client errors (4xx) — those won't fix themselves.
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
    mutations: { retry: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <AppShell />
                </RequireAuth>
              }
            >
              <Route index element={<DashboardPage />} />
              <Route path="work-items" element={<WorkItemsPage />} />
              <Route path="work-items/new" element={<NewWorkItemPage />} />
              <Route path="work-items/:id" element={<WorkItemDetailPage />} />
              <Route path="teams" element={<TeamsPage />} />
              <Route path="teams/:id" element={<TeamDetailPage />} />
              <Route path="account" element={<AccountPage />} />
              <Route
                path="admin"
                element={
                  <RequireAdmin>
                    <AdminPage />
                  </RequireAdmin>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
