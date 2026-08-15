"""Repository classes — the only place raw SQLAlchemy queries live.

Use cases depend on the `OrganizationRepository` / `StoreRepository` /
`StoreTokenRepository` / `ShiftRepository` / `MetricsRepository` protocols
declared in app.application.ports, not on these classes directly, so tests
can substitute in-memory fakes (app.application.ports.InMemory*).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import (
    Agent,
    Approval,
    AuditLog,
    CognitiveTask,
    Execution,
    Issue,
    MetricsHourly,
    Organization,
    Rollback,
    Shift,
    ShiftReport,
    Store,
    StoreToken,
    Subscription,
    ThemeSnapshot,
    TrackingSnapshot,
    Verification,
)


class SqlOrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self._session.execute(select(Organization).where(Organization.slug == slug))
        return result.scalar_one_or_none()

    async def create(self, *, name: str, slug: str, billing_email: str) -> Organization:
        org = Organization(name=name, slug=slug, billing_email=billing_email)
        self._session.add(org)
        await self._session.flush()
        return org


class SqlStoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_shopify_domain(self, shopify_domain: str) -> Store | None:
        result = await self._session.execute(
            select(Store).where(Store.shopify_domain == shopify_domain)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, store_id: uuid.UUID) -> Store | None:
        result = await self._session.execute(select(Store).where(Store.id == store_id))
        return result.scalar_one_or_none()

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
        """Create or reactivate a Store on (re)installation.

        Handles the re-install edge case named in Sprint 1 Feature 2: "Merchant
        re-installs app after uninstallation (must update existing Store
        record and rotate token)."
        """
        existing = await self.get_by_shopify_domain(shopify_domain)
        if existing is not None:
            existing.store_name = store_name
            existing.currency_code = currency_code
            existing.iana_timezone = iana_timezone
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            return existing

        store = Store(
            organization_id=organization_id,
            shopify_domain=shopify_domain,
            myshopify_domain=myshopify_domain,
            store_name=store_name,
            currency_code=currency_code,
            iana_timezone=iana_timezone,
        )
        self._session.add(store)
        await self._session.flush()
        return store

    async def update_health_score(self, store_id: uuid.UUID, health_score: int) -> None:
        store = await self.get_by_id(store_id)
        if store is not None:
            store.health_score = health_score
            await self._session.flush()

    async def list_active(self) -> list[Store]:
        result = await self._session.execute(select(Store).where(Store.is_active.is_(True)))
        return list(result.scalars().all())

    async def deactivate(self, store_id: uuid.UUID) -> None:
        """Shopify Compliance `app/uninstalled` webhook handler's write —
        see `StoreRepository.deactivate`'s Protocol docstring."""
        store = await self.get_by_id(store_id)
        if store is not None:
            store.is_active = False
            store.updated_at = datetime.now(timezone.utc)
            await self._session.flush()


class SqlStoreTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, store_id: uuid.UUID, access_token_encrypted: str, scopes: list[str]
    ) -> StoreToken:
        """Rotate the token on reinstall (unique constraint on store_id)."""
        stmt = (
            pg_insert(StoreToken)
            .values(
                store_id=store_id,
                access_token_encrypted=access_token_encrypted,
                scopes=scopes,
            )
            .on_conflict_do_update(
                index_elements=[StoreToken.store_id],
                set_={
                    "access_token_encrypted": access_token_encrypted,
                    "scopes": scopes,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(StoreToken)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    async def get_by_store_id(self, store_id: uuid.UUID) -> StoreToken | None:
        result = await self._session.execute(
            select(StoreToken).where(StoreToken.store_id == store_id)
        )
        return result.scalar_one_or_none()


class SqlShiftRepository:
    """Per-store shift numbering lives here in application logic rather than
    a Postgres SERIAL column (brief Section 7.5 — SERIAL is a single global
    sequence and would break the intended per-store numbering semantics)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_shift_number(self, store_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(Shift.shift_number), 0)).where(Shift.store_id == store_id)
        )
        return result.scalar_one() + 1

    async def create_baseline_shift(self, store_id: uuid.UUID) -> Shift:
        shift_number = await self.next_shift_number(store_id)
        shift = Shift(store_id=store_id, shift_number=shift_number, status="IN_PROGRESS")
        self._session.add(shift)
        await self._session.flush()
        return shift

    async def create_shift(self, store_id: uuid.UUID, *, status: str = "IN_PROGRESS") -> Shift:
        """Sprint 2: generic per-store shift creation for the nightly
        inspection pipeline (`tasks.inspect_catalog`). Kept distinct from
        `create_baseline_shift`, which is Sprint 1's first-install baseline
        scan and is left untouched."""
        shift_number = await self.next_shift_number(store_id)
        shift = Shift(store_id=store_id, shift_number=shift_number, status=status)
        self._session.add(shift)
        await self._session.flush()
        return shift

    async def get_by_id(self, shift_id: uuid.UUID) -> Shift | None:
        result = await self._session.execute(select(Shift).where(Shift.id == shift_id))
        return result.scalar_one_or_none()

    async def get_latest_completed(self, store_id: uuid.UUID) -> Shift | None:
        result = await self._session.execute(
            select(Shift)
            .where(Shift.store_id == store_id, Shift.status == "COMPLETED")
            .order_by(Shift.shift_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_completed(self, shift_id: uuid.UUID) -> None:
        result = await self._session.execute(select(Shift).where(Shift.id == shift_id))
        shift = result.scalar_one_or_none()
        if shift is not None:
            shift.status = "COMPLETED"
            shift.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def update_metrics(
        self,
        shift_id: uuid.UUID,
        *,
        issues_detected_count: int,
        estimated_revenue_protected: float,
        estimated_time_saved_hours: float,
    ) -> None:
        result = await self._session.execute(select(Shift).where(Shift.id == shift_id))
        shift = result.scalar_one_or_none()
        if shift is not None:
            shift.issues_detected_count = issues_detected_count
            shift.estimated_revenue_protected = estimated_revenue_protected
            shift.estimated_time_saved_hours = estimated_time_saved_hours
            await self._session.flush()

    async def increment_pending_approvals(self, shift_id: uuid.UUID, delta: int) -> None:
        """Sprint 3: adjusts `pending_approvals_count` as approvals are
        created/resolved during the Execution lifecycle. Clamped at 0 so a
        double-decrement (e.g. a retried callback) can never drive the
        counter negative."""
        result = await self._session.execute(select(Shift).where(Shift.id == shift_id))
        shift = result.scalar_one_or_none()
        if shift is not None:
            shift.pending_approvals_count = max(0, shift.pending_approvals_count + delta)
            await self._session.flush()

    async def increment_resolved_count(self, shift_id: uuid.UUID, delta: int = 1) -> None:
        """Sprint 3: increments `issues_resolved_count` when a CognitiveTask
        reaches SUCCESS."""
        result = await self._session.execute(select(Shift).where(Shift.id == shift_id))
        shift = result.scalar_one_or_none()
        if shift is not None:
            shift.issues_resolved_count = max(0, shift.issues_resolved_count + delta)
            await self._session.flush()


class SqlMetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_baseline_metric(
        self, *, store_id: uuid.UUID, health_score: int, open_issues_count: int = 0
    ) -> MetricsHourly:
        metric = MetricsHourly(
            store_id=store_id,
            health_score=health_score,
            open_issues_count=open_issues_count,
            revenue_protected_delta=0,
        )
        self._session.add(metric)
        await self._session.flush()
        return metric

    async def record_metric(
        self, *, store_id: uuid.UUID, health_score: int, open_issues_count: int, revenue_protected_delta: float = 0.0
    ) -> MetricsHourly:
        """Sprint 2: records a health-score snapshot after each nightly
        inspection shift (Feature 3 DoD: "Store Health Score recalculates
        deterministically post-inspection and writes to metrics_hourly and
        stores.")."""
        metric = MetricsHourly(
            store_id=store_id,
            health_score=health_score,
            open_issues_count=open_issues_count,
            revenue_protected_delta=revenue_protected_delta,
        )
        self._session.add(metric)
        await self._session.flush()
        return metric


class SqlAgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_identifier(self, identifier: str) -> Agent | None:
        result = await self._session.execute(select(Agent).where(Agent.identifier == identifier))
        return result.scalar_one_or_none()


class SqlIssueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        issue = Issue(
            store_id=store_id,
            shift_id=shift_id,
            agent_id=agent_id,
            category=category,
            severity=severity,
            title=title,
            description=description,
            evidence_data=evidence_data,
            affected_resources=affected_resources,
            revenue_impact_estimate=revenue_impact_estimate,
            confidence_score=confidence_score,
        )
        self._session.add(issue)
        await self._session.flush()
        return issue

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Issue]:
        result = await self._session.execute(
            select(Issue).where(Issue.shift_id == shift_id).order_by(Issue.revenue_impact_estimate.desc())
        )
        return list(result.scalars().all())

    async def list_for_store(self, store_id: uuid.UUID) -> list[Issue]:
        result = await self._session.execute(
            select(Issue).where(Issue.store_id == store_id).order_by(Issue.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_status(self, issue_id: uuid.UUID, status: str) -> None:
        """Sprint 3: transitions `issues.status` through OPEN ->
        AWAITING_APPROVAL -> IN_PROGRESS -> RESOLVED/FAILED as a
        CognitiveTask moves through the Execute/Verify lifecycle."""
        result = await self._session.execute(select(Issue).where(Issue.id == issue_id))
        issue = result.scalar_one_or_none()
        if issue is not None:
            issue.status = status
            await self._session.flush()

    async def get_by_id(self, issue_id: uuid.UUID) -> Issue | None:
        """Sprint 3 Part 2: needed by the Verify/Rollback use cases and the
        `GET /api/v1/tasks/{task_id}` route, which join a CognitiveTask back
        to its originating Issue."""
        result = await self._session.execute(select(Issue).where(Issue.id == issue_id))
        return result.scalar_one_or_none()

    async def get_open_by_dedup_key(self, store_id: uuid.UUID, dedup_key: str) -> Issue | None:
        """Sprint 4: `.contains()` on a JSONB column compiles to Postgres's
        `@>` containment operator — matches any issue whose `evidence_data`
        has this exact `dedup_key`, regardless of what else is in that dict."""
        result = await self._session.execute(
            select(Issue).where(
                Issue.store_id == store_id,
                Issue.status.in_(["OPEN", "AWAITING_APPROVAL"]),
                Issue.evidence_data.contains({"dedup_key": dedup_key}),
            )
        )
        return result.scalars().first()


class SqlShiftReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, shift_id: uuid.UUID, store_id: uuid.UUID, executive_summary: str, report_json: dict
    ) -> ShiftReport:
        report = ShiftReport(
            shift_id=shift_id,
            store_id=store_id,
            executive_summary=executive_summary,
            report_json=report_json,
        )
        self._session.add(report)
        await self._session.flush()
        return report

    async def get_by_shift_id(self, shift_id: uuid.UUID) -> ShiftReport | None:
        result = await self._session.execute(select(ShiftReport).where(ShiftReport.shift_id == shift_id))
        return result.scalar_one_or_none()

    async def get_latest_for_store(self, store_id: uuid.UUID) -> ShiftReport | None:
        result = await self._session.execute(
            select(ShiftReport)
            .where(ShiftReport.store_id == store_id)
            .order_by(ShiftReport.published_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent_for_store(self, store_id: uuid.UUID, *, limit: int) -> list[ShiftReport]:
        result = await self._session.execute(
            select(ShiftReport)
            .where(ShiftReport.store_id == store_id)
            .order_by(ShiftReport.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# --- Sprint 3: AI Trust & Execution -----------------------------------------


class SqlCognitiveTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        task = CognitiveTask(
            issue_id=issue_id,
            store_id=store_id,
            shift_id=shift_id,
            agent_id=agent_id,
            action_type=action_type,
            execution_plan=execution_plan,
            risk_level=risk_level,
            risk_reasoning=risk_reasoning,
            idempotency_key=idempotency_key,
            status=status,
            confidence_assessment=confidence_assessment or {},
            explanation=explanation or {},
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_by_id(self, task_id: uuid.UUID) -> CognitiveTask | None:
        result = await self._session.execute(select(CognitiveTask).where(CognitiveTask.id == task_id))
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> CognitiveTask | None:
        """Sprint 3 idempotent (re-)planning: if a task with this key
        already exists, planning should skip re-creating it rather than
        producing a duplicate CognitiveTask for the same Issue+action."""
        result = await self._session.execute(
            select(CognitiveTask).where(CognitiveTask.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def update_status(self, task_id: uuid.UUID, status: str) -> None:
        result = await self._session.execute(select(CognitiveTask).where(CognitiveTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is not None:
            task.status = status
            await self._session.flush()

    async def update_confidence_and_explanation(
        self, task_id: uuid.UUID, *, confidence_assessment: dict, explanation: dict
    ) -> None:
        result = await self._session.execute(select(CognitiveTask).where(CognitiveTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is not None:
            task.confidence_assessment = confidence_assessment
            task.explanation = explanation
            await self._session.flush()

    async def update_execution_plan(self, task_id: uuid.UUID, execution_plan: dict) -> None:
        """Sprint 3 Part 2: supports the merchant "Modify" approval action
        (ApprovalAction.APPROVE + `execution_override_params`, ADR-028) —
        `HandleApprovalAction` shallow-merges the override params into a copy
        of the task's `execution_plan` and persists the merged plan here
        before dispatching execution, so `ExecuteCognitiveTask` always reads
        the final, merchant-approved parameters rather than the original
        AI-proposed ones."""
        result = await self._session.execute(select(CognitiveTask).where(CognitiveTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is not None:
            task.execution_plan = execution_plan
            await self._session.flush()

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[CognitiveTask]:
        result = await self._session.execute(
            select(CognitiveTask)
            .where(CognitiveTask.shift_id == shift_id)
            .order_by(CognitiveTask.created_at.desc())
        )
        return list(result.scalars().all())


class SqlApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        task_id: uuid.UUID,
        issue_id: uuid.UUID,
        store_id: uuid.UUID,
        expires_at: datetime | None = None,
    ) -> Approval:
        """`expires_at` is left unset unless the caller wants to override
        it — the column's own `DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24
        hours')` in the migration is the single source of truth for the
        default TTL, not re-hardcoded here."""
        kwargs: dict = {"task_id": task_id, "issue_id": issue_id, "store_id": store_id}
        if expires_at is not None:
            kwargs["expires_at"] = expires_at
        approval = Approval(**kwargs)
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get_by_id(self, approval_id: uuid.UUID) -> Approval | None:
        result = await self._session.execute(select(Approval).where(Approval.id == approval_id))
        return result.scalar_one_or_none()

    async def get_by_task_id(self, task_id: uuid.UUID) -> Approval | None:
        result = await self._session.execute(select(Approval).where(Approval.task_id == task_id))
        return result.scalar_one_or_none()

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
        result = await self._session.execute(select(Approval).where(Approval.id == approval_id))
        approval = result.scalar_one_or_none()
        if approval is not None:
            approval.status = status
            approval.decided_at = decided_at
            approval.approver_user_id = approver_user_id
            approval.merchant_rationale = merchant_rationale
            approval.execution_override_params = execution_override_params
            await self._session.flush()

    async def count_rejections_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int:
        """Feeds `domain/merchant_memory.py::has_repeated_rejection_history`.
        Requires a JOIN to `cognitive_tasks` since `action_type` lives there,
        not on `approvals` itself."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Approval)
            .join(CognitiveTask, Approval.task_id == CognitiveTask.id)
            .where(
                Approval.store_id == store_id,
                CognitiveTask.action_type == action_type,
                Approval.status == "REJECTED",
            )
        )
        return result.scalar_one()

    async def list_pending_for_store(self, store_id: uuid.UUID) -> list[Approval]:
        result = await self._session.execute(
            select(Approval)
            .where(Approval.store_id == store_id, Approval.status == "PENDING")
            .order_by(Approval.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[Approval]:
        """Sprint 3 Part 2: needed by `workers/tasks/shift_report.py` to
        build the persisted `pending_approvals[]` section of the Morning
        Shift Report — joins through `cognitive_tasks` since `shift_id`
        lives there, not on `approvals` itself. Not in Part 1's spec; added
        now as the minimal symmetric extension of the existing
        `list_pending_for_store` query pattern (see CONFLICTS.md)."""
        result = await self._session.execute(
            select(Approval)
            .join(CognitiveTask, Approval.task_id == CognitiveTask.id)
            .where(CognitiveTask.shift_id == shift_id)
            .order_by(Approval.created_at.desc())
        )
        return list(result.scalars().all())

    async def count_approvals_for_action_type(self, store_id: uuid.UUID, action_type: str) -> int:
        """Symmetric companion to `count_rejections_for_action_type` —
        together the two let `PlanCognitiveTasks` derive a merchant
        approval-rate signal for `compute_confidence` without inventing a
        new, more complex aggregate repository method. Counts APPROVED (not
        merely decided) approvals only."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Approval)
            .join(CognitiveTask, Approval.task_id == CognitiveTask.id)
            .where(
                Approval.store_id == store_id,
                CognitiveTask.action_type == action_type,
                Approval.status == "APPROVED",
            )
        )
        return result.scalar_one()

    async def extend_expiry(self, approval_id: uuid.UUID, new_expires_at: datetime) -> None:
        """Sprint 3 Part 2: supports the DEFER approval action, which does
        not change `status` (stays PENDING) but pushes the decision window
        back — a dedicated method rather than overloading `decide()`, since
        DEFER is not itself a decision."""
        result = await self._session.execute(select(Approval).where(Approval.id == approval_id))
        approval = result.scalar_one_or_none()
        if approval is not None:
            approval.expires_at = new_expires_at
            await self._session.flush()


class SqlExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, task_id: uuid.UUID, store_id: uuid.UUID, request_payload: dict) -> Execution:
        execution = Execution(task_id=task_id, store_id=store_id, request_payload=request_payload)
        self._session.add(execution)
        await self._session.flush()
        return execution

    async def get_by_task_id(self, task_id: uuid.UUID) -> Execution | None:
        result = await self._session.execute(select(Execution).where(Execution.task_id == task_id))
        return result.scalar_one_or_none()

    async def get_by_id(self, execution_id: uuid.UUID) -> Execution | None:
        """Sprint 3 Part 2: `VerifyExecution.execute(execution_id)` and
        `RollbackCognitiveTask.execute(execution_id)` both need to load an
        Execution by its own id, not by task_id — not in Part 1's spec;
        added now as the minimal symmetric extension of the pattern already
        used by every other repository's `get_by_id`."""
        result = await self._session.execute(select(Execution).where(Execution.id == execution_id))
        return result.scalar_one_or_none()

    async def mark_completed(
        self, execution_id: uuid.UUID, *, response_payload: dict, execution_duration_ms: int
    ) -> None:
        result = await self._session.execute(select(Execution).where(Execution.id == execution_id))
        execution = result.scalar_one_or_none()
        if execution is not None:
            execution.status = "COMPLETED"
            execution.response_payload = response_payload
            execution.execution_duration_ms = execution_duration_ms
            execution.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def mark_failed(
        self, execution_id: uuid.UUID, *, error_log: str, execution_duration_ms: int
    ) -> None:
        result = await self._session.execute(select(Execution).where(Execution.id == execution_id))
        execution = result.scalar_one_or_none()
        if execution is not None:
            execution.status = "FAILED"
            execution.error_log = error_log
            execution.execution_duration_ms = execution_duration_ms
            execution.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def mark_rolled_back(self, execution_id: uuid.UUID) -> None:
        result = await self._session.execute(select(Execution).where(Execution.id == execution_id))
        execution = result.scalar_one_or_none()
        if execution is not None:
            execution.status = "ROLLED_BACK"
            await self._session.flush()

    async def increment_retry_count(self, execution_id: uuid.UUID) -> None:
        result = await self._session.execute(select(Execution).where(Execution.id == execution_id))
        execution = result.scalar_one_or_none()
        if execution is not None:
            execution.retry_count += 1
            await self._session.flush()


class SqlVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
            execution_id=execution_id,
            task_id=task_id,
            store_id=store_id,
            status=status,
            method=method,
            result_data=result_data,
        )
        self._session.add(verification)
        await self._session.flush()
        return verification

    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Verification | None:
        result = await self._session.execute(
            select(Verification).where(Verification.execution_id == execution_id)
        )
        return result.scalar_one_or_none()


class SqlRollbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
            execution_id=execution_id,
            task_id=task_id,
            store_id=store_id,
            reverted_state=reverted_state,
            rollback_reason=rollback_reason,
        )
        self._session.add(rollback)
        await self._session.flush()
        return rollback

    async def get_by_id(self, rollback_id: uuid.UUID) -> Rollback | None:
        """Sprint 3 Part 2: lets `RollbackCognitiveTask` return an
        up-to-date `Rollback` domain object (reflecting `mark_completed`/
        `mark_failed`) rather than the stale STARTED-status object
        `create()` returned. Not in Part 1's spec; minimal symmetric
        addition matching every other repository's `get_by_id`."""
        result = await self._session.execute(select(Rollback).where(Rollback.id == rollback_id))
        return result.scalar_one_or_none()

    async def get_by_execution_id(self, execution_id: uuid.UUID) -> Rollback | None:
        """Sprint 3 Part 2: needed by `GET /api/v1/tasks/{task_id}` to
        assemble the optional `rollback` field of `TaskDetailResponse` —
        mirrors `SqlVerificationRepository.get_by_execution_id` exactly."""
        result = await self._session.execute(
            select(Rollback).where(Rollback.execution_id == execution_id)
        )
        return result.scalar_one_or_none()

    async def mark_completed(self, rollback_id: uuid.UUID) -> None:
        result = await self._session.execute(select(Rollback).where(Rollback.id == rollback_id))
        rollback = result.scalar_one_or_none()
        if rollback is not None:
            rollback.status = "COMPLETED"
            rollback.completed_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def mark_failed(self, rollback_id: uuid.UUID, *, error_log: str) -> None:
        result = await self._session.execute(select(Rollback).where(Rollback.id == rollback_id))
        rollback = result.scalar_one_or_none()
        if rollback is not None:
            rollback.status = "FAILED"
            rollback.error_log = error_log
            rollback.completed_at = datetime.now(timezone.utc)
            await self._session.flush()


class SqlAuditLogRepository:
    """Append-only by design: intentionally exposes no update/delete method
    at all, so the append-only intent is enforced by the absence of a
    mutation path rather than by convention alone."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> AuditLog:
        entry = AuditLog(
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
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_store(
        self, store_id: uuid.UUID, *, limit: int = 20, before_timestamp: datetime | None = None
    ) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.store_id == store_id)
        if before_timestamp is not None:
            stmt = stmt.where(AuditLog.timestamp < before_timestamp)
        stmt = stmt.order_by(AuditLog.timestamp.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_task(self, task_id: uuid.UUID) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog).where(AuditLog.task_id == task_id).order_by(AuditLog.timestamp.asc())
        )
        return list(result.scalars().all())

    async def list_for_shift(self, shift_id: uuid.UUID) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog).where(AuditLog.shift_id == shift_id).order_by(AuditLog.timestamp.asc())
        )
        return list(result.scalars().all())

    async def get_latest_shift_id_with_activity(self, store_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(AuditLog.shift_id)
            .where(AuditLog.store_id == store_id, AuditLog.shift_id.is_not(None))
            .order_by(AuditLog.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist ------------------


class SqlThemeSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_filename(
        self, store_id: uuid.UUID, theme_id: str, filename: str
    ) -> ThemeSnapshot | None:
        result = await self._session.execute(
            select(ThemeSnapshot).where(
                ThemeSnapshot.store_id == store_id,
                ThemeSnapshot.theme_id == theme_id,
                ThemeSnapshot.filename == filename,
            )
        )
        return result.scalar_one_or_none()

    async def create_baseline(
        self, *, store_id: uuid.UUID, theme_id: str, filename: str, content: str, checksum_md5: str
    ) -> ThemeSnapshot:
        snapshot = ThemeSnapshot(
            store_id=store_id,
            theme_id=theme_id,
            filename=filename,
            content=content,
            checksum_md5=checksum_md5,
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot


class SqlTrackingSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_store(self, store_id: uuid.UUID) -> list[TrackingSnapshot]:
        result = await self._session.execute(
            select(TrackingSnapshot).where(TrackingSnapshot.store_id == store_id)
        )
        return list(result.scalars().all())

    async def create(
        self, *, store_id: uuid.UUID, src: str, display_scope: str | None, pattern_name: str | None
    ) -> TrackingSnapshot:
        """Idempotent upsert on the `(store_id, src)` unique constraint —
        same `pg_insert(...).on_conflict_do_update(...)` pattern as
        `SqlStoreTokenRepository.upsert`. Originally a bare INSERT (matching
        the Protocol's own "only ever called for a src not already present"
        contract), but `TriggerDemoIncident`'s Rogue Developer Theme Break
        scenario is meant to be re-triggerable for repeated demos and calls
        this unconditionally every time — a second trigger against a store
        that already has this exact snapshot crashed with a live
        `UniqueViolationError` instead of succeeding. The in-memory test
        fake was already effectively idempotent (a plain dict-key
        overwrite), which is exactly why this went uncaught: only the real
        SQL-backed path enforced insert-only. Real detection callers that
        only ever call this for a genuinely-new `src` are unaffected — an
        upsert onto an absent row is just an insert."""
        stmt = (
            pg_insert(TrackingSnapshot)
            .values(store_id=store_id, src=src, display_scope=display_scope, pattern_name=pattern_name)
            .on_conflict_do_update(
                index_elements=[TrackingSnapshot.store_id, TrackingSnapshot.src],
                set_={
                    "display_scope": display_scope,
                    "pattern_name": pattern_name,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(TrackingSnapshot)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class SqlSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        store_id: uuid.UUID,
        plan: str,
        status: str,
        shopify_charge_gid: str | None = None,
        monthly_price_usd: float = 0.0,
    ) -> Subscription:
        subscription = Subscription(
            store_id=store_id,
            plan=plan,
            status=status,
            shopify_charge_gid=shopify_charge_gid,
            monthly_price_usd=monthly_price_usd,
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    async def get_by_id(self, subscription_id: uuid.UUID) -> Subscription | None:
        result = await self._session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        return result.scalar_one_or_none()

    async def get_by_store_and_charge_gid(
        self, store_id: uuid.UUID, shopify_charge_gid: str
    ) -> Subscription | None:
        result = await self._session.execute(
            select(Subscription).where(
                Subscription.store_id == store_id,
                Subscription.shopify_charge_gid == shopify_charge_gid,
            )
        )
        return result.scalar_one_or_none()

    async def get_current_for_store(self, store_id: uuid.UUID) -> Subscription | None:
        result = await self._session.execute(
            select(Subscription)
            .where(Subscription.store_id == store_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        subscription_id: uuid.UUID,
        *,
        status: str,
        activated_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> None:
        result = await self._session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            return
        subscription.status = status
        if activated_at is not None:
            subscription.activated_at = activated_at
        if cancelled_at is not None:
            subscription.cancelled_at = cancelled_at
        await self._session.flush()
