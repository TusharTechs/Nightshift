# Sprint 1 Conflict Log

Per the NightShift AI engineering instructions, implementation stopped before
any code was written so these cross-document conflicts could be surfaced and
approved. All nine items below were approved as recommended on 2026-07-31.
This file is the permanent record of what was approved and why, so the
resolutions are auditable independent of chat history.

## 1. Missing tables (CRITICAL)

Sprint 1's own migration draft created only `organizations`, `stores`,
`store_tokens`, `shifts` — omitting `metrics_hourly` and `store_memories`,
which Sprint 1's own Story 2, Feature 3, and AI Specification sections
require writes to. **Resolution:** both tables pulled forward from the
Database Architecture document into `alembic/versions/0001_sprint1_baseline_schema.py`.
`issues` and `audit_logs` are intentionally deferred — no Sprint 1 acceptance
criterion exercises either.

## 2. ORM disagreement — Prisma vs. SQLAlchemy (CRITICAL)

SATDD illustrates the data layer with Prisma/camelCase; Sprint 1's own
directory layout and Acceptance Checklist specify SQLAlchemy 2.0 Async +
Alembic. **Resolution:** SQLAlchemy + Alembic, snake_case, throughout. SATDD's
Prisma blocks are treated as an illustrative artifact, not a contract.

## 3. `shift_number` SERIAL vs. per-store counter

A global `SERIAL` contradicts the per-store `UNIQUE(store_id, shift_number)`
intent. **Resolution:** plain `INT` column; `ShiftRepository.next_shift_number`
computes the next number per store in application logic.

## 4. `estimated_revenue_protected` vs. `estimated_revenue_saved`

**Resolution:** `estimated_revenue_protected` — matches Sprint 1's own
migration, the Database Architecture doc, and the product docs' own
"revenue protected" phrasing.

## 5. OAuth callback failure status code: 502 vs. 500

**Resolution:** 502, per Sprint 1's explicit Story 1 failure-case narrative
("Shopify API timeout during token exchange → HTTP 502").

## 6. Row-Level Security

Blueprint and Database Architecture doc mandate RLS on every table holding
merchant data; Sprint 1's own migration omitted it entirely. **Resolution:**
RLS enabled on `stores`, `store_tokens`, `shifts`, `metrics_hourly`, and
`store_memories` in the Sprint 1 migration (the last two were extended to
match the same axiom, since they are new tables added under item #1).

## 7. `GET /api/v1/stores/me` missing from the API Contract Specification

**Resolution:** built exactly as Sprint 1 specifies. Flagged as a documentation
gap for the API architecture owner — not a blocker.

## 8. Webhook listener setup

SATDD's day-by-day plan places webhook setup in Sprint 1; the dedicated
Sprint 1 spec's deliverables never mention it. **Resolution:** deferred.
Sprint 1's own Definition of Done and Acceptance Checklist do not require a
webhook endpoint; baseline sync is triggered by the post-OAuth Celery task
only.

## 9. Vector storage: pgvector-in-Postgres vs. Qdrant

Feature 1 says to stand up Qdrant in Docker Compose; the Database
Architecture doc defines `store_memories` as a native Postgres/pgvector
table. **Resolution:** pgvector-in-Postgres only for Sprint 1 — no Qdrant
service in `docker/docker-compose.yml`. Simpler, and nothing in Sprint 1's
acceptance criteria requires a second database.

---

Secondary items noted during review but not requiring a decision for Sprint 1
(carried forward for future sprints): `cognitive_tasks` vs. `tasks` naming
inconsistency across documents (Section 7.10 of the engineering brief); the
PRD's Milestone 1 exit criterion "audit logging operational" has no
corresponding `audit_logs` table in any Sprint 1 document — same
missing-table pattern as item #1, but explicitly out of Sprint 1's own scope
list, so left for the Tech Lead to schedule.

---

# Sprint 2 Conflict Log

Per the same engineering instructions, implementation stopped again before
any Sprint 2 code was written so these conflicts could be surfaced and
approved. Items 10 and 12 were put to the user directly (they change
behavior/cost, not just documentation); items 11 and 13-18 were resolved the
same way Sprint 1's items were — following the clearest, most conservative
reading of the higher-priority documents — and are recorded here for the
same auditability reason. All items below were approved on 2026-07-31.

## 10. Two incompatible Store Health Score algorithms (CRITICAL)

`domain/health.py` (Sprint 1, tested by `test_health_score.py`) implemented
a weighted-average-of-category-subscores model (lowercase keys: `checkout`,
`tracking`, `seo`, `products`, `discounts`, `performance`; each subscore
0-100, missing categories default to 100). Sprint 2's own Feature 3 defines
a completely different deduction-from-100 model for the same computation
(`HealthScore = 100 - Σ(Deductions_Category)`, uppercase `issue_category`
enum keys, different weight allocation), without acknowledging that
`domain/health.py` already existed with a conflicting implementation.
**Resolution (user-approved):** replace Sprint 1's algorithm with Sprint 2's
deduction model. Sprint 2's own Feature 3 is titled the "Composite Store
Health **Engine**" and its DoD requires the score to "recalculate
deterministically post-inspection" — read as the intended successor, not a
coexisting variant. Sprint 1's `compute_health_score` /
`baseline_category_scores_from_snapshot` API and its dedicated unit tests
are retired; the one Sprint 1 caller (`tasks.store_discovery`) is updated to
call `calculate_store_health([])` (no issues exist yet at baseline scan
time, so the score is the neutral 100 ceiling, same behavior as before).

## 11. `issue_category` has 8 values; the health-deduction model only covers 6

Sprint 2's own migration DDL creates all 8 `issue_category` values (matching
the Database Architecture doc), but its Feature 3 code sample only assigns a
deduction cap to 6 of them, silently dropping any `INVENTORY` or
`TRUST_INDICATOR` issue from the score with no warning. **Resolution:** keep
the 6-category cap as specified (those two categories aren't monitored by
any Sprint 2 agent), but surface uncapped categories via a
`HealthScoreResult.unscored_categories` field and a structured warning log,
so they're visible rather than silently discarded.

## 12. Gemini model: `gemini-1.5-pro` is retired (user-approved)

Every source document hardcodes `gemini-1.5-pro`. Checked against Google's
current model lifecycle documentation: both dated 1.5 Pro API versions
passed their retirement dates in 2025, so a literal implementation would
fail at request time. **Resolution:** default to `gemini-2.5-pro` (closest
"Pro"-tier reasoning replacement, supports structured `response_schema`
output), exposed as a configurable `GEMINI_MODEL_NAME` setting rather than
hardcoded, so a future model change never requires a code edit.

## 13. `GEMINI_API_KEY` vs. existing `GOOGLE_AI_API_KEY`

Sprint 2's Dependencies section names a distinct `GEMINI_API_KEY` variable;
the repo's `config.py` already defines `google_ai_api_key`, documented since
Sprint 1 as "AI embeddings ... not invoked until Sprint 2." **Resolution:**
reuse `google_ai_api_key` for the Gemini SDK call rather than add a second,
redundant credential — it already exists for exactly this purpose.

## 14. `AgentIssueSchema` vs. `DetectedIssueSchema` naming mismatch

