"""SQLAlchemy ORM models for Sprint 1's tables.

Column-for-column matches the migration in
alembic/versions/0001_sprint1_baseline_schema.py, which is itself reconciled
from the Sprint 1 Spec's own DDL plus `metrics_hourly` / `store_memories`
pulled forward from the Database Architecture document (brief Section 7.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import (
    ApprovalStatus,
    ExecutionStatus,
    IssueCategory,
    IssueSeverity,
    IssueStatus,
    PlanTier,
    RiskLevel,
    ShiftStatus,
    SubscriptionStatus,
    TaskStatus,
    VerificationStatus,
)
from app.infrastructure.database.session import Base

# Postgres enum types are created via raw DDL in the Alembic migrations
# (0001_sprint1_baseline_schema.py / 0002_sprint2_ai_employee_schema.py /
# 0003_sprint3_trust_execution_schema.py), not by SQLAlchemy —
# `create_type=False` prevents these ORM-level Enum columns from trying to
# CREATE TYPE a second time.
_ISSUE_CATEGORY_ENUM = SqlEnum(
    *[c.value for c in IssueCategory], name="issue_category", create_type=False
)
_ISSUE_SEVERITY_ENUM = SqlEnum(
    *[s.value for s in IssueSeverity], name="issue_severity", create_type=False
)
_ISSUE_STATUS_ENUM = SqlEnum(
    *[s.value for s in IssueStatus], name="issue_status", create_type=False
)
_SHIFT_STATUS_ENUM = SqlEnum(
    *[s.value for s in ShiftStatus], name="shift_status", create_type=False
)

# --- Sprint 3: AI Trust & Execution -----------------------------------------
_RISK_LEVEL_ENUM = SqlEnum(*[r.value for r in RiskLevel], name="risk_level", create_type=False)
_TASK_STATUS_ENUM = SqlEnum(*[s.value for s in TaskStatus], name="task_status", create_type=False)
_APPROVAL_STATUS_ENUM = SqlEnum(
    *[s.value for s in ApprovalStatus], name="approval_status", create_type=False
)
_EXECUTION_STATUS_ENUM = SqlEnum(
    *[s.value for s in ExecutionStatus], name="execution_status", create_type=False
)
_VERIFICATION_STATUS_ENUM = SqlEnum(
    *[s.value for s in VerificationStatus], name="verification_status", create_type=False
)
# `rollbacks.status` reuses `_EXECUTION_STATUS_ENUM` — the migration reuses
# the same Postgres `execution_status` enum type rather than defining a
# redundant sixth type (see 0003_sprint3_trust_execution_schema.py docstring).

# --- Billing: NightShift Free / Pro / Business monetization -----------------
_PLAN_TIER_ENUM = SqlEnum(*[p.value for p in PlanTier], name="plan_tier", create_type=False)
_SUBSCRIPTION_STATUS_ENUM = SqlEnum(
    *[s.value for s in SubscriptionStatus], name="subscription_status", create_type=False
)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    billing_email: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(50), nullable=False, default="GROWTH")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stores: Mapped[list["Store"]] = relationship(back_populates="organization")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    shopify_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    myshopify_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    store_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    iana_timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="America/New_York")
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preferred_shift_time: Mapped[str] = mapped_column(Time, nullable=False, server_default="02:00:00")
    is_autonomous_execution_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("health_score BETWEEN 0 AND 100", name="ck_stores_health_score_range"),
    )

    organization: Mapped["Organization"] = relationship(back_populates="stores")
    token: Mapped["StoreToken | None"] = relationship(back_populates="store", uselist=False)
    shifts: Mapped[list["Shift"]] = relationship(back_populates="store")


class StoreToken(Base):
    __tablename__ = "store_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    access_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    token_type: Mapped[str] = mapped_column(String(50), nullable=False, default="offline")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship(back_populates="token")


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    # NOTE: plain INT, not SERIAL. Sprint 1's own migration used a global
    # SERIAL sequence here, which contradicts the per-store
    # UNIQUE(store_id, shift_number) intent (brief Section 7.5). The
    # per-store number is assigned in application logic — see
    # ShiftRepository.next_shift_number.
    shift_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(_SHIFT_STATUS_ENUM, nullable=False, default="SCHEDULED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issues_detected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issues_resolved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_approvals_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_revenue_protected: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    estimated_time_saved_hours: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("store_id", "shift_number", name="uq_store_shift_number"),
    )

    store: Mapped["Store"] = relationship(back_populates="shifts")


class MetricsHourly(Base):
    """Pulled forward from the Database Architecture doc (brief Section 3.7 /
    7.1) — required by Sprint 1 Story 2's own acceptance criteria but absent
    from Sprint 1's own migration draft."""

    __tablename__ = "metrics_hourly"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    open_issues_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_protected_delta: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )


class StoreMemory(Base):
    """Pulled forward from the Database Architecture doc (brief Section 3.8 /
    7.1). Vector column is declared in the Alembic migration via raw DDL
    (pgvector's `vector` type has no first-class SQLAlchemy core type here);
    this ORM class covers the non-vector columns used by Sprint 1 code paths
    and is not used to write embeddings directly."""

    __tablename__ = "store_memories"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Sprint 2: AI Employee / Product Inspection / Morning Shift Report -----


