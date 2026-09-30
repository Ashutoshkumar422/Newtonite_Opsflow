# Final engineering report — OpsFlow

*Newtonite Software Engineering Challenge — "Operations Under Pressure". Report date: 2026-09-29, updated 2026-09-30 after the identity & access iteration.*
Every result below was produced by actually running the code; nothing is projected.

## 1. Executive summary

OpsFlow is a working full-stack application for coordinating operational work across teams. Users
create work items, claim or get assigned ownership, move items through a workflow that ends in a
manager's approval, discuss them, and read an immutable history of every change. A dashboard shows
what needs attention; lists support full-text search, filters and cursor pagination that stay fast
at 50 000 items.

The engineering focus is **correctness under concurrent and repeated use**: atomic claims (exactly
one winner), optimistic versioning with a conflict-resolution UI, idempotency keys, resource-level
authorization, database-enforced workflow and audit invariants, and a transactional outbox for
notifications. A second iteration added **identity and access management**: user administration
with one-time temporary passwords and server-enforced password change, revocable server-side
sessions, PostgreSQL-backed login rate limiting, OIDC single sign-on (with a bundled development
identity provider), an append-only security audit log, a team-creation UI and security headers.

Everything is covered by 146 backend tests against real PostgreSQL (all passing), 4 frontend unit
tests, and a 32-check multi-browser end-to-end smoke test (all passing, including SSO).

**Ready to demonstrate:** everything in §18. **Not ready / not done:** Docker images were not built
in the sandbox (registry access blocked; compose validated and the container path exercised
natively), no cloud deployment, no CI, no MFA inside OpsFlow (delegated to the IdP) — see §15.

## 2. Problem formulation (short)

Replace chat/spreadsheet coordination with a system of record where each request has one owner, a
trustworthy state, an explicit workflow with approval, and an explainable history — and where
simultaneous or repeated actions cannot corrupt that record. Actors, invariants I1–I9 and critical
scenarios A–F are formalised in [`PROBLEM_FORMULATION.md`](PROBLEM_FORMULATION.md).

## 3. Requirements analysis

27 functional requirements (FR-01…FR-27): 13 Must — all implemented and verified; 11 Should — all
implemented and verified (including user administration, team-creation UI, revocable sessions,
rate limiting, SSO, forced password change and the security log); 1 Could — done (outbox
monitoring); real-time push not implemented (polling); 2 Out of scope. NFRs (correctness, reliability, security, maintainability,
performance, scalability, usability, testability, observability) and how each is addressed are in
PROBLEM_FORMULATION §2.5.

## 4. Architecture

Modular monolith (FastAPI) with layers api → services → domain/policy → models, plus a separate
outbox worker process; React SPA served same-origin behind nginx. One transaction per request.
Details: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 5. Technology stack and justification

Python 3.11 · FastAPI · Pydantic 2 · SQLAlchemy 2 · Alembic · PostgreSQL 16 (pg_trgm, FTS) ·
React 19 · TypeScript 6 (strict) · Vite 8 · Tailwind 4 · TanStack Query 5 · React Router 7 ·
pytest · Vitest · Playwright · Docker Compose · nginx. Each choice is compared with alternatives in
[`TECH_STACK_COMPARISON.md`](TECH_STACK_COMPARISON.md); the deciding factor throughout was
PostgreSQL's locking, constraint and search features, which make the critical behaviours simple
and verifiable.

## 6. Database design

Twelve tables (nine for work management, three for identity: `sessions`, `auth_throttle`,
`security_events`); invariants as constraints (owned-state-requires-assignee CHECK, unique per-team item
numbers, domain CHECKs), an append-only trigger on `activities`, unique keys that make duplicates
impossible (idempotency, notifications), 19 secondary indexes aligned with the actual queries (keyset sort
orders, GIN for FTS/trigram, partial indexes for overdue and pending outbox). No hard deletes.
Details and the lock-by-lock correctness table: [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md).

## 7. API design

41 REST operations under `/api/v1` with OpenAPI docs at `/api/docs`, one error envelope with
stable codes and request IDs, cursor pagination, `version` for optimistic concurrency,
`Idempotency-Key` on the five side-effecting POSTs, and an advisory `permissions` object on items.
Details: [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md).

## 8. Authentication and authorization

bcrypt password hashes with a length/personal-information policy. Signed tokens name a
**server-side session** row and are valid only while it is active (immediate logout/revocation on
every replica); carried in an HttpOnly SameSite=Lax cookie with a required custom header for unsafe
methods (CSRF), or as a bearer token for API clients. Sign-in is throttled per account and per
client address in PostgreSQL (429 + Retry-After, no account enumeration). **OIDC SSO** uses the
authorization-code flow with PKCE, state and nonce and fully validates the ID token; identities map
to accounts by (issuer, subject) or verified email. Admin-issued passwords must be changed before
any other endpoint answers. Identity events go to an append-only security log. Authorization = global
admin + per-team manager/member + per-item reporter/assignee, implemented once in
`app/auth/policy.py` and re-checked by every service; cross-team resources return 404; list,
search and dashboard queries are scoped in SQL.

