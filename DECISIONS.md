# NightShift AI Architecture Decisions

Current truth, not history — see `CONFLICTS.md` for why each of these was in
dispute. Future sprints should reference this file directly rather than
re-reading chat history or re-deriving these from source docs.

## ADR-001

ORM

SQLAlchemy 2.0 Async + Alembic

Status

Accepted

Reason

Matches Sprint 1's own directory layout and Acceptance Checklist. SATDD's
Prisma/camelCase illustration is non-binding.

---

## ADR-002

Vector Storage

PostgreSQL + pgvector (no Qdrant)

Status

Accepted

Reason

`store_memories` is a native Postgres table. Avoids standing up a second
database when nothing in Sprint 1's acceptance criteria requires one.

---

## ADR-003

Shift Numbering

Per-store sequence, computed in application logic
(`ShiftRepository.next_shift_number`)

Status

Accepted

Reason

A global `SERIAL` column contradicts the intended per-store
`UNIQUE(store_id, shift_number)` numbering and would leak sequence gaps
across tenants.

---

## ADR-004

Revenue Field Naming

`estimated_revenue_protected`

Status

Accepted

Reason

Matches the migration, the Database Architecture doc, and the product docs'
own "revenue protected" phrasing. `estimated_revenue_saved` does not appear
anywhere else.

---

## ADR-005

OAuth Callback Failure Status Code

HTTP 502 on Shopify token-exchange failure

Status

Accepted

Reason

Sprint 1's own Story 1 failure-case narrative is explicit: "Shopify API
timeout during token exchange → HTTP 502."

---

## ADR-006

Row-Level Security

Enabled on `stores`, `store_tokens`, `shifts`, `metrics_hourly`,
`store_memories`, scoped by the `app.current_store_id` Postgres GUC

Status

Accepted, with a known gap (see ADR-012)

Reason

Blueprint and Database Architecture doc both mandate RLS on every table
holding merchant data.

---

## ADR-007

Baseline Schema Scope

`metrics_hourly` and `store_memories` included in the Sprint 1 migration;
`issues` and `audit_logs` deferred

Status

Accepted

Reason

The first two are written to by Sprint 1's own Story 2, Feature 3, and AI
Specification sections. Nothing in Sprint 1's acceptance criteria writes to
`issues` or `audit_logs` — building them now would be speculative.

---

## ADR-008

Webhook Listeners

Not implemented in Sprint 1

Status

Deferred

Reason

Sprint 1's own Definition of Done and Acceptance Checklist never require a
webhook endpoint; baseline sync is triggered by the post-OAuth Celery task
only. SATDD's day-by-day plan mentions webhooks, but SATDD is not the
binding spec.

---

## ADR-009

API Error Format

RFC 7807 `application/problem+json` for every error response, no bare
strings or default framework error pages

Status

Accepted

Reason

Explicit Sprint 1 Technical Objective (Section 8.2). Enforced centrally via
`register_error_handlers` in `app/api/errors.py`, including a catch-all
handler so unhandled exceptions never leak a plain-text response.

---

## ADR-010

Session Token → Store Resolution