Sprint 2 Story 2's acceptance criteria says Gemini output must conform "to
`AgentIssueSchema`," but the only Pydantic issue schema ever defined
anywhere in the document is `DetectedIssueSchema` — a naming slip in the
source doc. **Resolution:** `DetectedIssueSchema` is used consistently
throughout, since it's the only one with a concrete definition.

## 15. `issues` has no dedicated `model_identifier` / `prompt_version` columns

Story 2's AC requires storing "AI model identifier and prompt version in
issues metadata," but the `issues` table DDL (identical in the Sprint 2 Spec
and the Database Architecture doc) has no such columns — only
`evidence_data JSONB`. **Resolution:** both values are written into
`evidence_data` (`model_identifier`, `prompt_version` keys) at issue-creation
time, since there is no other column to hold them without a schema change
neither document calls for.

## 16. Frontend dependency gaps: `framer-motion` and tRPC

Sprint 2's UI Spec calls for `framer-motion` card-entrance animation and a
tRPC data layer ("fetched via tRPC with React Query caching"). Neither
package is a dependency of `apps/web` today — the app uses a plain `fetch()`
wrapper + React Query, and the Technical Blueprint's tRPC plan is its own
stated future direction, not yet built. **Resolution:** reproduce the same
staggered fade-in/slide-up visual with a plain CSS `@keyframes` animation
(`globals.css`) instead of adding `framer-motion`, and keep the existing
fetch + React Query pattern instead of introducing tRPC mid-sprint.

## 17. Three incompatible JSON contracts for the same endpoint

