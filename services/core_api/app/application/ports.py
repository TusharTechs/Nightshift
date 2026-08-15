"""Repository/service ports (Protocols) that use cases depend on.

Defining these as structural Protocols — rather than importing the concrete
SQLAlchemy repositories directly — is what lets application/use_cases stay
framework-agnostic and lets tests substitute in-memory fakes without a
database. This satisfies the Clean/Hexagonal layering rule: application/ may
depend on abstractions, infrastructure/ provides the implementations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.domain.models import (
    Approval,
    AuditLogEntry,
    CognitiveTask,
    Execution,
    Issue,
    Organization,
    Rollback,
    Shift,
    ShiftReport,
    Store,
    StoreToken,
    Subscription,
    ThemeSnapshotRecord,
    TrackingSnapshotRecord,
    Verification,
)


class OrganizationRepository(Protocol):
    async def get_by_slug(self, slug: str) -> Organization | None: ...
    async def create(self, *, name: str, slug: str, billing_email: str) -> Organization: ...


class StoreRepository(Protocol):
    async def get_by_shopify_domain(self, shopify_domain: str) -> Store | None: ...
    async def get_by_id(self, store_id: uuid.UUID) -> Store | None: ...
    async def upsert_from_installation(
        self,
        *,
        organization_id: uuid.UUID,
        shopify_domain: str,
        myshopify_domain: str,
        store_name: str,
        currency_code: str,
        iana_timezone: str,
    ) -> Store: ...
    async def update_health_score(self, store_id: uuid.UUID, health_score: int) -> None: ...
    async def list_active(self) -> list[Store]:
        """Sprint 4: every `is_active` store — the nightly scheduler's fan-out
        list. Excludes stores that uninstalled (see `upsert_from_installation`'s
        reinstall handling, which is the only other place `is_active` is
        touched)."""
        ...
    async def deactivate(self, store_id: uuid.UUID) -> None:
        """Shopify Compliance: `app/uninstalled` webhook handler's own write —
        marks a Store `is_active = False` so `list_active`'s nightly-scheduler
        fan-out (and every other `is_active`-gated code path) stops acting on
        a store the merchant has uninstalled. Idempotent: safe to call on an
        already-inactive store (e.g. a duplicate/retried webhook delivery)."""
        ...


class StoreTokenRepository(Protocol):
    async def upsert(
        self, *, store_id: uuid.UUID, access_token_encrypted: str, scopes: list[str]
    ) -> StoreToken: ...
    async def get_by_store_id(self, store_id: uuid.UUID) -> StoreToken | None: ...


class TaskDispatcher(Protocol):
    def dispatch_store_discovery(self, store_id: uuid.UUID) -> str:
        """Enqueue tasks.store_discovery onto celery:observation; returns task id."""
        ...

    def dispatch_execute_cognitive_task(self, task_id: uuid.UUID) -> str:
        """Sprint 3 Part 2: enqueue tasks.execute_cognitive_task onto
        celery:execution — the ONE truly async execution path, used only
        when `HandleApprovalAction` grants an APPROVE decision. The
        synchronous auto-execute path inside `tasks.plan_cognitive_tasks`
        never goes through this — it calls `ExecuteCognitiveTask.execute()`
        directly, in-process. Returns the dispatched Celery task id."""
        ...

    def dispatch_inspect_catalog(self, store_id: uuid.UUID) -> str:
        """Sprint 5 Phase 4: enqueue tasks.inspect_catalog onto
        celery:observation — the same entry point the nightly scheduler
        (`tasks.dispatch_nightly_shifts`) and `scripts/trigger_shift.py`
        already use, now also reachable from the Demo Incident Generator so
        a Chaos Panel click both corrupts data AND runs the background
        shift that detects/resolves it, without a human needing to wait for
        the nightly cadence or run the script by hand. Returns the
        dispatched Celery task id."""
        ...

    def dispatch_nightly_shifts(self) -> str:
        """Cloud Run migration: enqueue tasks.dispatch_nightly_shifts onto
        celery:cron — the same task Celery Beat used to fire on its own
        schedule. Cloud Run has no persistent-process analog of Beat, so
        `POST /internal/dispatch-nightly-shifts` (called by Cloud Scheduler)
        calls this instead. Returns the dispatched Celery task id."""
        ...


class ShiftRepository(Protocol):
    async def get_by_id(self, shift_id: uuid.UUID) -> Shift | None: ...
    async def get_latest_completed(self, store_id: uuid.UUID) -> Shift | None: ...

    async def increment_pending_approvals(self, shift_id: uuid.UUID, delta: int) -> None:
        """Sprint 3 Part 2."""
        ...

    async def increment_resolved_count(self, shift_id: uuid.UUID, delta: int = 1) -> None:
        """Sprint 3 Part 2."""
        ...


class ShiftReportRepository(Protocol):
    async def get_by_shift_id(self, shift_id: uuid.UUID) -> ShiftReport | None: ...
    async def get_latest_for_store(self, store_id: uuid.UUID) -> ShiftReport | None: ...

    async def list_recent_for_store(self, store_id: uuid.UUID, *, limit: int) -> list[ShiftReport]:
        """Sprint 4 Step 4: "Ask NightShift" grounds every answer in real,
        already-persisted shift reports — never in freshly recomputed or
        invented data — so it needs more than just the single latest report
        `get_latest_for_store` returns. Newest-first, capped at `limit` so a
        long-lived store's history never balloons a single LLM prompt."""
        ...