Resolve the current store from the Shopify session token's `dest` claim
(the shop's myshopify.com domain) via `StoreRepository.get_by_shopify_domain`,
falling back to the `X-Shopify-Shop-Domain` header if `dest` is absent

Status

Accepted (corrected 2026-07-31, during first live end-to-end verification)

Reason

Real Shopify session tokens (App Bridge's `getSessionToken()`) only ever
carry Shopify's own standard JWT claims — `iss`/`dest`/`aud`/`sub`/`exp`/
`nbf`/`iat`/`jti`/`sid` — never a custom `store_id` claim. The original
Sprint 1 implementation (and its unit tests) incorrectly assumed a
`store_id` claim would be present, which meant `GET /api/v1/stores/me`
could never succeed against a real embedded session. Caught only once
tested against a live Shopify Admin session rather than a hand-built test
JWT.

---

## ADR-011

Session Token Signature Verification

Structural validation only (decode + expiry check, signature verification
disabled) in Sprint 1

Status

Deferred — must be resolved before production traffic

Reason

Full verification requires fetching and caching Shopify's JWKS public keys,
which is a production integration point, not a local-dev concern. Tracked
explicitly in `get_current_store_id`'s docstring so it isn't forgotten.

---

## ADR-012

RLS Tenant GUC Scoping

`get_db_session` (used by `get_store_repository`) does not currently run
`SET LOCAL app.current_store_id` before queries, even though
`tenant_scoped_session` exists in `session.py` for exactly this

Status

Known gap — do not close silently, needs an explicit decision before
Sprint 2 introduces a non-owner DB role

Reason

Postgres skips RLS policies entirely for the table-owning role, and
`docker-compose.yml`'s `nightshift` user currently owns every table, so this
has no observable effect in local dev. It will matter the moment a
least-privilege, non-owner application role is introduced (the RLS-correct
production posture) — at that point every store-scoped query will need to
run through `tenant_scoped_session`, and `stores` table inserts during OAuth
provisioning will need a `WITH CHECK` policy that permits row creation
without the GUC already matching the new row's own id.

---

## ADR-013

Request-Scoped Transaction Commit

`get_db_session` (`app/api/deps.py`) explicitly commits on successful
request completion and rolls back on any exception, wrapping the whole
request in a single unit-of-work

Status

Accepted (corrected 2026-07-31, found during first real end-to-end local
verification of the OAuth ingress + baseline discovery flow)

Reason

Repository methods only ever call `session.flush()`, and the use-case layer
(`CompleteOAuthInstallation`) never touches a raw session by design — it
only holds repository Protocol ports, per the Clean Architecture layering
rule. Nothing was committing the transaction, so `async with
session_factory() as session:` silently rolled back every write on session
close. OAuth installation appeared to succeed end-to-end (redirected back
to the app, no errors raised), but the `Organization`/`Store`/`StoreToken`
rows were never durably written — confirmed directly by writing a row via
the old code path and observing it was gone from a fresh session, and
independently by the Celery discovery worker logging
`discovery_task_store_not_found` for a store_id that had just been
"created." Any future FastAPI dependency that opens its own DB session must
follow the same commit/rollback pattern, not just `yield session`.

---

## ADR-014

Store Health Score Algorithm

Deduction-from-100 model (`domain/health.py::calculate_store_health`):
`HealthScore = 100 - Σ(bounded per-category deductions)`, categories keyed
by the `issue_category` enum, per-issue penalties by severity (CRITICAL 15,
HIGH 8, MEDIUM 3, LOW 1), each category's total deduction capped at its own
weight (Checkout 25, Pixel Tracking 20, Product Quality 20, SEO 15, Discount
10, Performance 10)

Status

Accepted (approved by the user 2026-07-31, replacing Sprint 1's algorithm)

Reason

Sprint 1's weighted-average-of-category-subscores model and Sprint 2's own
deduction model compute the same thing two incompatible ways. Sprint 2's
Feature 3 is titled the "Composite Store Health Engine" and its DoD requires
recalculating the score "post-inspection" — the intended successor
algorithm, not a second code path to maintain alongside the first. Sprint
1's `compute_health_score` API and its dedicated tests are retired; the one
caller (`tasks.store_discovery`) now calls `calculate_store_health([])` at
baseline-scan time (no issues exist yet, so the score is still the neutral
100 ceiling — same observable behavior as before for that one call site).

---

## ADR-015

Health Score: Unscored Issue Categories

`INVENTORY` and `TRUST_INDICATOR` issues are excluded from the numeric
Health Score (no assigned category cap) but reported via
`HealthScoreResult.unscored_categories` and a structured warning log, not
silently dropped

Status

Accepted

Reason

Sprint 2's own reference implementation for the deduction model only
defines caps for 6 of the `issue_category` enum's 8 values and silently
drops the other 2 with no warning at all. Silently dropping data the system
itself persisted is worse than excluding it visibly — this preserves the
6-category scoring scope Sprint 2 actually specifies while making the gap
observable instead of invisible.

---

## ADR-016

Gemini Model Identifier

`gemini-2.5-pro`, exposed as configurable `Settings.gemini_model_name`
(env var `GEMINI_MODEL_NAME`), not hardcoded

Status

Accepted (approved by the user 2026-07-31)

Reason

Every source document hardcodes `gemini-1.5-pro`. Verified against Google's
current model lifecycle documentation: both dated 1.5 Pro API versions
passed their retirement dates in 2025 — a literal implementation would fail
at request time. `gemini-2.5-pro` is the closest "Pro"-tier reasoning
replacement and supports the same structured `response_schema` output the
AI Specification requires. Kept configurable rather than hardcoded so a
future model change is a config edit, not a code change.

---

## ADR-017

Gemini API Key Configuration

Reuse the existing `google_ai_api_key` setting (env var `GOOGLE_AI_API_KEY`)
for the Product Quality Agent's Gemini SDK calls, rather than add a second
`GEMINI_API_KEY` variable

Status

Accepted

Reason

`google_ai_api_key` was already declared in Sprint 1 specifically as
"AI embeddings ... not invoked until Sprint 2" — this is that invocation.
Adding a second, functionally identical credential would be redundant.

---

## ADR-018

Product Quality Agent Issue Schema Naming

`DetectedIssueSchema` (not `AgentIssueSchema`)

Status

Accepted

Reason

Sprint 2's own Story 2 acceptance criteria names `AgentIssueSchema`, but no
such Pydantic model is ever defined anywhere in the document — only
`DetectedIssueSchema`, in the Feature 2 code sample. A naming slip in the
source doc; the schema that actually has a concrete definition wins.

---

## ADR-019

Issue AI Metadata Storage

`model_identifier` and `prompt_version` are stored as keys inside
`issues.evidence_data` (JSONB), not as dedicated columns

Status

Accepted

Reason

Story 2's AC requires persisting both values, but the `issues` table DDL
(identical across the Sprint 2 Spec and the Database Architecture doc) has
no dedicated columns for either — only `evidence_data`. No document calls
for a schema change to add them, so `evidence_data` is the only place they
can live without inventing new columns neither spec asks for.

---

## ADR-020

Frontend Animation & Data-Fetching Approach

Plain CSS `@keyframes` animation for issue-card entrance (not
framer-motion); existing `fetch()` + React Query pattern (not tRPC)

Status

Accepted

Reason

Neither `framer-motion` nor any tRPC package is a dependency of `apps/web`.
The visual effect the UI Spec calls for (staggered fade-in/slide-up) is
fully achievable with a CSS keyframe, and the existing fetch + React Query
pattern already satisfies Sprint 2's data-fetching needs — avoids adding two
new runtime dependencies mid-sprint for a purely illustrative implementation
detail in the source spec.

---

## ADR-021

Morning Shift Report API Contract

`GET /api/v1/shifts/latest` implements Sprint 2's own narrower JSON shape
(`health_score` top-level, `metrics{issues_detected, issues_resolved,
estimated_revenue_protected_usd, time_saved_hours}`, flat `issues[]`) — not
the API Contract Specification's or SATDD's richer, task/approval-aware
shapes

Status

Accepted

Reason

The richer shapes both depend on `cognitive_tasks`/`approvals` tables that
don't exist until a later sprint. Sprint 2's own shape is the only one
implementable against this sprint's actual schema. Revisit once the
Task/Approval domain lands.

---

## ADR-022

Row-Level Security on Sprint 2 Tables

RLS enabled on `issues` and `shift_reports` (`store_id`-scoped, same policy
pattern as ADR-006); `agents` has no `store_id` column and is exempt (shared
AI Employee registry, not per-tenant merchant data)

Status

Accepted — compounds the still-open ADR-012 gap

Reason

The Database Architecture doc's and Technical Blueprint's RLS axiom applies
unconditionally to every merchant-data table; Sprint 2's own migration draft
omitted RLS entirely. Matches ADR-006's precedent from Sprint 1. Note this
does not close ADR-012 (the RLS tenant GUC is still never actually set by
`get_db_session`) — Sprint 2 does not introduce a non-owner DB role, so
ADR-012 remains open for whichever future sprint does.

---

## ADR-023

LLM Provider

Anthropic (`claude-haiku-4-5-20251001`) is the default provider, selected via
`Settings.llm_provider` and built by `infrastructure/llm/factory.py`; Gemini
remains fully supported as the alternate provider (`LLM_PROVIDER=GEMINI`)

Status

Accepted (user-directed, 2026-07-31 — the user has their own metered
Anthropic API credits and asked to use them for testing instead of Gemini)

Reason

Every source document specifies Gemini, but this is a runtime/cost decision
the user is entitled to make directly, not a documents-vs-repo conflict.
`ProductQualityAgent` was refactored to depend only on a `StructuredLlmClient`
structural contract (`model_name` + `generate_structured`) so it has no
knowledge of which provider is active — switching providers again is a
`.env` change (`LLM_PROVIDER`), never a code change. Anthropic's native
structured-outputs feature (`messages.parse(output_format=PydanticModel)`,
GA for Claude 4.5+ models including Haiku 4.5) is the direct equivalent of
Gemini's `response_schema` — same guarantee of constrained, schema-valid
JSON output, not just "asked nicely."

---

## ADR-024

LLM Cost Safety Guardrails

Three independent mechanisms, all enabled by default: (1) `max_tokens` cap
per call (`Settings.llm_max_output_tokens`, default 1024) with
`stop_reason == "max_tokens"` treated as a hard failure; (2) per-call
findings truncation (`Settings.llm_max_findings_per_call`, default 50,
severity-prioritized); (3) a Redis-backed daily call ceiling
(`Settings.llm_max_calls_per_day`, default 30, enforced by
`LlmCallBudgetGuard` in `infrastructure/llm/budget_guard.py`)

Status

Accepted (user-directed, 2026-07-31 — explicit request: "make sure it does
not go into an infinite loop and spend all my credits")

Reason

The user is testing against a real, metered $10 Anthropic API budget and
asked for a hard guarantee nothing could exhaust it in one session. Celery's
own retry bound (`tasks.inspect_catalog`: max 5 retries) already caps a
single failing shift at 6 LLM calls, but that alone doesn't protect against
a bug that triggers many separate shifts, or a developer re-running a test
script in a loop. The daily budget guard is deliberately backed by Redis
(not just an in-process counter) so it holds across Celery worker restarts
and multiple worker processes — the ceiling can't be accidentally reset by
restarting the worker. Once any of the three guards trips,
`ProductQualityAgent` falls back to its existing deterministic rule-based
path (the same one already used for schema-validation failures) rather than
failing the shift outright — a degraded shift report is always better than
no report, and never costs another token.

---

## ADR-025

Sprint 3 Schema: Additive Tables/Columns Beyond the Database Architecture Doc

`rollbacks` table (new, reusing the `execution_status` enum type rather than
a dedicated seventh type), `executions.retry_count`, `approvals.execution_override_params`,
`audit_logs.execution_id` — all added in
`alembic/versions/0003_sprint3_trust_execution_schema.py`

Status

Accepted (user-approved, 2026-08-01)

Reason

The Database Architecture doc's schema for `cognitive_tasks`/`approvals`/
`executions`/`verifications`/`audit_logs` has no table or column to satisfy
the master engineering brief's mandatory retry, rollback, and merchant-
"Modify" requirements. All four additions are purely additive — no existing
column/table from the base doc's design is altered or removed — and were
approved directly by the user this session as necessary to satisfy
requirements the base schema doc itself could not support. See CONFLICTS.md
item 23.

---

## ADR-026

`approvals.approver_user_id` Has No Foreign Key Constraint

Bare nullable `UUID` column, no `REFERENCES users(id)`

Status

Accepted

Reason

No `users` table exists anywhere in this codebase — Sprint 1/2 never built
per-user auth, and `get_current_store_id` resolves only to a store, never a
user. The column is reserved for when user accounts land; until then,
merchant-initiated actions during the Sprint 3 lifecycle are attributed via
`audit_logs.actor_type = 'MERCHANT'` / `audit_logs.actor_id` instead. See
CONFLICTS.md item 24.

---

## ADR-027

Sprint 2 `DetectedIssueSchema` Touch-Up: `fix_check` Field

Optional `fix_check: str | None` field added to `DetectedIssueSchema`
(`domain/agents/product_quality.py`), populated verbatim (never invented)
from the originating inspection finding's `evidence.check` identifier in
both the LLM prompt path and the deterministic fallback path; persisted into
`issues.evidence_data["fix_check"]` by `services/workers/tasks/inspection.py`

Status

Accepted

Reason

Sprint 3's Plan step (`Agent.propose_action`) must deterministically map a
persisted `Issue` to one of this sprint's two auto-fixable action types.
String-matching LLM-generated title/description prose to decide "is this a
missing-ALT-text issue?" would be fragile and non-deterministic; `fix_check`
makes that match a plain dict lookup instead. Small, justified, additive
touch-up to Sprint 2 code — not a refactor, and issues without a `fix_check`
are unaffected. See CONFLICTS.md item 25.

---

## ADR-028

"Modify" Approval Action Is Not a New Enum Value

`ApprovalAction.APPROVE` with `execution_override_params` populated
represents "Modify"; no `MODIFY` value was added to `approval_status` or
`ApprovalAction`

Status

Accepted (user-approved)

Reason

The PRD's Approval Center UI describes editing a proposed fix's parameters
before approving it. Modeling this as a distinct status/enum value would
fork the approval state machine for what is semantically just an Approve
with edited parameters, which `approvals.execution_override_params` (ADR-025)
already supports. See CONFLICTS.md item 26.

---

## ADR-029

Sprint 3 Part 1 Scope: Two Concrete Auto-Fixable Action Types

Only `GENERATE_ALT_TEXT` (RiskLevel.LEVEL_1_SAFE) and
`REWRITE_PRODUCT_DESCRIPTION` (RiskLevel.LEVEL_2_MODERATE) are wired to
concrete `ProductQualityAgent.propose_action` fix paths this sprint. The
risk/approval engine (`domain/risk.py`) still handles LEVEL_3_HIGH/
LEVEL_4_CRITICAL generically (always requires approval, never auto-executed)
for future extensibility, but no action type maps to either level yet. No
discount agent, no other issue categories, this sprint.

Status

Accepted (deliberate, user-directed scope narrowing)

Reason

Keeps the AI Trust & Execution lifecycle's first implementation focused and
fully verifiable end-to-end against two real, well-understood fix types
rather than spreading effort thin across a wider action catalog the SATDD
gestures at but this sprint was not scoped to build. See CONFLICTS.md
item 27.

---

## ADR-030

Product Description Rollback Fidelity: Known Limitation

`REWRITE_PRODUCT_DESCRIPTION`'s `execution_plan.before_state.description_html`
is always `None`, and its `rollback` restores to an empty description, not
the merchant's true prior copy

Status

Accepted, with a documented limitation (not a target for silent future
"fix" without a deliberate follow-up decision)

Reason

Neither the `Issue` domain model nor `evidence_data` carries the actual
prior `descriptionHtml` text anywhere upstream this sprint (only
`word_count` is captured, via the `thin_description` finding's evidence, and
that value is not even threaded through the LLM path's `DetectedIssueSchema`
today). Building full before/after content capture was out of this
sprint's narrowed scope (ADR-029). Flagged explicitly in code
(`# LIMITATION:` comment in `_propose_description_fix`) and in the Sprint 3
Part 1 completion report so it is not silently forgotten before Part 2
(execution/rollback use cases) is built on top of it.

---

## ADR-031

Idempotency-Key Required, Full Replay-Cache Deferred

`POST /api/v1/approvals/{id}/action` and `POST /api/v1/tasks/{task_id}/rollback`
require an `Idempotency-Key` header; no Redis-backed replay-cache exists to
return an identical cached response on an exact request replay

Status

Accepted, with a documented limitation

Reason

No request-replay cache store exists anywhere in this codebase, and
building one was outside Part 2's approved scope. The header's presence is
still enforced (400/422 if missing), and true idempotency is provided
instead by the approval/task state-machine guards
(`ApprovalAlreadyDecidedProblem`, `TaskNotRollbackableProblem`) — sufficient
for the common "did I already submit this?" case, but not a guard against a
genuine concurrent double-send race. See CONFLICTS.md item 28.

---

## ADR-032

Synchronous, In-Process Auto-Execution Inside `tasks.plan_cognitive_tasks`

Level-1/auto-approved-Level-2 CognitiveTasks are executed and verified
synchronously, in-process, by directly awaiting `ExecuteCognitiveTask`/
`VerifyExecution` from within `PlanCognitiveTasks.execute()` — never via a
Celery dispatch for this path

Status

Accepted (per the master engineering brief for this session)

Reason

`tasks.compile_shift_report` fires immediately after planning, same as
Sprint 2, and the Morning Shift Report is immutable once persisted. Without
this decision, an auto-executed task's completion could race the report
snapshot and be silently missing from `completed_tasks[]` forever. Making
the auto-execute path synchronous guarantees every such task has reached a
terminal state before the report compiles. The reserved
`tasks.execute_cognitive_task`/`tasks.verify_execution` Celery tasks
(celery:execution/celery:verification) still exist as real, independently
dispatchable tasks — used only for the one genuinely asynchronous case:
merchant APPROVE on a task that required approval. See CONFLICTS.md item 29.

---

## ADR-033

Additive Repository/Protocol Methods Discovered While Implementing Part 2

Seven small, symmetric repository methods added beyond Part 1's explicit
spec (`SqlIssueRepository.get_by_id`, `SqlExecutionRepository.get_by_id`,
`SqlRollbackRepository.get_by_id`/`get_by_execution_id`,
`SqlCognitiveTaskRepository.update_execution_plan`,
`SqlApprovalRepository.list_for_shift`/`count_approvals_for_action_type`/
`extend_expiry`), each with a matching Protocol method and `InMemory*` fake

Status

Accepted

Reason

The master brief's own use-case method signatures (e.g. `VerifyExecution.execute(execution_id)`,
the merchant "Modify" approval flow, the Morning Shift Report's
`pending_approvals[]` section) required lookups/mutations Part 1's
repository surface didn't yet expose. Each addition follows the exact
pattern of an existing sibling method on the same repository — no existing
method was changed. See CONFLICTS.md item 30.

---

## ADR-034

`IssueRepository` Protocol Added (Was Missing Entirely in Part 1)

`application/ports.py` now defines an `IssueRepository` Protocol (plus
`InMemoryIssueRepository` fake) covering `SqlIssueRepository`'s existing
methods (`create`/`list_for_shift`/`list_for_store`/`update_status`) and
the new `get_by_id`

Status

Accepted

Reason

`SqlIssueRepository` existed since Sprint 2 but had no corresponding
Protocol — Sprint 2's worker tasks used the concrete SQL class directly,
which was fine when no use case needed to depend on the abstraction.
Sprint 3's use cases must stay framework-agnostic per this module's own
Clean Architecture rule, so a Protocol (and a real, join-capable in-memory
fake) was required now. See CONFLICTS.md item 31.

---

## ADR-035

Sprint 4 Step 1 — Agent Registry Keyed by `domain_category`; Demo Incident Generator Gated by Default-`False` Settings Flag

`PlanCognitiveTasks(product_quality_agent: Agent, agent_record_id: uuid.UUID)`
became `PlanCognitiveTasks(agents: dict[str, Agent], agent_record_ids:
dict[str, uuid.UUID])`, both keyed by `Agent.domain_category` (the same
string values as the existing `IssueCategory` enum / `agents.domain_category`
Postgres column). `POST /api/v1/demo/incidents/{scenario_id}` (the Demo
Incident Generator's first wired scenario, "Midnight Pricing Disaster") is
gated behind `Settings.demo_mode_enabled` (default `False`, env var
`DEMO_MODE_ENABLED`), returning 404 via `DemoModeDisabledProblem` when
disabled.

Status

Accepted

Reason

The agent registry key choice means every future specialist (Checkout,
Theme, Tracking) registers itself with zero new enum values or schema
changes — `IssueCategory` already has `CHECKOUT`/`DISCOUNT` entries from
Sprint 2's original 8-value enum. The demo-mode gate exists because the
Demo Incident Generator's entire purpose — letting an authenticated caller
deliberately corrupt their own store's live pricing data on cue — is a real
production liability if left unconditionally reachable; a 404 (not 403)
response when disabled keeps the endpoint indistinguishable from one that
doesn't exist. See CONFLICTS.md items 32-33.

---

## ADR-036

Sprint 4 Step 2 — Checkout Specialist Built as a Deterministic (Non-LLM) Agent; New Celery Task Inserted Into the Existing Inspection Chain

`CheckoutSpecialistAgent` (`domain_category = 'DISCOUNT'`) detects
overlapping/duplicate storewide discount codes via a pure structural rule
(`domain/discount_inspection.py`) — no LLM call at all, unlike
`ProductQualityAgent`. Its Plan step deactivates every duplicate (keeping
the oldest, by `createdAt`, as canonical) via `discountCodeDeactivate`,
fully reversible via `discountCodeActivate`. A new Celery task,
`tasks.inspect_discounts`, runs between `tasks.inspect_catalog` and
`tasks.plan_cognitive_tasks` on the same `celery:observation` queue. A new
migration (`0004_sprint4_step2_checkout_agent.py`) seeds the agent's
`agents` table row — the only schema-adjacent change this step required,
since `domain_category='DISCOUNT'` already existed as a valid
`issue_category` enum value from Sprint 2.

Status

Accepted

Reason

Detecting a duplicate/overlapping discount is a deterministic fact about
live store configuration, not a judgment call — an LLM call would add cost,
latency, and a new prompt to validate for no accuracy benefit, and would
work against the roadmap's own "zero new scope" framing for this slice
(`SPRINT4_AI_WORKFORCE_VISION.md`, Phase A). Reusing `DISCOUNT` (not
`CHECKOUT`) as the routing key keeps `CHECKOUT` free for a genuinely
broader future specialist. Revenue impact is Shopify's own real, reported
`totalSales` figure summed across the flagged codes — never a fabricated
projection. See CONFLICTS.md items 34-37 for the full write-up, including
the flagged risk around `codeDiscountNodes`'s deprecated-but-functional
status.

---

## ADR-037

Sprint 4 Step 3 — Theme Guardian's Restore Is a Guided Bundle, Never an Autonomous Shopify Write; Tracking Specialist Recreates Script Tags Fully Autonomously (Approval-Gated)

`ThemeGuardianAgent` (`domain_category = 'CHECKOUT'`, reused from Step 2's
own reservation) snapshots a fixed watch-list of critical theme files
(`sections/main-product.liquid` by default), detects checksum divergence
from baseline, and uses an LLM (same Anthropic client as Product Quality)
to explain the diff. Its Plan step (`GENERATE_THEME_RESTORE_GUIDE`,
`LEVEL_3_HIGH`, always human-in-the-loop) never calls a Shopify mutation —
`execute_cognitive_task.py`'s `"theme_restore_guide"` branch only builds a
guided-resolution bundle (baseline content + Theme Editor deep link).
Verification re-fetches the live file and passes once it matches baseline,
regardless of who/what applied the fix. Rollback for this action type is a
documented no-op (`{"mutation": "none"}`).

`TrackingSpecialistAgent` (`domain_category = 'PIXEL_TRACKING'`) snapshots
known script tags and detects removal via a pure structural rule
(`domain/script_tag_inspection.py`) — no LLM call. Its Plan step
(`RECREATE_TRACKING_SCRIPT_TAG`, `LEVEL_2_MODERATE`) recreates the tag via
`scriptTagCreate`, fully reversible via `scriptTagDelete`. Because Shopify
only assigns the new tag's id once creation actually succeeds,
`ExecuteCognitiveTask` patches the live id into the task's
`execution_plan["items"][i]["rollback"]` post-execution and persists it via
the existing `update_execution_plan` repository method (built for the
merchant "Modify" override in Sprint 3, reused here for a different
purpose) — the first action type this engagement where a rollback
mutation's parameter genuinely cannot be known at Plan time.

Demo Incident Generator Scenario 2 ("Rogue Developer Theme Break") is
wired, but only its tracking half (create-then-delete a Meta Pixel-pattern
script tag) actually executes — see CONFLICTS.md item 40 for why the
theme-corruption half cannot be auto-triggered at all.

New migration `0005_sprint4_step3_theme_tracking_schema.py` adds
`theme_snapshots`/`tracking_snapshots` (both RLS-protected) and seeds both
agent rows. New scope: `write_script_tags` (README updated). New Celery
tasks: `tasks.inspect_theme_files`, `tasks.inspect_tracking_scripts`,
inserted into the existing chain after `tasks.inspect_discounts` and before
`tasks.plan_cognitive_tasks`.

Status

Accepted

Reason

The restore-scope decision (guided bundle vs. autonomous write vs.
app-extension-only restore) was presented to the user as a structured,
locked decision point before any Step 3 code was written, per the standing
"stop and flag conflicts rather than guess" rule — Shopify's
`themeFilesUpsert`/legacy Asset-write exemption requirement (CONFLICTS.md
item 38) is a hard external blocker no amount of careful engineering can
route around within this engagement's timeline. The user selected the
guided-bundle approach, framing it explicitly as a deliberate Level-3
high-risk security guardrail rather than a compromise. Tracking
Specialist's fully-autonomous (approval-gated) design was possible because
no equivalent restriction exists for `scriptTagCreate`/`scriptTagDelete` —
confirmed via the same live-docs research rigor applied to every prior
mutation this engagement (Sprint 3's `productImageUpdate`, Step 2's
`codeDiscountNodes`, Step 3's own theme-file findings). See CONFLICTS.md
items 38-42 for the full write-up.

## ADR-038

Sprint 4 Step 4 — Chief Ops AI Orchestration + "Ask NightShift" + Executive Briefing (Phase B, one build, two surfaces)

Once 2+ specialist categories have findings in a completed shift,
`ChiefOpsSynthesizer` (`domain/chief_ops.py`) makes one structured-LLM call
(same `StructuredLlmClient` pattern as every other agent) to narrate the
correlated story across them and flag whether they plausibly describe the
same incident. Below that threshold, no LLM call is made at all —
`deterministic_briefing()` covers the 0- or 1-specialist case for free.
Output is the Vision doc's "Multi-Agent Handshake" shape: a list of
per-specialist turns (issue, agent name, a deterministically-assigned
🟢/⚡/🧠 icon, timestamp) plus a final `narrative`/`correlated` synthesis —
persisted as a new `chief_ops_briefing` key inside the existing
`shift_reports.report_json` JSONB column, not a new table (see CONFLICTS.md
item 44). `GET /api/v1/shifts/latest` now returns it verbatim, and the
frontend's new `ExecutiveBriefing` component renders it as the "polished
summary view" surface.

"Ask NightShift" (`POST /api/v1/ask`, `application/use_cases/ask_nightshift.py`)
is the same underlying idea from the other direction: a merchant's
free-text question, grounded in the store's own last 5 (configurable)
persisted `ShiftReport.report_json` rows via one more structured-LLM call,
with the model required to say a question is ungroundable rather than
invent an answer. Stateless — no conversation-history table. Both surfaces
share the exact Redis-backed `LlmCallBudgetGuard` daily-call ceiling
already established for Product Quality (ADR-024), and both fall back to a
deterministic, non-LLM answer/narrative on budget exhaustion or schema
validation failure — never leaving a merchant with a blank response.

Chief Ops AI is deliberately not an `Agent` subclass and has no `agents`
table row of its own — see CONFLICTS.md item 43 for why forcing it into
the existing per-issue-category registry would misrepresent what it does.

Status

Accepted

Reason

Matches the Vision doc's own framing of Phase B as "one build, two
surfaces" reusing the existing structured-LLM-call pattern with zero new
backend infrastructure beyond that one call. Reusing `report_json` instead
of a new table, and keeping Ask NightShift stateless, both follow this
engagement's standing preference for the smallest schema footprint that
satisfies the stated requirement — consistent with how Steps 1-3 avoided
inventing tables/columns beyond what each step's own scope demanded.

## ADR-039

Sprint 4 Step 5 — Shift Replay Scrubber + Multi-Agent Log Icon Rendering + Counterfactual ROI Widget

Almost entirely a frontend build over data every prior sprint already
persists, with one small, explicitly-flagged backend addition (CONFLICTS.md
item 45): the existing `GET /api/v1/work-log` had no way to scope to a
single shift, so a new `GET /api/v1/shifts/{shift_id}/replay` endpoint was
added (`AuditLogRepository.list_for_shift`, chronological ascending,
mirroring `get_for_task`'s existing pattern; same store-ownership 404 guard
as `GET /api/v1/tasks/{id}`). Both this endpoint and the existing Work Log
now carry a deterministic icon per entry (`app/domain/replay.py::
icon_for_action`) — a second, explicitly separate mapping from Step 4's
per-issue 🟢/⚡/🧠 (CONFLICTS.md item 46), since a per-audit-log-event
icon needs to represent outcomes (failure, a merchant's own reject/defer
decision, a demo trigger) that don't fit that first mapping's three
symbols.

`ShiftReplay` (`components/shift/ShiftReplay.tsx`) is a play/pause +
draggable-scrubber timeline over that endpoint's entries. `WorkLog.tsx`
gained the same icon next to each entry's action label.

`CounterfactualRoiWidget` (`components/shift/CounterfactualRoiWidget.tsx`)
renders "what if I wasn't here" cards for every issue resolved this shift,
using only real data: `revenue_impact_estimate` already on the issue, and
an exposure window from Checkout Specialist's real `duplicate_created_at`
(fetched via `GET /api/v1/tasks/{id}`, this endpoint's first frontend
caller) where available, else "detected and resolved within Shift #N" —
grounded in `completed_tasks[]`'s own documented same-shift synchronous
execution guarantee (CONFLICTS.md item 47), never a fabricated bleed rate.

No new migration, no new scopes.

## ADR-040

Sprint 5 Phase 1.3 — Chief Ops always-synthesize + LLM budget headroom

Per direct user instruction as part of the Sprint 5 "XPRIZE Polish"
roadmap, `should_synthesize()` (`domain/chief_ops.py`) now gates on 1+
specialist findings instead of 2+ distinct categories
(`MINIMUM_TURNS_FOR_SYNTHESIS = 1`, replacing
`MINIMUM_SPECIALIST_CATEGORIES_FOR_SYNTHESIS = 2`). A single specialist's
finding now gets a real LLM-written executive-memo sentence instead of a
deterministic template one; only a shift with zero findings still skips the
LLM entirely (CONFLICTS.md item 48). This is a deliberate reversal of Step
4's cost-conscious 2+-category gate, not a bug fix.

Because this raises the number of shifts that make an LLM call from "only
multi-specialist shifts" to "effectively every shift with any finding at
all," `LLM_MAX_CALLS_PER_DAY` is raised from 30 to 200 (`config.py`,
`.env`, `.env.example`) so a demo day running many on-demand shifts (Phase
4's Chaos Panel) doesn't hit the daily ceiling mid-demo. The budget guard
itself (`LlmCallBudgetGuard`, ADR-024) is unchanged — only its configured
ceiling moves.

The "AI narration unavailable" copy in `ExecutiveBriefing.tsx` and
`AskNightShift.tsx` is reworded (not removed outright): with the new gate,
`used_llm: false` is only reachable on a genuine in-shift LLM failure
(budget exceeded or a schema-invalid response), never on the "only one
specialist reported" case that previously produced it by design. The new
copy ("AI synthesis hit a temporary limit") reflects that it is now
strictly a failure-path message, not a routine one.

## ADR-041

Sprint 5 Phase 2 — Command Center header + Shift Replay scrubber + specialist avatar visual polish

Frontend-only visual layer over data every prior sprint already persists —
no new endpoints, no schema changes, no new backend logic.

`OperationsHeader.tsx` gained a row of bracketed `[ LABEL ]` status badges
below the existing title/domain row: `CHIEF OPS AI: ACTIVE/STANDBY` (bound
to whether the latest shift's `chief_ops_briefing` is actually non-null —
never a hardcoded "ACTIVE"), `N SPECIALISTS ON WATCH` (a fixed roster count
of 4, mirroring `domain/chief_ops.py::CATEGORY_DISPLAY_NAMES`), and
`SHIFT #N COMPLETE/IN PROGRESS` (bound to the same `latestShift.shift_number`
/`.status` already rendered elsewhere on the page; omitted entirely — never
"Shift #0" — until a first shift has actually run).

New shared module `lib/specialist-identity.ts` gives each of the four real
specialists a distinct avatar emoji (🛡️ Theme Guardian, 💳 Checkout
Specialist, 🎯 Tracking Specialist, 📦 Product Quality Employee) plus 🧠 for
Chief Ops itself. The `action_type -> specialist` mapping it uses is not a
new invention — it's a direct mirror of each backend `Agent`'s own
`action_type`/`domain_category` (`domain/risk.py::ACTION_RISK_LEVELS`,
`domain/agents/*.py`). An unrecognized `actor_id` (a merchant's own action,
or a demo-scenario trigger id) renders with no avatar rather than a guessed
one. This avatar is deliberately layered alongside, not merged into, the
two existing per-*event* icon systems (Chief Ops's ⚡/🧠/🟢 in
`ExecutiveBriefing.tsx`, and `icon_for_action`'s ✅/⚠️/🎬/etc. in
`WorkLog.tsx`/`ShiftReplay.tsx`) — see CONFLICTS.md item 46 for why those
two were kept separate in the first place; this is now a third, purely
presentational layer on top.

`ShiftReplay.tsx`'s `ReplayScrubber` timeline row is upgraded from a bare
icon-dot strip to horizontally-scrollable, clickable nodes each showing the
specialist avatar + status icon + a short time label, with the active node
scaling up and gaining a focus-ring glow as the shift is scrubbed or
autoplayed — same underlying `entries` data and play/pause/range-input
controls as Sprint 4 Step 5, no new data fetching.

## ADR-042

Sprint 5 Phase 3 — Counterfactual ROI banner reposition + Theme Guardian visual diff card

**3.1 (ROI banner):** `CounterfactualRoiWidget` moved from below the full
issue list to directly under `ShiftReportView`'s heading, and restyled per
card into an explicit Side A ("Without NightShift AI") / Side B ("With
NightShift AI") two-column layout, matching the roadmap's own worked
example. Side B's "resolved in N minutes" is real elapsed time between the
shift's own `started_at` and the task's real `verified_at`/`completed_at` —
deliberately worded "resolved within N minutes of this shift starting," not
"N minutes after detection," since no per-issue detection timestamp exists
this shift (`domain/chief_ops.py`'s own documented gap). No new data
fetching — same `useTaskDetails` call as before, just reshaped rendering.

**3.2 (Theme Guardian diff card):** Per the user's approved Option 1 spec
("honest text-diff, polished UI"), built `ThemeGuardianDiffCard.tsx` — a
dark-mode side-by-side Liquid diff (baseline vs. current), a header banner
carrying Theme Guardian's own real plain-English explanation, and the
existing guided-resolution action area (copy patch / open Theme Editor,
extracted into a shared `ThemeRestoreActions.tsx` so `WorkLog.tsx` and this
new card use the identical component rather than two copies). Mounted
inside `ApprovalCard.tsx` for `GENERATE_THEME_RESTORE_GUIDE` approvals —
scoped to the Approval Center only (not duplicated into the Shift Report's
issue list too), since that's the moment a merchant is actually deciding
whether to trust the finding.

Needed two additive `PendingApprovalDTO` fields (`description`,
`evidence_data`, both passed through from the `issue` object
`list_pending_approvals` already fetches) so the diff card has real content
before any approval decision — see CONFLICTS.md item 50. The diff itself
uses a new dependency-free LCS line-diff (`lib/line-diff.ts`), not the
backend's own coarse index-aligned line counter (that one only sizes an LLM
prompt/severity signal; this one has to be accurate enough to show a
merchant). The header banner's wording deviates from the literal approved
spec ("Gemini 1.5 Pro's structured analysis") for accuracy reasons — see
CONFLICTS.md item 49.

## ADR-043

Sprint 5 Phase 4 — Demo Incident Control Panel ("Chaos Panel") + Catalog SEO Collapse (Scenario 3) wiring

Three real additions, no new tables:

1. **`TaskDispatcher.dispatch_inspect_catalog`** — a new method on the
   existing Celery dispatch Protocol/class (mirrors
   `dispatch_store_discovery`/`dispatch_execute_cognitive_task` exactly),
   enqueuing the same `tasks.inspect_catalog` entry point the nightly
   scheduler and `scripts/trigger_shift.py` already use.
2. **`POST /api/v1/demo/incidents/{scenario_id}`** now calls that dispatcher
   right after a scenario successfully triggers, returning the dispatched
   Celery task id as `shift_dispatch_task_id` — one click both corrupts
   data and runs the background shift that detects/resolves it (CONFLICTS.md
   item 51 explains why this wasn't a separate endpoint).
3. **Scenario 3 (`catalog_seo_collapse`)** is wired: strips a flagship
   product's description and image ALT text using Sprint 3's own real
   `update_product_description`/`update_product_image_alt_text` mutations
   in reverse (CONFLICTS.md item 52), added to `WIRED_SCENARIO_IDS`.

Frontend: `components/shared/ChaosPanel.tsx` is a floating `[ ⚡ CHAOS PANEL ]`
button that expands into the three named scenario triggers. Gated on the
new `StoreSnapshotResponse.demo_mode_enabled` field (CONFLICTS.md item 53)
so it can never render promising a capability that would 404. After a
successful trigger, it invalidates the pending-approvals/latest-shift/
work-log/shift-replay react-query caches immediately and again every 4
seconds for up to 60 seconds — a deliberate, narrowly-scoped exception to
the rest of the app's "refetch on demand, not a live feed" posture, since
the merchant just personally kicked off the exact background job they're
waiting to see finish.

No new migration, no new endpoints beyond the additive fields/method above.

## ADR-045

Sprint 5 Phase 5 — "Killer feature" pass: Tonight's Impact, Employee Notebook, Merchant Memory, Root Cause framing

Four scoped features from the user's own detailed final-pass brief (a fifth,
cross-shift learning/pattern recognition, was explicitly deferred — the
user's own selection, not this pass's judgment call):

1. **Tonight's Impact widget** (`TonightsImpactWidget.tsx`, top of the
   Morning Shift Report) — four grounded metrics only, no fabricated
   conversion-rate estimate: Issues Fixed Overnight
   (`metrics.issues_resolved`), Revenue Protected
   (`metrics.estimated_revenue_protected_usd`), Store Health Delta (needed
   the new additive `previous_shift_health_score` field, CONFLICTS.md item
   55), and Merchant Actions Status (CONFLICTS.md item 56 on why it says
   "Fixes Verified" rather than "Autonomous Fixes Verified").
2. **Work Log "Employee Notebook" restyle** — `WorkLogStageTrack` renders a
   five-stage Observed → Reasoned → Proposed → Executed → Verified pill
   track per entry. `TASK_PLANNED` (one real audit-log event) lights all
   three of Observed/Reasoned/Proposed at once, honestly reflecting that
   this deterministic pipeline produces one rationale covering all three
   conceptually — never fabricated as three separately-timestamped events.
   Merchant decisions (`APPROVAL_GRANTED`/`REJECTED`/`DEFERRED`) and
   `ROLLBACK_COMPLETED` render as plain badges instead, since they don't sit
   on this five-stage AI lifecycle track.
3. **Merchant Memory surfaced in the UI** — `domain/confidence.py` gained
   `merchant_memory_note(assessment)`, extracting the existing
   `merchant_acceptance_history` signal's own `reasoning` sentence verbatim
   whenever it reflects real prior history (never the neutral no-history
   prior, never a newly-composed phrase). `compute_confidence` also gained
   an optional `merchant_approval_count` param so that sentence can say
   "Based on N previous approvals..." — enriches wording only, never the
   score. Threaded through to both the live Approval Center
   (`PendingApprovalDTO.merchant_memory_note`) and the persisted Chief Ops
   turns (`SpecialistTurn.merchant_memory_note`, built in
   `workers/tasks/shift_report.py` from the same `confidence_assessment`
   blobs already being read there), rendered as a "🧠 Merchant Preference
   Applied" callout in both `ApprovalCard.tsx` and `ExecutiveBriefing.tsx`.
4. **Root Cause correlation framing** — `chief_ops.py`'s `_PROMPT_TEMPLATE`
   now instructs the LLM to prefix a `correlated: true` narrative with the
   exact string "Root Cause: " followed by the one connecting-cause
   sentence, and to never use those words when `correlated` is false. Purely
   a prompt-wording change — `correlated` was already a real, LLM-decided
   boolean; this only shapes how a true value gets narrated. Frontend badge
   in `ExecutiveBriefing.tsx` changed from "Correlated incident" to "🔗 Root
   Cause Identified", reinforcing the same backend flag, not a new claim.

No schema/migration changes. All four features reuse data this codebase
already computes; the only genuinely new backend surface is the additive
`previous_shift_health_score` field and the `merchant_approval_count`
optional parameter — both purely additive, non-breaking.

Status

Accepted

Reason

Every one of the user's five original feature ideas was evaluated against
this project's standing "never invent data not present in context"
principle before implementation began. Two ideas were caught and corrected
before any code was written: a fabricated conversion-rate metric (dropped
entirely, per the user's own "Drop it, keep the real 4 metrics" choice) and
a fabricated third correlated signal for the Root Cause example (the
worked example in this ADR names only the two specialists — Theme Guardian
and Tracking Specialist — that a real shift can actually produce, not the
user's original three-specialist illustration). The fifth idea,
cross-shift pattern recognition, was deferred rather than rushed, since it
is the only one of the five that would require new persistence (comparing
across shifts beyond the two already exposed here) rather than reframing
data already computed this shift.

## ADR-044

Sprint 5 Phase 5 — Final micro-polish pass (user-reported)

Three small, targeted fixes from direct user feedback on the live UI:

1. **Explanation text cross-contamination bug (CONFLICTS.md item 54)** —
   `domain/risk.py` gained `ACTION_TYPE_REASONING`, one sentence per action
   type, so `assess_risk_level` (and therefore every `TASK_PLANNED`/
   `APPROVAL_REQUESTED` audit log rationale rendered in Work Log and Shift
   Replay) never again shows another, unrelated action type's explanation.
   `RISK_LEVEL_REASONING` remains only as a short, genuinely generic
   per-level fallback for action types with no specific entry.
2. **Dashboard tile specialist icons** — `CategoryHealthTile` gained an
   optional `icon` prop; the dashboard's three health tiles now show
   💳 Checkout (Discounts & Pricing), 📦 Catalog Quality, and 🛡️ Theme &
   Storefront (the last one newly added — `CHECKOUT`/Theme Guardian's real
   category was already tracked in `health_category_deductions` and shown
   in the Shift Report's Health Score Breakdown widget, just never given
   its own dashboard tile until now). Icons reuse the same avatar emoji as
   `lib/specialist-identity.ts` (Sprint 5 Phase 2.3) — one consistent
   specialist-identity system across the whole app, not a fourth one-off.
3. **Counterfactual ROI banner visual weight** — wrapped in a distinct
   indigo-tinted, double-bordered container (vs. the plain white cards used
   everywhere else in the report), with a prominent total "Revenue
   Protected" figure (the real sum of every resolved issue's own
   `revenue_impact_estimate`, already rendered per-card — just also totaled
   and surfaced at the top) next to the heading, so it's the first thing a
   judge's eye lands on scrolling through the report.

No backend schema changes beyond the risk-reasoning text fix (a pure text/
logic change, no new columns or migrations).

Status

Accepted

Reason

The Vision doc's own Phase C framing ("frontend timeline component over
data that already exists, not a backend build") was correct about the data
but not quite about the API surface — the smallest possible addition
(one shift-scoped read endpoint, reusing the existing repository/DTO
patterns) was made rather than either forcing the feature into the
existing global Work Log endpoint (would have conflated two different UI
needs) or skipping shift-scoping entirely (would have made "replay" show
every shift's history at once, defeating the feature's own premise). The
Counterfactual ROI Widget's grounding decisions mirror the same
"never invent data not present in context" discipline this codebase has
held since Sprint 2 (CONFLICTS.md item 35's sibling reasoning).

## ADR-046

Context

ADR-023 recorded switching the default per-specialist LLM provider to
Anthropic (Claude Haiku 4.5) to spend the user's own metered Anthropic
credits during cost-conscious local testing. The hackathon submission
requires the project to run entirely on Gemini via Google Cloud/Vertex
AI, and the user separately asked for every Anthropic reference to be
removed from the repo ahead of their one-time final commit.

Decision

Anthropic support is fully removed: `AnthropicClient` and its dedicated
test file are deleted, `infrastructure/llm/factory.py` accepts only
`GEMINI` as a valid provider value (raising `ValueError` otherwise), and
every config surface (`.env.example`, `deploy/secrets-setup.md`, the
Sprint 2/4 migration seed rows for `agents.model_provider`/`model_name`)
now defaults to Gemini. See CONFLICTS.md item 57.

Status

Accepted

Reason

Gemini was already the load-bearing provider for Chief Ops AI's Executive
Briefing (ADR-023 itself never touched that call site); consolidating the
per-specialist detection agents onto the same provider removes an entire
second SDK dependency and its own credential/config surface, which is
strictly simpler once cost-conscious multi-provider testing is no longer
a goal. ADR-023's own reasoning is left unedited above as the historical
record of why the Anthropic branch existed.
