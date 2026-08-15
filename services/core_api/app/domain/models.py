"""Pure domain aggregates (Pydantic v2, no ORM/framework imports).

These mirror the persistence schema but are what use cases and domain
services operate on — the infrastructure layer maps to/from these at the
repository boundary.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import (
    ApprovalStatus,
    ExecutionStatus,
    IssueCategory,
    IssueSeverity,
    IssueStatus,
    PlanTier,
    RiskLevel,
    SubscriptionStatus,
    TaskStatus,
    VerificationStatus,
)


class Organization(BaseModel):
    id: UUID
    name: str
    slug: str
    billing_email: str
    plan_tier: str = "GROWTH"
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class Store(BaseModel):
    id: UUID
    organization_id: UUID
    shopify_domain: str
    myshopify_domain: str
    store_name: str
    currency_code: str = "USD"
    iana_timezone: str = "America/New_York"
    autonomy_level: int = 1
    is_autonomous_execution_enabled: bool = True
    health_score: int = Field(default=100, ge=0, le=100)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class StoreToken(BaseModel):
    id: UUID
    store_id: UUID
    access_token_encrypted: str
    scopes: list[str]
    token_type: str = "offline"
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Shift(BaseModel):
    id: UUID
    store_id: UUID
    shift_number: int
    status: str = "SCHEDULED"
    started_at: datetime
    completed_at: datetime | None = None
    issues_detected_count: int = 0
    issues_resolved_count: int = 0
    pending_approvals_count: int = 0
    estimated_revenue_protected: float = 0.0
    estimated_time_saved_hours: float = 0.0
    created_at: datetime


class MetricsHourly(BaseModel):
    id: UUID
    store_id: UUID
    health_score: int
    open_issues_count: int
    revenue_protected_delta: float = 0.0
    recorded_at: datetime


# --- Sprint 2: AI Employee / Product Inspection / Morning Shift Report -----


class Agent(BaseModel):
    id: UUID
    identifier: str
    display_name: str
    description: str
    domain_category: IssueCategory
    system_prompt_template: str
    model_provider: str = "GEMINI"
    model_name: str = "gemini-2.5-pro"
    is_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class Issue(BaseModel):
    id: UUID
    store_id: UUID
    shift_id: UUID | None = None
    agent_id: UUID | None = None
    category: IssueCategory
    severity: IssueSeverity
    status: IssueStatus = IssueStatus.OPEN
    title: str
    description: str
    evidence_data: dict = Field(default_factory=dict)
    affected_resources: list[str] = Field(default_factory=list)
    revenue_impact_estimate: float = 0.0
    confidence_score: float = Field(ge=0.0, le=1.0)
    created_at: datetime
    updated_at: datetime


class ShiftReport(BaseModel):
    id: UUID
    shift_id: UUID
    store_id: UUID
    executive_summary: str
    report_json: dict
    published_at: datetime
    created_at: datetime


# --- Sprint 3: AI Trust & Execution -----------------------------------------


class CognitiveTask(BaseModel):
    id: UUID
    issue_id: UUID
    store_id: UUID
    shift_id: UUID
    agent_id: UUID
    action_type: str
    execution_plan: dict
    risk_level: RiskLevel
    risk_reasoning: str
    status: TaskStatus = TaskStatus.PLANNED
    idempotency_key: str
    confidence_assessment: dict = Field(default_factory=dict)
    explanation: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class Approval(BaseModel):
    id: UUID
    task_id: UUID
    issue_id: UUID
    store_id: UUID
    status: ApprovalStatus = ApprovalStatus.PENDING
    approver_user_id: UUID | None = None
    merchant_rationale: str | None = None
    execution_override_params: dict | None = None
    expires_at: datetime
    decided_at: datetime | None = None
    created_at: datetime


class Execution(BaseModel):
    id: UUID
    task_id: UUID
    store_id: UUID
    status: ExecutionStatus = ExecutionStatus.STARTED
    request_payload: dict
    response_payload: dict | None = None
    error_log: str | None = None
    execution_duration_ms: int | None = None
    retry_count: int = 0
    started_at: datetime
    completed_at: datetime | None = None


class Verification(BaseModel):
    id: UUID
    execution_id: UUID
    task_id: UUID
    store_id: UUID
    status: VerificationStatus = VerificationStatus.PASSED
    method: str
    result_data: dict
    verified_at: datetime


class Rollback(BaseModel):
    id: UUID
    execution_id: UUID
    task_id: UUID
    store_id: UUID
    status: ExecutionStatus = ExecutionStatus.STARTED
    reverted_state: dict
    rollback_reason: str
    error_log: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class AuditLogEntry(BaseModel):
    id: UUID
    store_id: UUID
    shift_id: UUID | None = None
    task_id: UUID | None = None
    execution_id: UUID | None = None
    actor_type: str
    actor_id: str
    action: str
    before_state: dict | None = None
    after_state: dict | None = None
    rationale: str
    model_identifier: str | None = None
    prompt_version: str | None = None
    timestamp: datetime


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist ------------------


class ThemeSnapshotRecord(BaseModel):
    id: UUID
    store_id: UUID
    theme_id: str
    filename: str
    content: str
    checksum_md5: str
    captured_at: datetime
    updated_at: datetime


class TrackingSnapshotRecord(BaseModel):
    id: UUID
    store_id: UUID
    src: str
    display_scope: str | None = None
    pattern_name: str | None = None
    captured_at: datetime
    updated_at: datetime


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class Subscription(BaseModel):
    """One row per subscribe attempt (append-style history, same convention
    as CognitiveTask/Approval) — `SubscriptionRepository.get_current_for_store`
    treats the most-recently-created row for a store as its current plan.
    See `alembic/versions/0006_billing_subscriptions.py`'s own docstring for
    the full reasoning."""

    id: UUID
    store_id: UUID
    plan: PlanTier
    status: SubscriptionStatus
    shopify_charge_gid: str | None = None
    monthly_price_usd: float = 0.0
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None = None
    cancelled_at: datetime | None = None