class IssueRepository(Protocol):
    """Sprint 3 Part 2: no Protocol existed for `SqlIssueRepository` in
    Part 1 — added now so the Plan/Execute/Verify use cases can depend on
    the abstraction rather than the concrete SQL class, per this module's
    own Clean Architecture rule."""

    async def create(
        self,
        *,
        store_id: uuid.UUID,
        shift_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        category: str,
        severity: str,
        title: str,
        description: str,
        evidence_data: dict,
        affected_resources: list[str],
        revenue_impact_estimate: float,
        confidence_score: float,
    ) -> Issue: ...
    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Issue]: ...
    async def list_for_store(self, store_id: uuid.UUID) -> list[Issue]: ...
    async def update_status(self, issue_id: uuid.UUID, status: str) -> None: ...
    async def get_by_id(self, issue_id: uuid.UUID) -> Issue | None: ...
    async def get_open_by_dedup_key(self, store_id: uuid.UUID, dedup_key: str) -> Issue | None:
        """Sprint 4: finds an existing OPEN/AWAITING_APPROVAL issue carrying
        this exact `evidence_data["dedup_key"]` for this store, so a
        detector can reuse it instead of creating a new Issue (and thus a
        new duplicate pending approval) every time an already-flagged,
        still-unfixed problem gets re-observed on a later shift — which,
        with a real recurring scheduler now running, is the normal case for
        anything the merchant hasn't acted on yet, not an edge case."""
        ...


# --- Sprint 3: AI Trust & Execution -----------------------------------------


class CognitiveTaskRepository(Protocol):
    async def create(
        self,
        *,
        issue_id: uuid.UUID,
        store_id: uuid.UUID,
        shift_id: uuid.UUID,
        agent_id: uuid.UUID,
        action_type: str,
        execution_plan: dict,
        risk_level: str,
        risk_reasoning: str,
        idempotency_key: str,
        status: str = "PLANNED",
        confidence_assessment: dict | None = None,
        explanation: dict | None = None,
    ) -> CognitiveTask: ...
    async def get_by_id(self, task_id: uuid.UUID) -> CognitiveTask | None: ...
    async def get_by_idempotency_key(self, key: str) -> CognitiveTask | None: ...
    async def update_status(self, task_id: uuid.UUID, status: str) -> None: ...
    async def update_confidence_and_explanation(
        self, task_id: uuid.UUID, *, confidence_assessment: dict, explanation: dict
    ) -> None: ...
    async def update_execution_plan(self, task_id: uuid.UUID, execution_plan: dict) -> None: ...
    async def list_for_shift(self, shift_id: uuid.UUID) -> list[CognitiveTask]: ...


class ApprovalRepository(Protocol):
    async def create(
        self,
        *,
        task_id: uuid.UUID,
        issue_id: uuid.UUID,
        store_id: uuid.UUID,
        expires_at: datetime | None = None,
    ) -> Approval: ...
    async def get_by_id(self, approval_id: uuid.UUID) -> Approval | None: ...
    async def get_by_task_id(self, task_id: uuid.UUID) -> Approval | None: ...
    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        status: str,
        decided_at: datetime,
        approver_user_id: uuid.UUID | None = None,
        merchant_rationale: str | None = None,
        execution_override_params: dict | None = None,
    ) -> None: ...
    async def count_rejections_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int: ...
    async def count_approvals_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int: ...
    async def list_pending_for_store(self, store_id: uuid.UUID) -> list[Approval]: ...
    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Approval]: ...
    async def extend_expiry(self, approval_id: uuid.UUID, new_expires_at: datetime) -> None: ...


class ExecutionRepository(Protocol):
    async def create(
        self, *, task_id: uuid.UUID, store_id: uuid.UUID, request_payload: dict
    ) -> Execution: ...
    async def get_by_task_id(self, task_id: uuid.UUID) -> Execution | None: ...
    async def get_by_id(self, execution_id: uuid.UUID) -> Execution | None: ...
    async def mark_completed(
        self, execution_id: uuid.UUID, *, response_payload: dict, execution_duration_ms: int
    ) -> None: ...
    async def mark_failed(
        self, execution_id: uuid.UUID, *, error_log: str, execution_duration_ms: int
    ) -> None: ...
    async def mark_rolled_back(self, execution_id: uuid.UUID) -> None: ...
    async def increment_retry_count(self, execution_id: uuid.UUID) -> None: ...


class VerificationRepository(Protocol):
    async def create(
        self,
        *,
        execution_id: uuid.UUID,
        task_id: uuid.UUID,
        store_id: uuid.UUID,
        status: str,
        method: str,
        result_data: dict,
    ) -> Verification: ...
    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Verification | None: ...


class RollbackRepository(Protocol):
    async def create(
        self,
        *,
        execution_id: uuid.UUID,
        task_id: uuid.UUID,
        store_id: uuid.UUID,
        reverted_state: dict,
        rollback_reason: str,
    ) -> Rollback: ...
    async def get_by_id(self, rollback_id: uuid.UUID) -> Rollback | None: ...
    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Rollback | None: ...
    async def mark_completed(self, rollback_id: uuid.UUID) -> None: ...
    async def mark_failed(self, rollback_id: uuid.UUID, *, error_log: str) -> None: ...