class Agent(Base):
    """Registry of specialist AI Employees (Sprint 2 Feature 2 / Agent
    Registry). Not per-tenant merchant data — no `store_id` column, no RLS
    policy (see the Sprint 2 migration's docstring)."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identifier: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    domain_category: Mapped[str] = mapped_column(_ISSUE_CATEGORY_ENUM, nullable=False)
    system_prompt_template: Mapped[str] = mapped_column(String, nullable=False)
    model_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="GEMINI")
    model_name: Mapped[str] = mapped_column(String(100), nullable=False, default="gemini-2.5-pro")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Issue(Base):
    """Detected operational issue (Sprint 2 Feature 1-3 / Story 1-2)."""

    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(_ISSUE_CATEGORY_ENUM, nullable=False)
    severity: Mapped[str] = mapped_column(_ISSUE_SEVERITY_ENUM, nullable=False)
    status: Mapped[str] = mapped_column(_ISSUE_STATUS_ENUM, nullable=False, default="OPEN")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    evidence_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    affected_resources: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    revenue_impact_estimate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("confidence_score BETWEEN 0.0 AND 1.0", name="ck_issues_confidence_range"),
    )


class ShiftReport(Base):
    """Immutable, published Morning Shift Report (Sprint 2 Feature 4)."""

    __tablename__ = "shift_reports"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    executive_summary: Mapped[str] = mapped_column(String, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Sprint 3: AI Trust & Execution -----------------------------------------
#
# Column-for-column matches
# alembic/versions/0003_sprint3_trust_execution_schema.py. No relationships()
# are declared — none of the other ORM classes need back_populates from
# these, and this sprint's query patterns are all satisfied by plain FK
# columns via the repository layer.


class CognitiveTask(Base):
    """The Plan step's persisted output: a proposed, risk-assessed action
    for a single Issue, moving through PLANNED -> ... -> SUCCESS/FAILED/
    ROLLED_BACK (Sprint 3 lifecycle)."""

    __tablename__ = "cognitive_tasks"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    shift_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    execution_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(_RISK_LEVEL_ENUM, nullable=False)
    risk_reasoning: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(_TASK_STATUS_ENUM, nullable=False, default="PLANNED")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    confidence_assessment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    explanation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Approval(Base):
    """Merchant approval request/decision for a CognitiveTask requiring
    human sign-off. `approver_user_id` has no FK — no `users` table exists
    in this codebase yet (see the Sprint 3 migration's module docstring)."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cognitive_tasks.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(_APPROVAL_STATUS_ENUM, nullable=False, default="PENDING")
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    merchant_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_override_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Matches the migration's own `DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24
    # hours')` verbatim — without `server_default` here, SQLAlchemy sends an
    # explicit NULL for any Approval() constructed without expires_at set
    # (the repository's intended usage), overriding the column's DB-side
    # default and violating its NOT NULL constraint.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(CURRENT_TIMESTAMP + INTERVAL '24 hours')")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Execution(Base):
    """A single attempt to carry out a CognitiveTask's execution_plan
    against the Shopify Admin API."""

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cognitive_tasks.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(_EXECUTION_STATUS_ENUM, nullable=False, default="STARTED")
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Verification(Base):
    """Read-after-write verification result for a single Execution."""

    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("executions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cognitive_tasks.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(_VERIFICATION_STATUS_ENUM, nullable=False, default="PASSED")
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    result_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Rollback(Base):
    """A compensating action taken to revert a failed/rejected Execution.
    Reuses the `execution_status` Postgres enum type for its own `status`
    column — see the Sprint 3 migration's module docstring."""

    __tablename__ = "rollbacks"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("executions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cognitive_tasks.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(_EXECUTION_STATUS_ENUM, nullable=False, default="STARTED")
    reverted_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rollback_reason: Mapped[str] = mapped_column(Text, nullable=False)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Append-only audit trail entry. Never updated or deleted by
    application code — see `SqlAuditLogRepository`, which exposes only
    `append`/read methods, no update/delete."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cognitive_tasks.id", ondelete="SET NULL"), nullable=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    before_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    model_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        "timestamp", DateTime(timezone=True), server_default=func.now()
    )


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist ------------------


class ThemeSnapshot(Base):
    """Last known-good baseline for one watched theme file. See
    `alembic/versions/0005_sprint4_step3_theme_tracking_schema.py`'s own
    docstring for why `content` (not just a checksum) is stored, and why the
    baseline is never automatically re-written after first capture."""

    __tablename__ = "theme_snapshots"
    __table_args__ = (UniqueConstraint("store_id", "theme_id", "filename"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    theme_id: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_md5: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class Subscription(Base):
    """One row per subscribe attempt (append-style history table, same
    convention as `CognitiveTask`/`Approval`) — see
    `alembic/versions/0006_billing_subscriptions.py`'s own docstring."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    plan: Mapped[str] = mapped_column(_PLAN_TIER_ENUM, nullable=False, default="FREE")
    status: Mapped[str] = mapped_column(_SUBSCRIPTION_STATUS_ENUM, nullable=False, default="PENDING")
    shopify_charge_gid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    monthly_price_usd: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrackingSnapshot(Base):
    """Last known-good record of one expected script tag. See the same
    Step 3 migration's docstring for why no content/checksum column exists
    here — presence/absence of `src` is the entire detection signal."""

    __tablename__ = "tracking_snapshots"
    __table_args__ = (UniqueConstraint("store_id", "src"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    src: Mapped[str] = mapped_column(String(1000), nullable=False)
    display_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pattern_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