## 9. Concurrency and consistency mechanisms

| Mechanism | Implementation | Guarantees |
|---|---|---|
| Atomic claim | conditional `UPDATE … WHERE assignee_id IS NULL RETURNING` | one winner; losers 409; one audit row |
| Optimistic concurrency | `SELECT … FOR UPDATE` + client `version` | stale writes rejected with current state; version +1 per change |
| Idempotency | key row inserted in the same transaction; unique `(user, key)` | at-most-once effects for retries, including concurrent duplicates |
| Workflow | pure transition table + DB CHECK | invalid states unreachable even via direct SQL (for the assignee rule) |
| Audit | activity insert in the same transaction; append-only trigger | no change without its record; records immutable |
| Membership | `FOR SHARE` on assignee membership; team row lock for membership changes | no assignee outside the team; ≥1 manager |
| Async | transactional outbox, `SKIP LOCKED`, retries + dead letter, unique consumer key | events iff commit; each processed once at a time; no duplicate notifications |
| Login throttling | atomic `INSERT … ON CONFLICT DO UPDATE … RETURNING` counters, committed separately | every failure counted across replicas and concurrent requests |
| Sessions | session row checked on every request | revocation effective on the next request everywhere |
| Admin invariants | admin rows locked `FOR UPDATE`; self-demotion refused | never zero active administrators |

## 10. Critical engineering scenarios

All five prioritised scenarios (A claim race, B stale edit, C retry after timeout, D unauthorized
resource access, E workflow violation) plus F (async failure/duplication) are implemented in the
backend and covered by automated tests — see PROBLEM_FORMULATION §2.8 for initial state,
operations, expected results, protections and test names.

## 11. Testing approach and actual results

Risk-based: tests target the invariants whose violation would corrupt data, leak data or mislead
decisions. Integration tests use real PostgreSQL via the real migration; concurrency is tested
both with barrier-released thread races and with deterministic two-session interleavings.

| Suite | Executed | Passed | Failed | Skipped |
|---|---|---|---|---|
| Backend pytest (24 unit + 74 integration + 48 identity/SSO) | 146 | 146 | 0 | 0 |
| Frontend Vitest | 4 | 4 | 0 | 0 |
| E2E smoke (dev server and production nginx build, SSO included) | 32 checks ×2 runs | 32 | 0 | 0 |
| ruff · mypy · tsc · ESLint | — | clean | — | — |

Backend suite run repeatedly without flakes (≈42 s; bcrypt at cost 12 makes login tests slow on
purpose). Full breakdown, test-to-risk
mapping and gaps: [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md).

## 12. Performance

Measured with `backend/scripts/benchmark.py` after `python -m app.seed --bulk 50000` (50 025
items). Single client, sequential requests, 20 runs each, API with 2 Uvicorn workers (started via
`docker-entrypoint.sh`) and PostgreSQL 16 on the same Linux VM. Wall-clock from the client,
milliseconds — **these numbers include the per-request session lookup** added in the identity
iteration:

| Request | Priya (member of 2 teams, 25 014 visible) median / p95 | Admin (all 50 025) median / p95 |
|---|---|---|
| List, first page (25) | 11.7 / 13.5 | 12.0 / 16.2 |
| List, page 41 via keyset cursor | 13.5 / 17.5 | 11.7 / 14.0 |
| Filter status × priority | 18.5 / 25.1 | 20.7 / 24.5 |
| Full-text search "refund" (4 169 / 8 335 hits) | 15.7 / 20.5 | 14.2 / 21.1 |
| Overdue, sorted by due date | 12.9 / 15.9 | 10.6 / 15.3 |
| My work, sorted by priority | 14.5 / 18.6 | 9.0 / 10.9 |
| Dashboard summary (single aggregate) | 26.6 / 34.4 | 35.5 / 42.2 |

For reference: `GET /api/health` (no auth) 2.8 ms, `GET /auth/me` (session lookup + memberships)
7.8 ms, `GET /teams` 11.1 ms.

**Finding fixed during this measurement:** PostgreSQL's JIT compiler was triggering on short
queries whose cost estimate crossed `jit_above_cost` — `GET /teams` took 126 ms of which 87 ms was
JIT compilation, and full-text search ~45 ms. JIT is now disabled for application connections
(`OPSFLOW_DB_DISABLE_JIT`, default on), bringing those to 11 ms and ~15 ms.

Page 41 costs the same as page 1 (keyset); the plan for the page query is an index scan on
`ix_work_items_updated` with a row-comparison `Index Cond` (0.18 ms execution). Every response
includes an exact `total`, which is the dominant cost for large filtered sets. Not measured:
concurrent load / throughput.