class AuditLogRepository(Protocol):
    async def append(
        self,
        *,
        store_id: uuid.UUID,
        actor_type: str,
        actor_id: str,
        action: str,
        rationale: str,
        shift_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        execution_id: uuid.UUID | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        model_identifier: str | None = None,
        prompt_version: str | None = None,
    ) -> AuditLogEntry: ...
    async def list_for_store(
        self, store_id: uuid.UUID, *, limit: int = 20, before_timestamp: datetime | None = None
    ) -> list[AuditLogEntry]: ...
    async def get_for_task(self, task_id: uuid.UUID) -> list[AuditLogEntry]: ...

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[AuditLogEntry]:
        """Sprint 4 Step 5: Shift Replay's own data need — every audit log
        entry for one specific shift, chronological ascending (mirrors
        `get_for_task`'s existing ordering). See CONFLICTS.md item 45."""
        ...

    async def get_latest_shift_id_with_activity(self, store_id: uuid.UUID) -> uuid.UUID | None:
        """Sprint 5 Phase 1.2 fix: the shift_id of this store's single most
        recent audit log entry (by timestamp) that has a non-null shift_id —
        i.e. the most recent shift with ANY real activity, found via one
        direct query rather than walking a capped list of recent shifts.
        Returns None only if this store has never had a single shift-scoped
        audit log entry."""
        ...


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist ------------------


class ThemeSnapshotRepository(Protocol):
    async def get_by_filename(
        self, store_id: uuid.UUID, theme_id: str, filename: str
    ) -> ThemeSnapshotRecord | None: ...

    async def create_baseline(
        self, *, store_id: uuid.UUID, theme_id: str, filename: str, content: str, checksum_md5: str
    ) -> ThemeSnapshotRecord:
        """Only ever called when `get_by_filename` returns None — baseline
        capture is insert-only, never an upsert (see the Step 3 migration's
        own docstring on why an existing baseline is never overwritten)."""
        ...


class TrackingSnapshotRepository(Protocol):
    async def list_for_store(self, store_id: uuid.UUID) -> list[TrackingSnapshotRecord]: ...

    async def create(
        self, *, store_id: uuid.UUID, src: str, display_scope: str | None, pattern_name: str | None
    ) -> TrackingSnapshotRecord:
        """Idempotent upsert on `(store_id, src)` — safe to call whether or
        not this exact snapshot already exists (the real detection pipeline
        only ever calls this for a genuinely-new `src`, but
        `TriggerDemoIncident`'s Rogue Developer Theme Break scenario is
        meant to be re-triggerable for repeated demos, and a second trigger
        against an already-snapshotted `src` must succeed, not crash). Was
        previously insert-only, matching `ThemeSnapshotRepository
        .create_baseline`'s convention — that assumption doesn't hold for a
        deliberately-repeatable demo trigger."""
        ...


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class SubscriptionRepository(Protocol):
    async def create(
        self,
        *,
        store_id: uuid.UUID,
        plan: str,
        status: str,
        shopify_charge_gid: str | None = None,
        monthly_price_usd: float = 0.0,
    ) -> Subscription: ...

    async def get_by_id(self, subscription_id: uuid.UUID) -> Subscription | None: ...

    async def get_by_store_and_charge_gid(
        self, store_id: uuid.UUID, shopify_charge_gid: str
    ) -> Subscription | None:
        """`GET /api/v1/billing/confirm`'s own tenant-isolation check: looked
        up by BOTH `store_id` (the returnUrl's own query param) AND
        `shopify_charge_gid` (Shopify's own `charge_id` redirect param,
        converted to a GID) together -- a charge_id that exists but belongs
        to a DIFFERENT store's subscription row returns None here exactly
        like a charge_id that doesn't exist at all, never leaking which case
        it is."""
        ...

    async def get_current_for_store(self, store_id: uuid.UUID) -> Subscription | None:
        """This store's most-recently-created subscription row -- see
        `alembic/versions/0006_billing_subscriptions.py`'s own docstring for
        why "current" means "latest row" rather than a mutable single-row
        design. Every store has at least one row from install time onward
        (`CompleteOAuthInstallation`'s own FREE-tier default), so this
        returns None only for a store this use case has never been called
        for (e.g. a pre-existing test fixture that didn't seed one)."""
        ...

    async def update_status(
        self,
        subscription_id: uuid.UUID,
        *,
        status: str,
        activated_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> None: ...


# --- In-memory fakes used by unit/integration tests -------------------------


class InMemoryOrganizationRepository:
    def __init__(self) -> None:
        self._by_slug: dict[str, Organization] = {}

    async def get_by_slug(self, slug: str) -> Organization | None:
        return self._by_slug.get(slug)

    async def create(self, *, name: str, slug: str, billing_email: str) -> Organization:
        now = datetime.now(timezone.utc)
        org = Organization(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
            billing_email=billing_email,
            created_at=now,
            updated_at=now,
        )
        self._by_slug[slug] = org
        return org


class InMemoryStoreRepository:
    def __init__(self) -> None:
        self._by_domain: dict[str, Store] = {}
        self._by_id: dict[uuid.UUID, Store] = {}

    def seed(self, store: Store) -> None:
        """Test-only helper for pre-populating a store before exercising a
        use case or endpoint against this fake."""
        self._by_domain[store.shopify_domain] = store
        self._by_id[store.id] = store

    async def get_by_shopify_domain(self, shopify_domain: str) -> Store | None:
        return self._by_domain.get(shopify_domain)

    async def get_by_id(self, store_id: uuid.UUID) -> Store | None:
        return self._by_id.get(store_id)

    async def list_active(self) -> list[Store]:
        return [store for store in self._by_id.values() if store.is_active]

    async def upsert_from_installation(
        self,
        *,
        organization_id: uuid.UUID,
        shopify_domain: str,
        myshopify_domain: str,
        store_name: str,
        currency_code: str,
        iana_timezone: str,
    ) -> Store:
        existing = self._by_domain.get(shopify_domain)
        now = datetime.now(timezone.utc)
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "store_name": store_name,
                    "currency_code": currency_code,
                    "iana_timezone": iana_timezone,
                    "is_active": True,
                    "updated_at": now,
                }
            )
            self._by_domain[shopify_domain] = updated
            self._by_id[updated.id] = updated
            return updated

        store = Store(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shopify_domain=shopify_domain,
            myshopify_domain=myshopify_domain,
            store_name=store_name,
            currency_code=currency_code,
            iana_timezone=iana_timezone,
            created_at=now,
            updated_at=now,
        )
        self._by_domain[shopify_domain] = store
        self._by_id[store.id] = store
        return store

    async def update_health_score(self, store_id: uuid.UUID, health_score: int) -> None:
        store = self._by_id.get(store_id)
        if store is not None:
            updated = store.model_copy(update={"health_score": health_score})
            self._by_id[store_id] = updated
            self._by_domain[updated.shopify_domain] = updated

    async def deactivate(self, store_id: uuid.UUID) -> None:
        store = self._by_id.get(store_id)
        if store is not None:
            updated = store.model_copy(update={"is_active": False})
            self._by_id[store_id] = updated
            self._by_domain[updated.shopify_domain] = updated