`GET /api/v1/shifts/latest` has three different shapes across source
documents: the API Contract Specification (snake_case, `metrics{...,
pending_approvals}`, separate `completed_tasks[]`/`pending_approvals[]`
arrays, no top-level `health_score`), Sprint 2's own spec (snake_case,
top-level `health_score`, narrower `metrics{}`, flat `issues[]`, no
task/approval split), and SATDD (camelCase — already non-binding per
ADR-001's Prisma precedent). Sprint 2 has no `cognitive_tasks`/`approvals`
tables, so the richer two shapes aren't buildable this sprint. **Resolution:**
implement Sprint 2's own narrower shape verbatim; the richer, task/approval-
aware contract should be revisited once that domain exists in a later
sprint.

## 18. No Row-Level Security in Sprint 2's own migration DDL

Sprint 1's migration explicitly enabled RLS on every new tenant-data table
(ADR-006), matching the Database Architecture doc's and Technical
Blueprint's unconditional RLS-on-every-merchant-data-table axiom. Sprint 2's
own `0002_sprint2_ai_employee_schema` DDL creates `agents`, `issues`, and
`shift_reports` with no RLS statements at all. **Resolution:** RLS enabled
on `issues` and `shift_reports` (both hold per-tenant merchant data),
matching ADR-006's precedent. `agents` is a shared, non-tenant registry of
AI Employee configs with no `store_id` column — RLS does not apply to it.
This compounds the still-open ADR-012 gap (the RLS tenant GUC is still never
actually set by `get_db_session`) — Sprint 2 does not introduce a
non-owner DB role, so ADR-012 remains an open gap for a future sprint, not
something this sprint's RLS additions resolve on their own.

## 19. LLM provider: Anthropic (Claude Haiku 4.5) instead of Gemini (user-directed)

Every source document specifies Google Gemini as the reasoning provider.
After Sprint 2 was implemented and working end-to-end, the user explicitly
requested switching the *active* provider to Anthropic, to use their own
metered Anthropic API credits (a $10 testing budget) instead of Gemini, and
asked for hard guarantees against a bug or retry loop exhausting that
budget in one session. This is a user-directed change, not a
documents-vs-repo conflict, but is logged here for the same auditability
reason as every other deviation from the source specs.

**Resolution:** `ProductQualityAgent` was made fully provider-agnostic (it
depends only on a `StructuredLlmClient` structural contract — `model_name` +
`generate_structured`), with a factory (`infrastructure/llm/factory.py`)
selecting the concrete client from `Settings.llm_provider`. Default is now
`ANTHROPIC` / `claude-haiku-4-5-20251001` (cheapest current Claude model).
Gemini support is untouched and fully functional — switching back is
`LLM_PROVIDER=GEMINI` in `.env`, not a code change. Anthropic's native
structured-outputs feature (`messages.parse(output_format=PydanticModel)`)
is used, the direct equivalent of Gemini's `response_schema`. See
DECISIONS.md ADR-023.

Three concrete cost-safety mechanisms were added at the same time (not
requested as separately-numbered items, but all serve the same "don't burn
the budget" ask — see DECISIONS.md ADR-024):

1. `Settings.llm_max_output_tokens` (default 4096, raised from 1024 — see
   item 22 below) — a hard `max_tokens` cap
   on every LLM call; Anthropic's own docs note truncated responses are
   still billed for every token generated, so `stop_reason == "max_tokens"`
   is treated as a hard failure (triggers the existing deterministic
   fallback) rather than accepting a possibly-invalid partial response.
2. `Settings.llm_max_findings_per_call` (default 50) — caps how many
   inspection findings are sent to the LLM per call regardless of catalog
   size, prioritized by severity before truncation, so a large or messy
   catalog can never balloon a single request's input tokens unboundedly.
3. `Settings.llm_max_calls_per_day` (default 30), enforced by a new
   Redis-backed `LlmCallBudgetGuard` — a hard daily ceiling on LLM calls
   that holds across Celery worker processes/restarts (not just one Python
   process), so a bug, a Celery retry storm (bounded at 6 calls/shift
   already — 1 initial + 5 retries), or repeated manual test triggers can
   never exhaust the budget in one runaway session. Once tripped, the agent
   falls back to its existing deterministic rule-based path for the rest of
   the day rather than failing the shift outright.

## 20. Local dev `.env` hostname mismatch: Docker service names vs `localhost`

Found during live E2E testing of the Sprint 2 pipeline (2026-07-31), after
the user hit a `socket.gaierror` connecting to Postgres and then a
connection failure to Redis from inside the `core_api`/`worker` containers.
`DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`
were all set to `localhost` in `.env`, which only resolves correctly when
`core_api`/`worker` run directly on the host machine. Since
`docker/docker-compose.yml` runs them inside the compose network, they must
address Postgres/Redis by the compose *service names* (`postgres`, `redis`),
not `localhost`. Not a code bug — a local environment config gotcha — but
worth recording since it silently breaks every DB/Celery call and the fix is
non-obvious from the error message alone (`asyncpg`'s DNS failure gives no
hint that the issue is Docker networking).

**Resolution (corrected 2026-07-31, same day):** an earlier version of this
fix hardcoded `postgres`/`redis` directly into `.env`/`.env.example` — this
was wrong, because `.env` is shared by two run modes: native `shopify app
dev` (uvicorn on the host, reaching Postgres/Redis via their published
ports, needs `localhost`) and Docker Compose (`core_api`/`worker`
containers, need the service hostnames). Hardcoding the Docker hostnames
into `.env` fixed Compose but silently reintroduced the exact
`socket.gaierror` failure in the native `shopify app dev` path. The correct,
final fix: `.env`/`.env.example` stay on `localhost` for both `DATABASE_URL`
and `REDIS_URL`/`CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, and
`docker/docker-compose.yml`'s `environment:` block on the `core_api`/
`worker` services overrides these four vars with the Docker-internal
hostnames — Compose's `environment:` takes precedence over `env_file`, so
one shared `.env` now correctly serves both run modes without either
breaking the other.

## 21. `ShiftModel.status` mapped as `String(30)` instead of the native `shift_status` Postgres enum

Real bug, found during live E2E testing when `tasks.inspect_catalog`'s very
first DB insert (`shift_repo.create_shift`) failed on every attempt with an
`asyncpg` enum-cast error. `0001_sprint1_baseline_schema.py` created
`shifts.status` as a native Postgres enum type (`shift_status`), but
`app/infrastructure/database/models.py`'s `ShiftModel.status` mapped it as a
plain `String(30)` column — unlike `IssueModel.status`, which correctly used
a `SqlEnum(..., create_type=False)` mirroring `issue_status`. `asyncpg`
refuses to implicitly cast a varchar bind parameter into a native enum
column (unlike `psycopg2`, which is more permissive), so every insert of a
new `shifts` row failed, and Celery's retry/backoff masked this as if it
were a transient failure rather than a deterministic one — it would have
retried 5 times and then failed the shift permanently, every single run.

**Resolution:** added the missing `ShiftStatus` domain enum
(`app/domain/enums.py`, mirroring `IssueStatus`'s existing pattern exactly)
and wired `ShiftModel.status` to a `SqlEnum(*[s.value for s in ShiftStatus],
name="shift_status", create_type=False)`, matching `IssueModel.status`'s
correct implementation. This is a pure bug fix — no schema/migration change
was needed, since the Postgres column was already correct; only the SQLAlchemy
mapping was wrong.

## 22. `LLM_MAX_OUTPUT_TOKENS` default (1024) too low for a real catalog scan

Found during live E2E testing: a real Anthropic call against a 41-finding
catalog scan produced a response that hit `stop_reason == "max_tokens"` at
1024 tokens, got cut off mid-JSON-string, and correctly tripped
`AnthropicClient`'s existing hard-failure path (per ADR-024, truncated
output is a failure, not a partial success) — so the shift fell back to the
deterministic path rather than using real LLM output. Not a bug in the
guardrail logic (it did exactly what it was designed to do), just a default
that was sized before any real catalog had been scanned.

**Resolution:** raised `Settings.llm_max_output_tokens` default from `1024`
to `4096` in `.env`/`.env.example`. Still a hard, explicit cap — cost-per-call
ceiling scales accordingly but remains bounded and configurable, consistent
with ADR-024's intent.

---

# Sprint 3 Conflict Log

Per the same engineering instructions, all naming, schema, and design
decisions for Sprint 3 (AI Trust & Execution, Part 1) were discussed with and
approved directly by the user in this session (2026-08-01) before
implementation. Recorded here for the same auditability reason as every
prior sprint's log.

## 23. Additive schema beyond the Database Architecture doc: `rollbacks` table, `executions.retry_count`, `approvals.execution_override_params`, `audit_logs.execution_id`

The Database Architecture doc defines `cognitive_tasks`, `approvals`,
`executions`, `verifications`, and `audit_logs`, but not a `rollbacks` table
at all, and none of `executions.retry_count`, `approvals.execution_override_params`,
or `audit_logs.execution_id`. The master engineering brief's requirements —
bounded retry of a failed mutation, a merchant "Modify" action on a proposed
fix (edit-then-approve), deterministic rollback of every Level-1/Level-2
autonomous action, and correlating an audit entry to the exact execution
attempt it narrates — have no column/table to persist to without these
additions. **Resolution (user-approved, 2026-08-01):** all four added in
`0003_sprint3_trust_execution_schema.py` as additive, non-conflicting
columns/table. `rollbacks` reuses the existing `execution_status` enum type
rather than defining a redundant sixth enum type, since STARTED/COMPLETED/
FAILED apply naturally to a rollback attempt too.

## 24. `approvals.approver_user_id` has no foreign key constraint

The Database Architecture doc's `approvals` table implies `approver_user_id`
references a `users` table, but no `users` table exists anywhere in this
codebase — Sprint 1/2 never built per-user auth; `get_current_store_id`
resolves only to a store, never a user. **Resolution:** `approver_user_id`
is a bare nullable UUID column with no FK constraint, reserved for when user
accounts land. Until then, merchant-initiated actions during this lifecycle
are attributed via `audit_logs.actor_type = 'MERCHANT'` / `audit_logs.actor_id`.

## 25. Sprint 2's `DetectedIssueSchema` touch-up: `fix_check` field

Sprint 3's Plan step (`Agent.propose_action`) needs to deterministically map
a persisted `Issue` back to one of this sprint's two auto-fixable action
types, without string-matching LLM-generated title/description prose (fragile
and non-deterministic). Sprint 2's `DetectedIssueSchema` (product_quality.py)
had no field carrying the originating inspection finding's `evidence.check`
identifier through to the persisted `issues.evidence_data`. **Resolution:**
added an optional `fix_check: str | None` field to `DetectedIssueSchema`
(both the LLM prompt and the deterministic fallback path populate it verbatim
from the finding's `evidence.check`, never inventing a value), and
`services/workers/tasks/inspection.py` now writes it into `evidence_data` at
issue-creation time alongside the existing `model_identifier`/`prompt_version`
keys (same pattern as ADR-019/CONFLICTS.md item 15). A small, justified
Sprint 2 touch-up, not a refactor — additive only, no existing behavior
changed for issues without a `fix_check`.

## 26. "Modify" approval action is not a new `approval_status`/enum value

The PRD's Approval Center UI describes a "Modify" action (merchant edits a
proposed fix's parameters, then approves it), distinct from a plain Approve/
Reject/Defer. The Database Architecture doc's `approval_status` enum has no
`MODIFY` value, and adding one would fork the approval state machine for what
is really just an Approve with edited parameters. **Resolution (user-approved):**
"Modify" is modeled as `ApprovalAction.APPROVE` with
`execution_override_params` populated (see item 23) — no new enum value, no
new `approval_status`. `domain/enums.py::ApprovalAction` documents this
explicitly so a future implementer doesn't reintroduce a redundant `MODIFY`
state.

## 27. Risk-level-to-action mapping and autonomy policy scope narrowed to two action types

The SATDD's risk commentary describes a fuller taxonomy (LEVEL_1_SAFE through
LEVEL_4_CRITICAL) and richer example action catalogs than this sprint
implements. **Resolution (user-approved, deliberate scope narrowing):** only
`GENERATE_ALT_TEXT` (LEVEL_1_SAFE) and `REWRITE_PRODUCT_DESCRIPTION`
(LEVEL_2_MODERATE) are wired to concrete agent fix paths this sprint — no
discount agent, no other categories. `domain/risk.py`'s risk/approval engine
still handles LEVEL_3_HIGH/LEVEL_4_CRITICAL generically (always requires
approval) for future extensibility, but no action type maps to them yet.

---

# Sprint 3 Conflict Log — Part 2 (Execution/Approval/Verification/Rollback)

Continuing Part 1's numbering. All decisions below were made while
implementing the application/API/Celery layers on top of Part 1's domain
and infrastructure layer; none touch Part 1's files except by pure,
additive method calls, called out individually below.

## 28. Idempotency-Key: header required, but no replay-cache store exists

`POST /api/v1/approvals/{id}/action` and `POST /api/v1/tasks/{task_id}/rollback`
both require an `Idempotency-Key` header (FastAPI `Header(...)`, 422 if
absent). Full Redis-backed replay-caching — where an exact request replay
with the same key returns the identical cached response without
re-processing — is **not implemented this sprint**: no such cache/store
exists anywhere in this codebase, and building one was not part of the
approved scope for Part 2. **Resolution:** the header is required and its
presence enforced, but true request-level dedup instead relies on the
approval/task state-machine guards already in place —
`ApprovalAlreadyDecidedProblem` (409) on a second decision against an
already-decided approval, and `TaskNotRollbackableProblem` (400) against a
task whose execution isn't COMPLETED. This is a documented limitation, not
a silently skipped requirement — a genuine exact-replay (same key, request
arrives twice before the first is processed) is not guarded against a race
this sprint.

## 29. Synchronous, in-process auto-execution inside `tasks.plan_cognitive_tasks`

To avoid a race between the immutable Morning Shift Report snapshot
(`tasks.compile_shift_report`, fired immediately after planning, same as
Sprint 2) and asynchronous execution completing later, tasks that do NOT
require merchant approval (Level-1/auto-approved-Level-2) are executed
and verified **synchronously, in-process**, inside
`PlanCognitiveTasks.execute()` — by directly awaiting the injected
`ExecuteCognitiveTask`/`VerifyExecution` use case instances, never via a
Celery dispatch. Only tasks requiring approval stay `PENDING_APPROVAL`;
their execution is genuinely asynchronous, dispatched via
`tasks.execute_cognitive_task` (celery:execution) only when
`HandleApprovalAction` grants an APPROVE decision. **Resolution
(user-approved, per the master engineering brief for this session):** this
guarantees every auto-executed task has already reached a terminal
execution/verification state by the time the shift report compiles, so
`completed_tasks[]` in the persisted report is always accurate — no
"pending write" ever silently missing from an immutable report. The Celery
tasks `tasks.execute_cognitive_task`/`tasks.verify_execution` still exist as
real, independently dispatchable tasks (reserved queues from Sprint 1/2) —
they are simply not used for the synchronous path. No double-execution risk
either way, since `ExecuteCognitiveTask.execute()`'s own idempotency check
(a terminal `executions` row short-circuits a re-run) holds regardless of
which path triggered a given call.

## 30. Additive repository/Protocol methods beyond Part 1's explicit spec

Implementing the use cases surfaced several small, symmetric gaps in Part
1's repository/Protocol surface that were needed to fulfil the master
brief's own worked examples (e.g. "load the execution by execution_id",
"assemble a task's rollback for the detail view"). All were added as pure,
additive methods, each following the exact style/pattern of an existing
sibling method on the same repository — no existing method was modified:

- `SqlIssueRepository.get_by_id` (issues had no id-lookup at all; needed by
  Verify/Rollback use cases and `GET /api/v1/tasks/{task_id}`).
- `SqlExecutionRepository.get_by_id` and `SqlRollbackRepository.get_by_id`
  (both repositories had only task_id/execution_id-keyed lookups or none;
  `VerifyExecution`/`RollbackCognitiveTask` take an `execution_id` per the
  master brief's own method signatures, so a same-table `get_by_id` was
  required).
- `SqlRollbackRepository.get_by_execution_id` (mirrors
  `SqlVerificationRepository.get_by_execution_id` exactly; needed to
  assemble `TaskDetailResponse.rollback`).
- `SqlCognitiveTaskRepository.update_execution_plan` (explicitly
  anticipated by the master brief for the merchant "Modify" approval path —
  ADR-028 — but not present in Part 1's repository).
- `SqlApprovalRepository.list_for_shift` (needed by
  `workers/tasks/shift_report.py` to build `pending_approvals[]`; Part 1
  only had `list_pending_for_store`, scoped by store not shift).
- `SqlApprovalRepository.count_approvals_for_action_type` (symmetric
  companion to Part 1's own `count_rejections_for_action_type`, used to
  derive a `merchant_approval_rate` signal for `compute_confidence` rather
  than always passing `None` and losing that signal entirely).
- `SqlApprovalRepository.extend_expiry` (the DEFER approval action changes
  `expires_at` without changing `status`, so it doesn't fit `decide()`).

Every one of these has a matching Protocol method in `application/ports.py`
and a matching `InMemory*` fake implementation, so Part 3's tests can rely
on them without reaching into private repository internals.

## 31. `IssueRepository` had no Protocol in Part 1

`SqlIssueRepository` existed (Sprint 2) but `application/ports.py` never
defined a corresponding `IssueRepository` Protocol — every Sprint 2 worker
task used the concrete SQL class directly. Sprint 3's use cases
(`PlanCognitiveTasks`, `VerifyExecution`, `HandleApprovalAction`) need to
depend on the abstraction to stay framework-agnostic and unit-testable, per
this module's own stated Clean Architecture rule. **Resolution:** added
`IssueRepository` (Protocol) and `InMemoryIssueRepository` (fake) now,
covering `create`/`list_for_shift`/`list_for_store`/`update_status` (Part
1's existing methods) plus `get_by_id` (item 30). `ShiftRepository`'s
Protocol already existed correctly (with `get_by_id`/`get_latest_completed`)
and only needed the two `increment_*` methods added, matching Part 1's own
`SqlShiftRepository` additions exactly.

---

# Sprint 4 Step 1 Conflict Log (Agent Registry + Demo Incident Generator)

Per `SPRINT4_AI_WORKFORCE_VISION.md`'s user-approved "locked order of
execution," Step 1 is: the agent registry refactor and the Demo Incident
Generator wired for Scenario 1 minimum. No source document defines either
of these (they are this session's own roadmap, not the original Vision/PRD/
Hackathon MVP Spec/etc.), so there is no priority-order conflict to resolve
in the usual sense — the two items below are implementation decisions made
during Step 1 that are worth recording for the same auditability reason as
every other item in this log.

## 32. Agent registry keyed by `domain_category`, reusing `IssueCategory`'s existing values

`PlanCognitiveTasks` previously took one hardcoded `product_quality_agent:
Agent` constructor argument. Generalizing it to `agents: dict[str, Agent]`
raised the question of what the dict key should be. **Resolution:** the
same string values as the `IssueCategory` enum (`PRODUCT_QUALITY`,
`CHECKOUT`, `DISCOUNT`, etc.) — which is also, not coincidentally, the
Postgres enum type already backing the `agents.domain_category` column
since Sprint 2. This means Step 2's Checkout Specialist (or any future
specialist) needs zero new enum values or schema changes to register itself
in this dict; it only needs its own `Agent` subclass with
`domain_category = "CHECKOUT"` (or `"DISCOUNT"` — both already exist) and
its own row in the `agents` table. `agent_record_id: uuid.UUID` (singular)
became `agent_record_ids: dict[str, uuid.UUID]` for the same reason, keyed
identically, so a planned task is always attributed to the correct
specialist's registry row rather than a single hardcoded id.

## 33. Demo Incident Generator gated behind a default-`False` settings flag, not exposed unconditionally

The Demo Incident Generator's whole purpose is to let an authenticated
caller deliberately corrupt their own store's live data (e.g. create
overlapping stackable discount codes) on cue, for demo purposes. Left
unguarded, `POST /api/v1/demo/incidents/{scenario_id}` would be a real
production liability — any authenticated store could accidentally (or
maliciously, via a compromised session) trigger it. **Resolution:** added
`Settings.demo_mode_enabled` (default `False`, env var `DEMO_MODE_ENABLED`)
and gated the route on it, returning 404 (not 403) when disabled so the
endpoint's existence isn't distinguishable from a route that doesn't exist.
This was not specified by any source document (the Vision doc only
describes the capability, not its production safety posture) but follows
directly from the master brief's general "never ship a capability that
isn't safe by default" engineering judgment. The one-time local CLI script
(`scripts/trigger_demo_incident.py`, mirroring `scripts/trigger_shift.py`)
intentionally does NOT check this flag — it requires local repo/machine
access already, the same trust boundary `trigger_shift.py` assumes.

---

# Sprint 4 Step 2 Conflict Log (Checkout Specialist — Duplicate Discount Lifecycle)

## 34. `codeDiscountNodes` (the only bulk discount-listing query) is marked Deprecated in Shopify's current live schema docs

Detecting overlapping/duplicate discounts requires scanning every currently
active discount code — there is no non-deprecated bulk-listing query for
this. `codeDiscountNodeByCode` (the only non-deprecated alternative) only
supports an exact-code single-item lookup and cannot enumerate all active
discounts. **Resolution:** used `codeDiscountNodes(first, query:
"status:active")` deliberately, flagged in `shopify_client.py`'s own module
comment as a known risk to revalidate via live schema introspection before
any production use beyond this hackathon MVP — same category of risk as
Sprint 3's discovery that `productImageUpdate` had been fully removed
(caught by the same fetch-and-read method), except here the field is only
deprecated, not removed, so it is expected to keep working for now.

## 35. Revenue impact for discount findings uses Shopify's own reported `totalSales`, not a fabricated estimate

Unlike Product Quality's LLM-estimated `revenue_impact_estimate` (a
forward-looking, somewhat subjective number), a duplicate/overlapping
discount's revenue impact is reported as the sum of the flagged discounts'
own `totalSales` field — real, already-realized sales Shopify itself
attributes to those codes, not a projection. This is more defensible than
a fabricated bleed-rate, consistent with the "never invent data not present
in context" rule this codebase has held since Sprint 2, and matches the
grounding correction already applied to the Counterfactual ROI Widget
(Sprint 4 Step 1, CONFLICTS.md item 33's sibling entry in
`SPRINT4_AI_WORKFORCE_VISION.md`'s "Three locked additions" section). For a
freshly-created demo discount (Scenario 1), this number is honestly $0.00
until real orders use the code — not a misrepresentation, just an accurate
reflection of a brand-new discount's history.

## 36. `domain_category = "DISCOUNT"`, not `"CHECKOUT"`, for the Checkout Specialist agent

`IssueCategory` has had both `CHECKOUT` and `DISCOUNT` values since Sprint
2's original 8-value enum (Database Architecture doc), unused until now.
The agent built this step only ever detects/fixes discount-pricing
overlaps — never the broader checkout-flow health checks (payment methods,
shipping sanity, buy-now/cart-flow validation) that
`SPRINT4_AI_WORKFORCE_VISION.md`'s own Phase A table explicitly defers past
the hackathon. **Resolution:** registered under `domain_category =
'DISCOUNT'`, reserving `'CHECKOUT'` for a genuinely broader future
specialist without a naming collision or a misleading "Checkout Specialist
already covers checkout health" impression. The agent's user-facing
identifier/display name ("Checkout Specialist") is unaffected — this is a
routing-key distinction only.

## 37. Discount inspection inserted into the existing Celery chain as a new step, not merged into `tasks.inspect_catalog`

Sprint 2 named exactly two Celery tasks for the inspection pipeline
(`tasks.inspect_catalog`, `tasks.compile_shift_report`); Sprint 3 already
added a third (`tasks.plan_cognitive_tasks`) between them. Adding discount
detection could have gone inside `inspect_catalog` itself (one task, two
data sources) or as its own task in the chain. **Resolution:** a new task,
`tasks.inspect_discounts`, dispatched by `inspect_catalog` and itself
dispatching `tasks.plan_cognitive_tasks` — keeps each specialist's Observe
step independently retryable/queueable (same `celery:observation` queue,
its own bounded retry backoff) without growing `inspect_catalog`'s own
scope, and matches the precedent Sprint 3 already set by inserting
`plan_cognitive_tasks` as a new link in the same chain rather than folding
planning into an existing task.

## 38. Theme file writes (`themeFilesUpsert` and the legacy REST Asset endpoint) require a Shopify-granted exemption this project cannot obtain — presented to the user as a locked decision point before any Step 3 code was written (2026-08-02)

Researched immediately at the start of Step 3, per the standing "research the
exact API specifics right before that step starts, flag anything that
doesn't check out" rule. Confirmed via two independent live-docs fetches:
Shopify's `themeFilesUpsert` GraphQL mutation page states plainly that "the
user needs `write_themes` **and an exemption from Shopify** to modify theme
files"; the legacy REST `Asset` resource page confirms the same restriction
has applied to `PUT`/`DELETE` requests since Admin API 2023-04, with the
same manual, Google-Form-based exemption process, deadline already passed
(March 31, 2024). This is a materially different, more severe category of
restriction than the two prior "deprecated field" discoveries this
engagement (Sprint 3's fully-removed `productImageUpdate`, Step 2's
deprecated-but-functional `codeDiscountNodes`) — those were code-only
fixes; this one requires a manual approval process external to the
codebase entirely, with no guarantee of approval, that could not
realistically be completed within a hackathon timeline. Read access
(`theme(id:) { files }`) has no such restriction — only `read_themes`,
already granted.

**Resolution (user-approved, 2026-08-02, presented via structured
options before any Step 3 code was written):** Theme Guardian ships with
full snapshot/hash/diff/LLM-explain, but restore is never an autonomous
Shopify write. The approval-gated (LEVEL_3_HIGH) fix instead generates a
guided-resolution bundle — the exact baseline content plus a Theme Editor
deep link (`/admin/themes/{id}/editor`) — for the merchant to apply
themselves. Verification re-fetches the live file and passes once its
content matches the baseline, regardless of how the merchant applied the
fix. This was explicitly framed to the user as a deliberate Level-3
high-risk security guardrail, not a workaround, and the user selected it
over two alternatives (submitting the real exemption request and building
the gated-but-likely-403 mutation path; or narrowing "restore" to
app-embed/theme-app-extension assets only, which don't require the
exemption but would have changed Scenario 2's premise away from the
vision doc's literal "rogue developer edited the live theme" framing).

## 39. `GENERATE_THEME_RESTORE_GUIDE`'s rollback mutation is `"none"` — a real, documented value, not a placeholder gap

Every other action type's rollback either reverses a real Shopify mutation
(`productImageUpdate`/`productUpdate` restore to a placeholder value per
ADR-030's known limitation, `discountCodeActivate` exactly, `scriptTagDelete`
exactly) or doesn't exist. Theme Guardian's guided restore never calls
Shopify at all (see item 38), so there is genuinely nothing to roll back.
**Resolution:** `execution_plan["items"][i]["rollback"]["mutation"] = "none"`
is a first-class, explicitly-handled value in
`rollback_cognitive_task.py::_dispatch_rollback_mutation` (returns
`{"skipped": True, ...}` rather than raising `ValueError` or silently
no-op'ing) — chosen over leaving it unhandled (would raise past the
existing `except ShopifyApiProblem` guard, since `ValueError` isn't caught
there) or inventing a new `TaskStatus`/`VerificationStatus` value for
"pending manual action" (schema-invasive for what a plain data value
already expresses honestly). A side effect: when the merchant hasn't yet
applied the guide, `VerifyExecution` reports the task as "FAILED" and the
issue reopens to "OPEN" — semantically this means "not yet applied," not
"an error occurred," but no new status vocabulary was introduced for it
this step. Flagged here exactly like ADR-030's rollback-placeholder gap
was flagged — a documented, accepted modeling compromise, not a silent one.

## 40. Rogue Developer Theme Break (Scenario 2) can only auto-trigger its tracking half — the theme-corruption half hits the same write-exemption wall as the restore side

Discovered while wiring the Demo Incident Generator: deliberately
corrupting the live theme file on cue (to make Scenario 2 self-triggering,
like Scenario 1) would itself require `themeFilesUpsert`/the REST Asset
write endpoint — the exact same Shopify-granted exemption item 38
establishes this project does not have. There is no write path to theme
files at all, for injection or repair alike. **Resolution:** Scenario 2 is
wired (`WIRED_SCENARIO_IDS` includes it), but `TriggerDemoIncident` only
ever executes the script-tag-removal half (create a Meta Pixel-pattern
script tag, seed its `tracking_snapshots` baseline, then delete it) —
genuinely exercising Tracking Specialist end-to-end. The theme half is
documented, not silently dropped: `ROGUE_DEVELOPER_THEME_BREAK`'s own
description and the trigger result's `notes` field both state plainly that
demoing Theme Guardian's flow requires a human to manually edit
`sections/main-product.liquid` in the Shopify Theme Editor once before the
demo.

## 41. `evidence_data` carries full theme file content for `theme_file_diverged_from_baseline` — a deliberate exception to the lightweight-metadata convention

Every prior fix type's Plan step reads structural data from
`affected_resources` (GIDs, ordering) and keeps `evidence_data` to a
`fix_check` identifier plus small metadata (see Step 2's own established
convention). Theme file content has no stable GID a later lookup could
resolve the way a product or discount GID can — the content itself IS the
data the restore action and the LLM explanation both need, and `Agent.
propose_action(issue: Issue)` has no repository access to fetch it
separately at Plan time. **Resolution:** `theme_inspection.py`'s worker
task persists the full `baseline_content`/`current_content` directly in
`evidence_data` for this one fix_check, with an explicit code comment
flagging it as an intentional exception rather than an oversight. No PII
is at risk (theme Liquid/template source, not customer data), and content
size is bounded by the small, fixed watch-list (`DEFAULT_WATCHED_FILENAMES`)
this step scopes detection to.

## 42. `write_script_tags` is a new scope this step requires — Steps 1/2 both shipped with zero new scope

Tracking Specialist's fix (`scriptTagCreate`) is the first auto-fixable
action this sprint that needs a scope beyond what was already granted
(`read_script_tags` was already in the list for the Sprint 1 baseline
discovery scan). **Resolution:** added `write_script_tags` to the
documented scope list (README) and flagged here rather than silently
assuming it — unlike theme files, `scriptTagCreate`/`scriptTagDelete` have
no exemption requirement beyond the scope itself (confirmed via live docs),
so this is a normal, grantable scope addition, not a repeat of item 38.

## 43. Chief Ops AI is deliberately not an `Agent` subclass, and its Multi-Agent Handshake icon convention is this step's own invention

Two judgment calls from Step 4 ("Chief Ops AI orchestration"), neither
covered by any source document:

1. Every specialist to date (Product Quality, Checkout, Theme Guardian,
   Tracking) implements the `Agent` ABC, is keyed by `domain_category` in
   `PlanCognitiveTasks`'s registry, and has its own `agents` table row.
   Chief Ops AI does none of this — it never detects an issue or proposes a
   Shopify mutation, it only synthesizes already-persisted findings across
   2+ specialist categories after planning has finished. Forcing it into
   the `Agent` ABC (which requires `analyze()`/`propose_action()` tied to a
   single `domain_category`) would misrepresent what it does. **Resolution:**
   `ChiefOpsSynthesizer` (`domain/chief_ops.py`) is a standalone class, not
   an `Agent`, and is not registered in `PlanCognitiveTasks`'s agent dict —
   it runs once, at shift-compilation time, in `workers/tasks/shift_report.py`.
   No new `agents` table row was seeded for it; it has no `identifier`
   because it never appears in `Issue.agent_id`/audit-log `actor_id`.
2. The Vision doc's Three Locked Additions #2 mandates a "🟢/⚡/🧠 treatment"
   for the Multi-Agent Handshake log but never defines what each icon means
   — no source document does. **Resolution:** a deterministic (never
   LLM-assigned) convention was defined in `domain/chief_ops.py`: ⚡ = this
   issue was actually auto-executed by NightShift this shift, 🧠 = awaiting
   a merchant's approval, 🟢 = detected, informational only. Documented
   there as this step's own rendering decision, not a re-derivation of
   anything specified elsewhere.

## 44. `chief_ops_briefing` and Ask NightShift both reuse existing schema — no new migration this step

Both of Step 4's two surfaces (Executive Briefing, Ask NightShift) fit
entirely inside data Sprint 2/3/4 already persist: `chief_ops_briefing` is
a new key inside `shift_reports.report_json` (an existing JSONB column,
not a new table/column), and Ask NightShift is a stateless read over
`ShiftReportRepository.list_recent_for_store` (a new repository method,
zero new tables) — no conversation-history table was added, matching the
Vision doc's own framing of Phase B as "zero new infra... the only real
dependency is Phase A shipping first." Revisit if a later step calls for
persisted multi-turn conversation.

## 45. Shift Replay needs a small, genuine backend addition — the Vision doc's "not a backend build" framing is slightly optimistic

Phase C's own text: "`audit_logs` is already timestamped and shift-scoped —
this is a frontend timeline component over data that already exists, not a
backend build." True of the data, not quite true of the API surface: the
only existing read path, `GET /api/v1/work-log`, lists a store's entries
newest-first with cursor pagination and has no `shift_id` filter, and its
`WorkLogEntryDTO` doesn't expose `shift_id` at all — there is genuinely no
way for a frontend to ask "just this one shift's entries" today.
**Resolution:** the smallest addition that satisfies the stated need, not a
new domain concept: `AuditLogRepository.list_for_shift(shift_id)`
(chronological ascending, mirroring `get_for_task`'s existing pattern) plus
one new read-only endpoint, `GET /api/v1/shifts/{shift_id}/replay`, gated by
the same store-ownership check `GET /api/v1/tasks/{id}` already uses. No new
table, no new write path — purely a shift-scoped view over rows every prior
sprint already writes.

## 46. The 🟢/⚡/🧠 icon treatment is extended to real `audit_logs.action` values for Shift Replay/Work Log — a second, explicitly-scoped icon mapping, not a reuse of Step 4's per-issue one

Step 4's icon convention (CONFLICTS.md item 43) is assigned per-*issue*
(auto-executed / awaiting approval / informational). Shift Replay and Work
Log render per-*audit-log-entry* events instead (`TASK_PLANNED`,
`EXECUTION_COMPLETED`, `VERIFICATION_PASSED`, failures, merchant approval
decisions, demo triggers) — a different granularity the same three symbols
can't honestly cover (a failed execution is neither "auto-executed
successfully" nor "informational only"). **Resolution:** a second,
explicitly separate mapping in the new `domain/replay.py`
(`icon_for_action`), reusing ⚡/🧠/🟢 where the meaning genuinely lines up
(⚡ = a mutation/rollback actually completed, 🧠 = planned/pending a
decision, 🟢 = verification confirmed the fix held) and introducing ⚠️ for
genuine failures and 🎬 for demo-triggered entries — both meaningfully
different outcomes that would misrepresent a real failure or an
intentionally-injected demo incident if squeezed into the original three.
Assigned server-side, deterministically, from the real `action` string —
never LLM-derived, same discipline as item 43.

## 47. Counterfactual ROI Widget's exposure-duration fallback is grounded in this codebase's own synchronous-execution design, not invented

The Vision doc's Locked Addition #1 requires "first detected in Shift #N,
resolved in Shift #N+1" phrasing (not a fabricated bleed rate) for issue
types without a true origin timestamp — i.e. everything except Duplicate
Discount's real `duplicate_created_at`. Rather than an extra query to find
an issue's true originating shift number, the widget uses the current
shift's own number for both "detected" and "resolved" in the fallback
case. This is not a shortcut around missing data — it is accurate: per
`shift_report.py`'s own documented design, `completed_tasks[]` only ever
lists tasks that reached SUCCESS via *this shift's* synchronous auto-execute
path (Plan → Assess Risk → Execute → Verify all run in the same shift for
LEVEL_1/auto-approved-LEVEL_2 issues), so "detected and resolved within
Shift #N" is a true statement for every issue the widget renders this way,
never an approximation dressed up as fact. Issues that took multiple shifts
to resolve (an approval sat pending for days) are out of scope for this
widget's fallback path and simply aren't rendered with a manufactured
number — consistent with "never invent data not present in context."

## 48. Chief Ops synthesis gate lowered from 2+ categories to 1+ findings (user-directed, Sprint 5 Phase 1.3)

Step 4 originally gated Chief Ops's LLM synthesis call on 2+ distinct
specialist categories having findings in a shift — the Vision doc's own
worked example ("Theme Guardian noticed a change, Tracking Specialist saw
the pixel disappear") requires two agents to correlate, and a single
specialist's finding had nothing to correlate against, so it used a
deterministic one-line fallback instead, at zero LLM cost. Sprint 5's XPRIZE
Polish roadmap explicitly asks that the Executive Briefing never show
"Rule-based summary (AI narration unavailable this shift)" copy, including
on the single-specialist case that previously produced it by design, not by
failure. Per direct user instruction, `should_synthesize()` now returns True
for any shift with 1+ specialist findings (`MINIMUM_TURNS_FOR_SYNTHESIS =
1`, `domain/chief_ops.py`); only a shift with zero findings still skips the
LLM outright. This is a deliberate reversal of a cost-conscious design
choice, not a bug fix — flagging it here because it changes the project's
LLM-call volume assumption baked into `LLM_MAX_CALLS_PER_DAY` (addressed by
raising that default from 30 to 200, see DECISIONS.md ADR-040). The
"AI narration unavailable" wording itself is also removed from both
`ExecutiveBriefing.tsx` and `AskNightShift.tsx`; the remaining fallback
copy is now only reachable on a genuine in-shift LLM failure (budget
ceiling or a bad structured response), so it is reworded to read as a normal
operational note rather than an error state.

## 49. Theme Guardian diff card's header banner does not say "Gemini 1.5 Pro" (user-directed spec, adjusted for accuracy)

The user's approved Sprint 5 Phase 3.2 spec (Option 1 — "honest text-diff,
polished UI") reads verbatim: "Header Banner: Display Gemini 1.5 Pro's
structured analysis directly above the diff explaining the storefront
impact in plain English." Two facts make the literal wording inaccurate:
`gemini-1.5-pro` is a retired model (item 12 above; the real Gemini
substitute is `gemini-2.5-pro`), and this codebase has no per-issue
model/provider attribution column at all (item 15 above) — `Issue` never
recorded which model or provider generated a given `description`. The
active default LLM provider is also Anthropic Claude Haiku, not Gemini
(ADR-023) — Gemini is the dormant, swappable alternate.

Labeling the banner "Gemini 1.5 Pro" would therefore be a fabricated
attribution: not what actually ran, and not even a real model name.
`ThemeGuardianDiffCard.tsx`'s banner instead reads "NightShift AI Analysis"
and renders the real `Issue.description` Theme Guardian already generated
(LLM-authored or its documented deterministic fallback) — everything the
user's spec actually needed (a plain-English storefront-impact explanation
directly above the diff) without the invented model claim. Flagged here
rather than silently substituted, per this engagement's standing "stop and
flag conflicts rather than guess" rule.

## 50. Theme Guardian diff card's baseline/current content and Theme Editor deep link are surfaced via additive `PendingApprovalDTO` fields, not a new endpoint

`GET /api/v1/approvals` (the Approval Center's live data source) previously
returned only `PendingApprovalDTO`'s original 8 fields — no evidence data at
all. Building Phase 3.2's diff card needed the originating Issue's
`description` (Theme Guardian's plain-English explanation) and
`evidence_data` (baseline/current file content, filename, theme_id) at the
point a merchant is deciding whether to approve — before any execution has
happened, so the already-existing `GET /api/v1/tasks/{id}` (which only
has data once a task has an approval/execution record) wasn't the right
fit either. `issue = await issues.get_by_id(approval.issue_id)` was already
being fetched in `list_pending_approvals` for the existing fields, so
`description`/`evidence_data` are passed through unmodified as two new
optional `PendingApprovalDTO` fields — no new endpoint, no new table,
mirroring the exact "additive DTO field, same already-fetched object"
pattern used throughout this project (e.g. `issue_id` on `CompletedTaskDTO`,
Step 4). The Theme Editor deep link itself
(`https://admin.shopify.com/store/{handle}/themes/{id}`) is computed
client-side from `evidence_data.theme_id` + the shop domain, mirroring
`execute_cognitive_task.py`'s exact string construction — safe to do before
execution because it's a pure, deterministic URL format, not something that
depends on a mutation having run.

## 51. Chaos Panel dispatches a background shift from inside the existing demo-incident route, not a separate "trigger shift" endpoint

The Sprint 5 Phase 4 roadmap requires that clicking a scenario button
"immediately trigger the incident, run a background shift, and update the
UI live." Rather than adding a second, separate HTTP endpoint the frontend
would need to call right after the incident-trigger call (two round-trips,
and a second endpoint with no caller if the frontend always calls both
together anyway), `POST /api/v1/demo/incidents/{scenario_id}` itself now
also dispatches `tasks.inspect_catalog` for the same store once the
incident use case succeeds, via a new `TaskDispatcher.dispatch_inspect_catalog`
method mirroring the exact pattern `dispatch_store_discovery`/
`dispatch_execute_cognitive_task` already established. One click, one
request, both effects — no unused API surface.

## 52. Catalog SEO Collapse (Scenario 3) is now wired, reusing Sprint 2's real auto-fix mutations in reverse

`TriggerDemoIncident._trigger_catalog_seo_collapse` picks the catalog's
first active product (via the existing `fetch_catalog_for_inspection`, no
new GraphQL query) and calls `update_product_description(description_html="")`
and, if the product has an image, `update_product_image_alt_text(alt_text="")`
— the exact same two mutations Sprint 3's real auto-fix path already uses,
just called in the corrupting direction instead of the repairing one. This
guarantees the very next `tasks.inspect_catalog` run genuinely re-detects
both `missing_alt_text` and `thin_description` via `domain/inspection.py`'s
own unmodified checks (`MIN_DESCRIPTION_WORDS = 20`, an empty description
trivially qualifies) — no new detection logic, no fabricated finding. A
store with zero active products raises a new, honest
`DemoScenarioNoEligibleProductProblem` (422) rather than silently no-op'ing.

## 53. Chaos Panel visibility is gated by a new `demo_mode_enabled` field on `GET /api/v1/stores/me`, not a frontend-only toggle

The floating Chaos Panel must never appear promising a capability that
would actually 404 (the demo endpoints already return `DemoModeDisabledProblem`
when `Settings.demo_mode_enabled` is off). Rather than a separate
`NEXT_PUBLIC_*` frontend env var that could drift out of sync with the
backend's own flag, `StoreSnapshotResponse` gained one additive field,
`demo_mode_enabled`, sourced directly from the same `Settings.demo_mode_enabled`
the demo routes are gated on — the two can never disagree, and the
frontend has exactly one source of truth for whether to render the panel.

## 54. `RISK_LEVEL_REASONING` was keyed by risk level, not action type — every LEVEL_2_MODERATE action showed the same paragraph naming all three unrelated action types

Reported directly by the user from Work Log/Shift Replay screenshots: a
Checkout Specialist (`DEACTIVATE_DUPLICATE_DISCOUNT`) audit log entry's
rationale read "Product description rewrites are customer-facing content
changes that can affect SEO..." and a Product Quality
(`REWRITE_PRODUCT_DESCRIPTION`) entry read "Deactivating a duplicate
discount code is reversible...". Root cause: `domain/risk.py::assess_risk_level`
returned `RISK_LEVEL_REASONING[level]`, and the `LEVEL_2_MODERATE` entry in
that dict was a single string concatenating one sentence about each of the
three action types that happen to share that risk level
(`REWRITE_PRODUCT_DESCRIPTION`, `DEACTIVATE_DUPLICATE_DISCOUNT`,
`RECREATE_TRACKING_SCRIPT_TAG`) — every one of them displayed the exact
same three-sentence blob, `TASK_PLANNED`'s `rationale` field being set
directly to this text (`plan_cognitive_tasks.py`), which both Work Log and
Shift Replay render verbatim.

Fix: added `ACTION_TYPE_REASONING`, keyed by `action_type` — one sentence
per action type, never mentioning another action type's specifics.
`assess_risk_level` now looks up the action-specific sentence first,
falling back to the old level-wide generic text only for an action type
with no entry (i.e. genuinely unknown/future action types) rather than
crashing. `RISK_LEVEL_REASONING` itself was trimmed down to short, truly
generic per-level fallback text (no longer naming any specific action
type), since it's no longer the primary source of reasoning text for any
currently-wired action.

## 55. `GET /api/v1/shifts/latest` gains an additive `previous_shift_health_score` field, computed at request time — does this break the "immutable, never recomputed" invariant?

The Tonight's Impact widget's "Store Health Delta" (e.g. "78 ➔ 92 (+14
pts)") needs a second, prior data point. The underlying data already
exists — `ShiftReportRepository.list_recent_for_store(store_id, limit=2)`
— but no endpoint exposed more than the single latest shift's `health_score`.
This endpoint's own docstring makes a real promise: "the report is
immutable after publication... this endpoint never recomputes the report
at request time." Joining in a second shift's `health_score` at request
time looks, at first glance, like it violates that.

Resolved: it doesn't. The current shift's own `report_json` is still
returned completely unmodified — every key a merchant reads about *this*
shift is exactly what was durably persisted. The one additive key,
`previous_shift_health_score`, states a fact about a *different*,
already-published, equally-immutable `ShiftReport` row (the prior one)
that just happens to be joined in for convenience rather than requiring a
second frontend round-trip. `get_latest_shift` now calls
`list_recent_for_store(store_id, limit=2)` instead of
`get_latest_for_store`, uses `recent[0]` exactly as before, and reads
`recent[1].report_json.get("health_score")` (None on a store's first-ever
shift) as the additive field. No new table, no schema change, no migration.

## 56. Tonight's Impact widget's "Merchant Actions Status" says "Fixes Verified", not "Autonomous Fixes Verified"

The user's own example wording was "1 Approval Required • 5 Autonomous
Fixes Verified". Nothing in `completed_tasks[]` (or anywhere else in the
persisted report) distinguishes a task that executed with zero merchant
involvement from one that executed only after a merchant approved it —
both end up as `status: "SUCCESS"` in the same list, keyed only by
`risk_level`/`verified`, not by "how it got approved." Claiming
"Autonomous" for a mix of both would be an unverifiable label this data
can't actually back up — the same "never invent data not present in
context" discipline this codebase has held since Sprint 2. The widget
instead reads "{N} Approval(s) Required • {M} Fixes Verified", both counts
taken directly from the real `pending_approvals[]`/`completed_tasks[]`
arrays already on the payload.

## 57. Anthropic support fully removed — Gemini is now the sole LLM provider (user-directed, supersedes item 19)

Item 19 documented switching the default per-specialist LLM provider to
Anthropic (Claude Haiku 4.5) to use the user's own metered Anthropic
credits during cost-conscious local testing. Ahead of the hackathon
submission — which requires the project to run entirely on Gemini/Google
Cloud infrastructure, and per the user's own explicit instruction to strip
every Anthropic reference from the repo before their one-time final
commit — that provider split is gone: `AnthropicClient` and its test file
are deleted, `infrastructure/llm/factory.py` now raises `ValueError` for
any `LLM_PROVIDER` other than `GEMINI`, and every config default
(`.env.example`, `deploy/secrets-setup.md`, the Sprint 2/4 migration seed
rows) now reflects Gemini as the only supported provider. Item 19's
reasoning above is left intact as the historical record of why the
Anthropic branch existed in the first place — this entry documents its
removal rather than rewriting that history.