class InMemoryStoreTokenRepository:
    def __init__(self) -> None:
        self._by_store_id: dict[uuid.UUID, StoreToken] = {}

    async def upsert(
        self, *, store_id: uuid.UUID, access_token_encrypted: str, scopes: list[str]
    ) -> StoreToken:
        now = datetime.now(timezone.utc)
        token = StoreToken(
            id=uuid.uuid4(),
            store_id=store_id,
            access_token_encrypted=access_token_encrypted,
            scopes=scopes,
            created_at=now,
            updated_at=now,
        )
        self._by_store_id[store_id] = token
        return token

    async def get_by_store_id(self, store_id: uuid.UUID) -> StoreToken | None:
        return self._by_store_id.get(store_id)


class InMemoryTaskDispatcher:
    def __init__(self) -> None:
        self.dispatched_store_ids: list[uuid.UUID] = []
        self.dispatched_execute_task_ids: list[uuid.UUID] = []
        self.dispatched_inspect_catalog_store_ids: list[uuid.UUID] = []
        self.dispatch_nightly_shifts_call_count: int = 0

    def dispatch_store_discovery(self, store_id: uuid.UUID) -> str:
        self.dispatched_store_ids.append(store_id)
        return f"fake-task-{store_id}"

    def dispatch_execute_cognitive_task(self, task_id: uuid.UUID) -> str:
        self.dispatched_execute_task_ids.append(task_id)
        return f"fake-execute-task-{task_id}"

    def dispatch_inspect_catalog(self, store_id: uuid.UUID) -> str:
        """Sprint 5 Phase 4: kept in its own list, separate from
        `dispatched_store_ids` (which specifically means "store_discovery
        was dispatched") — a distinct dispatch type deserves a distinct
        record, same reasoning as `dispatched_execute_task_ids`."""
        self.dispatched_inspect_catalog_store_ids.append(store_id)
        return f"fake-inspect-catalog-task-{store_id}"

    def dispatch_nightly_shifts(self) -> str:
        self.dispatch_nightly_shifts_call_count += 1
        return f"fake-dispatch-nightly-shifts-{self.dispatch_nightly_shifts_call_count}"


class InMemoryShiftRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Shift] = {}

    def seed(self, shift: Shift) -> None:
        self._by_id[shift.id] = shift

    async def get_by_id(self, shift_id: uuid.UUID) -> Shift | None:
        return self._by_id.get(shift_id)

    async def get_latest_completed(self, store_id: uuid.UUID) -> Shift | None:
        completed = [
            s for s in self._by_id.values() if s.store_id == store_id and s.status == "COMPLETED"
        ]
        if not completed:
            return None
        return max(completed, key=lambda s: s.shift_number)

    async def increment_pending_approvals(self, shift_id: uuid.UUID, delta: int) -> None:
        shift = self._by_id.get(shift_id)
        if shift is not None:
            updated = shift.model_copy(
                update={"pending_approvals_count": max(0, shift.pending_approvals_count + delta)}
            )
            self._by_id[shift_id] = updated

    async def increment_resolved_count(self, shift_id: uuid.UUID, delta: int = 1) -> None:
        shift = self._by_id.get(shift_id)
        if shift is not None:
            updated = shift.model_copy(
                update={"issues_resolved_count": max(0, shift.issues_resolved_count + delta)}
            )
            self._by_id[shift_id] = updated


class InMemoryShiftReportRepository:
    def __init__(self) -> None:
        self._by_shift_id: dict[uuid.UUID, ShiftReport] = {}

    def seed(self, report: ShiftReport) -> None:
        self._by_shift_id[report.shift_id] = report

    async def get_by_shift_id(self, shift_id: uuid.UUID) -> ShiftReport | None:
        return self._by_shift_id.get(shift_id)

    async def get_latest_for_store(self, store_id: uuid.UUID) -> ShiftReport | None:
        matches = [r for r in self._by_shift_id.values() if r.store_id == store_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r.published_at)

    async def list_recent_for_store(self, store_id: uuid.UUID, *, limit: int) -> list[ShiftReport]:
        matches = [r for r in self._by_shift_id.values() if r.store_id == store_id]
        matches.sort(key=lambda r: r.published_at, reverse=True)
        return matches[:limit]


# --- Sprint 3: AI Trust & Execution — in-memory fakes -----------------------


class InMemoryIssueRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Issue] = {}

    def seed(self, issue: Issue) -> None:
        self._by_id[issue.id] = issue

    async def create(
        self,
        *,
        store_id: uuid.UUID,
        shift_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        category: str,
        severity: str,
        title: str,
        description: str,
        evidence_data: dict,
        affected_resources: list[str],
        revenue_impact_estimate: float,
        confidence_score: float,
    ) -> Issue:
        now = datetime.now(timezone.utc)
        issue = Issue(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=shift_id,
            agent_id=agent_id,
            category=category,
            severity=severity,
            status="OPEN",
            title=title,
            description=description,
            evidence_data=evidence_data,
            affected_resources=affected_resources,
            revenue_impact_estimate=revenue_impact_estimate,
            confidence_score=confidence_score,
            created_at=now,
            updated_at=now,
        )
        self._by_id[issue.id] = issue
        return issue

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Issue]:
        matches = [i for i in self._by_id.values() if i.shift_id == shift_id]
        return sorted(matches, key=lambda i: i.revenue_impact_estimate, reverse=True)

    async def list_for_store(self, store_id: uuid.UUID) -> list[Issue]:
        matches = [i for i in self._by_id.values() if i.store_id == store_id]
        return sorted(matches, key=lambda i: i.created_at, reverse=True)

    async def update_status(self, issue_id: uuid.UUID, status: str) -> None:
        issue = self._by_id.get(issue_id)
        if issue is not None:
            self._by_id[issue_id] = issue.model_copy(update={"status": status})

    async def get_by_id(self, issue_id: uuid.UUID) -> Issue | None:
        return self._by_id.get(issue_id)

    async def get_open_by_dedup_key(self, store_id: uuid.UUID, dedup_key: str) -> Issue | None:
        for issue in self._by_id.values():
            if (
                issue.store_id == store_id
                and issue.status in ("OPEN", "AWAITING_APPROVAL")
                and issue.evidence_data.get("dedup_key") == dedup_key
            ):
                return issue
        return None


class InMemoryCognitiveTaskRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, CognitiveTask] = {}
        self._by_idempotency_key: dict[str, uuid.UUID] = {}

    def seed(self, task: CognitiveTask) -> None:
        self._by_id[task.id] = task
        self._by_idempotency_key[task.idempotency_key] = task.id

    async def create(
        self,
        *,
        issue_id: uuid.UUID,
        store_id: uuid.UUID,
        shift_id: uuid.UUID,
        agent_id: uuid.UUID,
        action_type: str,
        execution_plan: dict,
        risk_level: str,
        risk_reasoning: str,
        idempotency_key: str,
        status: str = "PLANNED",
        confidence_assessment: dict | None = None,
        explanation: dict | None = None,
    ) -> CognitiveTask:
        now = datetime.now(timezone.utc)
        task = CognitiveTask(
            id=uuid.uuid4(),
            issue_id=issue_id,
            store_id=store_id,
            shift_id=shift_id,
            agent_id=agent_id,
            action_type=action_type,
            execution_plan=execution_plan,
            risk_level=risk_level,
            risk_reasoning=risk_reasoning,
            status=status,
            idempotency_key=idempotency_key,
            confidence_assessment=confidence_assessment or {},
            explanation=explanation or {},
            created_at=now,
            updated_at=now,
        )
        self._by_id[task.id] = task
        self._by_idempotency_key[idempotency_key] = task.id
        return task

    async def get_by_id(self, task_id: uuid.UUID) -> CognitiveTask | None:
        return self._by_id.get(task_id)

    async def get_by_idempotency_key(self, key: str) -> CognitiveTask | None:
        task_id = self._by_idempotency_key.get(key)
        return self._by_id.get(task_id) if task_id else None

    async def update_status(self, task_id: uuid.UUID, status: str) -> None:
        task = self._by_id.get(task_id)
        if task is not None:
            self._by_id[task_id] = task.model_copy(update={"status": status})

    async def update_confidence_and_explanation(
        self, task_id: uuid.UUID, *, confidence_assessment: dict, explanation: dict
    ) -> None:
        task = self._by_id.get(task_id)
        if task is not None:
            self._by_id[task_id] = task.model_copy(
                update={"confidence_assessment": confidence_assessment, "explanation": explanation}
            )

    async def update_execution_plan(self, task_id: uuid.UUID, execution_plan: dict) -> None:
        task = self._by_id.get(task_id)
        if task is not None:
            self._by_id[task_id] = task.model_copy(update={"execution_plan": execution_plan})

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[CognitiveTask]:
        matches = [t for t in self._by_id.values() if t.shift_id == shift_id]
        return sorted(matches, key=lambda t: t.created_at, reverse=True)


class InMemoryApprovalRepository:
    """Accepts a reference to the seeded `InMemoryCognitiveTaskRepository` so
    `count_rejections_for_action_type`/`count_approvals_for_action_type` can
    join against tasks by `action_type`, mirroring the real SQL repository's
    JOIN — a plain `return 0` here would silently defeat any test of the
    Merchant Memory / confidence-signal logic that depends on this count
    actually reflecting seeded history."""

    def __init__(self, task_repo: InMemoryCognitiveTaskRepository) -> None:
        self._task_repo = task_repo
        self._by_id: dict[uuid.UUID, Approval] = {}
        self._by_task_id: dict[uuid.UUID, uuid.UUID] = {}

    def seed(self, approval: Approval) -> None:
        self._by_id[approval.id] = approval
        self._by_task_id[approval.task_id] = approval.id

    async def create(
        self,
        *,
        task_id: uuid.UUID,
        issue_id: uuid.UUID,
        store_id: uuid.UUID,
        expires_at: datetime | None = None,
    ) -> Approval:
        now = datetime.now(timezone.utc)
        approval = Approval(
            id=uuid.uuid4(),
            task_id=task_id,
            issue_id=issue_id,
            store_id=store_id,
            status="PENDING",
            expires_at=expires_at or (now + timedelta(hours=24)),
            created_at=now,
        )
        self._by_id[approval.id] = approval
        self._by_task_id[task_id] = approval.id
        return approval

    async def get_by_id(self, approval_id: uuid.UUID) -> Approval | None:
        return self._by_id.get(approval_id)

    async def get_by_task_id(self, task_id: uuid.UUID) -> Approval | None:
        approval_id = self._by_task_id.get(task_id)
        return self._by_id.get(approval_id) if approval_id else None

    async def decide(
        self,
        approval_id: uuid.UUID,
        *,
        status: str,
        decided_at: datetime,
        approver_user_id: uuid.UUID | None = None,
        merchant_rationale: str | None = None,
        execution_override_params: dict | None = None,
    ) -> None:
        approval = self._by_id.get(approval_id)
        if approval is not None:
            self._by_id[approval_id] = approval.model_copy(
                update={
                    "status": status,
                    "decided_at": decided_at,
                    "approver_user_id": approver_user_id,
                    "merchant_rationale": merchant_rationale,
                    "execution_override_params": execution_override_params,
                }
            )

    async def _count_for_action_type(self, store_id: uuid.UUID, action_type: str, status: str) -> int:
        count = 0
        for approval in self._by_id.values():
            if approval.store_id != store_id or approval.status != status:
                continue
            task = await self._task_repo.get_by_id(approval.task_id)
            if task is not None and task.action_type == action_type:
                count += 1
        return count

    async def count_rejections_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int:
        return await self._count_for_action_type(store_id, action_type, "REJECTED")

    async def count_approvals_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int:
        return await self._count_for_action_type(store_id, action_type, "APPROVED")

    async def list_pending_for_store(self, store_id: uuid.UUID) -> list[Approval]:
        matches = [a for a in self._by_id.values() if a.store_id == store_id and a.status == "PENDING"]
        return sorted(matches, key=lambda a: a.created_at, reverse=True)

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Approval]:
        matches = []
        for approval in self._by_id.values():
            task = await self._task_repo.get_by_id(approval.task_id)
            if task is not None and task.shift_id == shift_id:
                matches.append(approval)
        return sorted(matches, key=lambda a: a.created_at, reverse=True)

    async def extend_expiry(self, approval_id: uuid.UUID, new_expires_at: datetime) -> None:
        approval = self._by_id.get(approval_id)
        if approval is not None:
            self._by_id[approval_id] = approval.model_copy(update={"expires_at": new_expires_at})


class InMemoryExecutionRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Execution] = {}
        self._by_task_id: dict[uuid.UUID, uuid.UUID] = {}

    def seed(self, execution: Execution) -> None:
        self._by_id[execution.id] = execution
        self._by_task_id[execution.task_id] = execution.id

    async def create(self, *, task_id: uuid.UUID, store_id: uuid.UUID, request_payload: dict) -> Execution:
        now = datetime.now(timezone.utc)
        execution = Execution(
            id=uuid.uuid4(),
            task_id=task_id,
            store_id=store_id,
            status="STARTED",
            request_payload=request_payload,
            started_at=now,
        )
        self._by_id[execution.id] = execution
        self._by_task_id[task_id] = execution.id
        return execution

    async def get_by_task_id(self, task_id: uuid.UUID) -> Execution | None:
        execution_id = self._by_task_id.get(task_id)
        return self._by_id.get(execution_id) if execution_id else None

    async def get_by_id(self, execution_id: uuid.UUID) -> Execution | None:
        return self._by_id.get(execution_id)

    async def mark_completed(
        self, execution_id: uuid.UUID, *, response_payload: dict, execution_duration_ms: int
    ) -> None:
        execution = self._by_id.get(execution_id)
        if execution is not None:
            self._by_id[execution_id] = execution.model_copy(
                update={
                    "status": "COMPLETED",
                    "response_payload": response_payload,
                    "execution_duration_ms": execution_duration_ms,
                    "completed_at": datetime.now(timezone.utc),
                }
            )

    async def mark_failed(
        self, execution_id: uuid.UUID, *, error_log: str, execution_duration_ms: int
    ) -> None:
        execution = self._by_id.get(execution_id)
        if execution is not None:
            self._by_id[execution_id] = execution.model_copy(
                update={
                    "status": "FAILED",
                    "error_log": error_log,
                    "execution_duration_ms": execution_duration_ms,
                    "completed_at": datetime.now(timezone.utc),
                }
            )

    async def mark_rolled_back(self, execution_id: uuid.UUID) -> None:
        execution = self._by_id.get(execution_id)
        if execution is not None:
            self._by_id[execution_id] = execution.model_copy(update={"status": "ROLLED_BACK"})

    async def increment_retry_count(self, execution_id: uuid.UUID) -> None:
        execution = self._by_id.get(execution_id)
        if execution is not None:
            self._by_id[execution_id] = execution.model_copy(
                update={"retry_count": execution.retry_count + 1}
            )


class InMemoryVerificationRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Verification] = {}
        self._by_execution_id: dict[uuid.UUID, uuid.UUID] = {}

    def seed(self, verification: Verification) -> None:
        self._by_id[verification.id] = verification
        self._by_execution_id[verification.execution_id] = verification.id

    async def create(
        self,
        *,
        execution_id: uuid.UUID,
        task_id: uuid.UUID,
        store_id: uuid.UUID,
        status: str,
        method: str,
        result_data: dict,
    ) -> Verification:
        verification = Verification(
            id=uuid.uuid4(),
            execution_id=execution_id,
            task_id=task_id,
            store_id=store_id,
            status=status,
            method=method,
            result_data=result_data,
            verified_at=datetime.now(timezone.utc),
        )
        self._by_id[verification.id] = verification
        self._by_execution_id[execution_id] = verification.id
        return verification

    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Verification | None:
        verification_id = self._by_execution_id.get(execution_id)
        return self._by_id.get(verification_id) if verification_id else None


class InMemoryRollbackRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Rollback] = {}

    def seed(self, rollback: Rollback) -> None:
        self._by_id[rollback.id] = rollback

    async def create(
        self,
        *,
        execution_id: uuid.UUID,
        task_id: uuid.UUID,
        store_id: uuid.UUID,
        reverted_state: dict,
        rollback_reason: str,
    ) -> Rollback:
        rollback = Rollback(
            id=uuid.uuid4(),
            execution_id=execution_id,
            task_id=task_id,
            store_id=store_id,
            status="STARTED",
            reverted_state=reverted_state,
            rollback_reason=rollback_reason,
            started_at=datetime.now(timezone.utc),
        )
        self._by_id[rollback.id] = rollback
        return rollback

    async def get_by_id(self, rollback_id: uuid.UUID) -> Rollback | None:
        return self._by_id.get(rollback_id)

    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Rollback | None:
        for rollback in self._by_id.values():
            if rollback.execution_id == execution_id:
                return rollback
        return None

    async def mark_completed(self, rollback_id: uuid.UUID) -> None:
        rollback = self._by_id.get(rollback_id)
        if rollback is not None:
            self._by_id[rollback_id] = rollback.model_copy(
                update={"status": "COMPLETED", "completed_at": datetime.now(timezone.utc)}
            )

    async def mark_failed(self, rollback_id: uuid.UUID, *, error_log: str) -> None:
        rollback = self._by_id.get(rollback_id)
        if rollback is not None:
            self._by_id[rollback_id] = rollback.model_copy(
                update={
                    "status": "FAILED",
                    "error_log": error_log,
                    "completed_at": datetime.now(timezone.utc),
                }
            )


class InMemoryAuditLogRepository:
    """Append-only, mirroring `SqlAuditLogRepository`'s deliberate absence
    of any update/delete method."""

    def __init__(self) -> None:
        self._entries: list[AuditLogEntry] = []

    def seed(self, entry: AuditLogEntry) -> None:
        self._entries.append(entry)

    async def append(
        self,
        *,
        store_id: uuid.UUID,
        actor_type: str,
        actor_id: str,
        action: str,
        rationale: str,
        shift_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        execution_id: uuid.UUID | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        model_identifier: str | None = None,
        prompt_version: str | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=shift_id,
            task_id=task_id,
            execution_id=execution_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            before_state=before_state,
            after_state=after_state,
            rationale=rationale,
            model_identifier=model_identifier,
            prompt_version=prompt_version,
            timestamp=datetime.now(timezone.utc),
        )
        self._entries.append(entry)
        return entry

    async def list_for_store(
        self, store_id: uuid.UUID, *, limit: int = 20, before_timestamp: datetime | None = None
    ) -> list[AuditLogEntry]:
        matches = [e for e in self._entries if e.store_id == store_id]
        if before_timestamp is not None:
            matches = [e for e in matches if e.timestamp < before_timestamp]
        matches.sort(key=lambda e: e.timestamp, reverse=True)
        return matches[:limit]

    async def get_for_task(self, task_id: uuid.UUID) -> list[AuditLogEntry]:
        matches = [e for e in self._entries if e.task_id == task_id]
        return sorted(matches, key=lambda e: e.timestamp)

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[AuditLogEntry]:
        matches = [e for e in self._entries if e.shift_id == shift_id]
        return sorted(matches, key=lambda e: e.timestamp)

    async def get_latest_shift_id_with_activity(self, store_id: uuid.UUID) -> uuid.UUID | None:
        matches = [e for e in self._entries if e.store_id == store_id and e.shift_id is not None]
        if not matches:
            return None
        return max(matches, key=lambda e: e.timestamp).shift_id


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist — in-memory fakes


class InMemoryThemeSnapshotRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[uuid.UUID, str, str], ThemeSnapshotRecord] = {}

    def seed(self, snapshot: ThemeSnapshotRecord) -> None:
        self._by_key[(snapshot.store_id, snapshot.theme_id, snapshot.filename)] = snapshot

    async def get_by_filename(
        self, store_id: uuid.UUID, theme_id: str, filename: str
    ) -> ThemeSnapshotRecord | None:
        return self._by_key.get((store_id, theme_id, filename))

    async def create_baseline(
        self, *, store_id: uuid.UUID, theme_id: str, filename: str, content: str, checksum_md5: str
    ) -> ThemeSnapshotRecord:
        now = datetime.now(timezone.utc)
        record = ThemeSnapshotRecord(
            id=uuid.uuid4(),
            store_id=store_id,
            theme_id=theme_id,
            filename=filename,
            content=content,
            checksum_md5=checksum_md5,
            captured_at=now,
            updated_at=now,
        )
        self._by_key[(store_id, theme_id, filename)] = record
        return record


class InMemoryTrackingSnapshotRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[uuid.UUID, str], TrackingSnapshotRecord] = {}

    def seed(self, snapshot: TrackingSnapshotRecord) -> None:
        self._by_key[(snapshot.store_id, snapshot.src)] = snapshot

    async def list_for_store(self, store_id: uuid.UUID) -> list[TrackingSnapshotRecord]:
        return [record for (sid, _), record in self._by_key.items() if sid == store_id]

    async def create(
        self, *, store_id: uuid.UUID, src: str, display_scope: str | None, pattern_name: str | None
    ) -> TrackingSnapshotRecord:
        now = datetime.now(timezone.utc)
        record = TrackingSnapshotRecord(
            id=uuid.uuid4(),
            store_id=store_id,
            src=src,
            display_scope=display_scope,
            pattern_name=pattern_name,
            captured_at=now,
            updated_at=now,
        )
        self._by_key[(store_id, src)] = record
        return record


# --- Billing: NightShift Free / Pro / Business monetization — in-memory fake


class InMemorySubscriptionRepository:
    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, Subscription] = {}

    def seed(self, subscription: Subscription) -> None:
        self._by_id[subscription.id] = subscription

    async def create(
        self,
        *,
        store_id: uuid.UUID,
        plan: str,
        status: str,
        shopify_charge_gid: str | None = None,
        monthly_price_usd: float = 0.0,
    ) -> Subscription:
        now = datetime.now(timezone.utc)
        subscription = Subscription(
            id=uuid.uuid4(),
            store_id=store_id,
            plan=plan,
            status=status,
            shopify_charge_gid=shopify_charge_gid,
            monthly_price_usd=monthly_price_usd,
            created_at=now,
            updated_at=now,
        )
        self._by_id[subscription.id] = subscription
        return subscription

    async def get_by_id(self, subscription_id: uuid.UUID) -> Subscription | None:
        return self._by_id.get(subscription_id)

    async def get_by_store_and_charge_gid(
        self, store_id: uuid.UUID, shopify_charge_gid: str
    ) -> Subscription | None:
        for subscription in self._by_id.values():
            if subscription.store_id == store_id and subscription.shopify_charge_gid == shopify_charge_gid:
                return subscription
        return None

    async def get_current_for_store(self, store_id: uuid.UUID) -> Subscription | None:
        matches = [s for s in self._by_id.values() if s.store_id == store_id]
        if not matches:
            return None
        return max(matches, key=lambda s: s.created_at)

    async def update_status(
        self,
        subscription_id: uuid.UUID,
        *,
        status: str,
        activated_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> None:
        subscription = self._by_id.get(subscription_id)
        if subscription is None:
            return
        update: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if activated_at is not None:
            update["activated_at"] = activated_at
        if cancelled_at is not None:
            update["cancelled_at"] = cancelled_at
        self._by_id[subscription_id] = subscription.model_copy(update=update)
