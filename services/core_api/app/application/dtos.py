"""Application-layer DTOs — the shapes returned across the API boundary.

Field names are snake_case to match the API Contract Specification's own
convention and Sprint 1's own /stores/me worked example (brief Section 7.4:
standardize on snake_case, disregard SATDD's camelCase illustrations).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class StoreSnapshotResponse(BaseModel):
    id: UUID
    shopify_domain: str
    store_name: str
    currency_code: str
    iana_timezone: str
    health_score: int
    autonomy_level: int
    is_discovery_completed: bool
    installed_at: datetime
    # Sprint 5 Phase 4: lets the frontend conditionally render the Chaos
    # Panel only when the backend's Demo Incident Generator is actually
    # reachable — mirrors `Settings.demo_mode_enabled`, the same flag
    # `POST /api/v1/demo/incidents/{scenario_id}` itself is gated on
    # (`DemoModeDisabledProblem`), so the two can never disagree.
    demo_mode_enabled: bool = False


# --- Sprint 3: AI Trust & Execution -----------------------------------------
#
# Field names are snake_case and verified verbatim against the API Contract
# Specification's worked examples this session — not negotiable without a
# spec change.


class ApprovalActionRequest(BaseModel):
    action: str  # APPROVE, REJECT, DEFER — validated against ApprovalAction enum in the route, not here
    rejection_reason: str | None = None
    execution_override_params: dict | None = None


class ApprovalActionResponse(BaseModel):
    success: bool
    approval_id: UUID
    task_id: UUID
    status: str
    message: str
    audit_log_id: UUID | None = None
    timestamp: datetime


class RollbackRequest(BaseModel):
    rollback_reason: str


class RollbackResponse(BaseModel):
    success: bool
    task_id: UUID
    status: str
    reverted_state: dict
    audit_log_id: UUID | None = None
    timestamp: datetime


class PendingApprovalDTO(BaseModel):
    approval_id: UUID
    issue_id: UUID
    title: str
    risk_level: str
    recommended_action: str
    revenue_impact_usd: float
    confidence_score: float
    expires_at: datetime
    # Sprint 5 Phase 3.2: the originating Issue's own `description` and raw
    # `evidence_data`, passed through unmodified (same never-invent-data
    # convention as `TaskDetailResponse.issue_evidence`) — lets the Approval
    # Center render Theme Guardian's actual baseline/current file content and
    # plain-English explanation before a merchant decides, without a new
    # endpoint or an extra round-trip through `GET /tasks/{id}`. Genuinely
    # optional: most approvals' evidence_data is a lightweight fix_check
    # identifier (see `checkout_specialist.py`'s own convention), only Theme
    # Guardian's carries full file content.
    description: str = ""
    evidence_data: dict | None = None
    # Sprint 5 Phase 5: grounded verbatim from `task.confidence_assessment`'s
    # own `merchant_acceptance_history` signal (see
    # `domain/confidence.py::merchant_memory_note`) — None whenever this
    # merchant has no real prior approval history for this action type at
    # this store, never a fabricated "personalized" note.
    merchant_memory_note: str | None = None


class CompletedTaskDTO(BaseModel):
    task_id: UUID
    category: str
    title: str
    risk_level: str
    verified: bool
    verified_at: datetime | None


class TaskDetailResponse(BaseModel):
    task_id: UUID
    issue_id: UUID
    action_type: str
    status: str
    risk_level: str
    risk_reasoning: str
    confidence_assessment: dict
    explanation: dict
    # The originating Issue's raw evidence_data (e.g. Checkout Specialist's
    # `duplicate_created_at` — the real Shopify createdAt timestamp per
    # duplicate discount, needed for the frontend's "live for N hours before
    # NightShift deactivated it" exposure-duration story). Same
    # never-invent-data convention as `execution.request_payload`/
    # `verification.result_data` being passed through raw rather than
    # re-derived.
    issue_evidence: dict | None = None
    execution: dict | None = None
    verification: dict | None = None
    rollback: dict | None = None
    approval: dict | None = None


class WorkLogEntryDTO(BaseModel):
    id: UUID
    timestamp: datetime
    actor_type: str
    actor_id: str
    action: str
    rationale: str
    before_state: dict | None = None
    after_state: dict | None = None
    task_id: UUID | None = None
    execution_id: UUID | None = None
    # Sprint 4 Step 5: deterministic 🟢/⚡/🧠/⚠️/🎬 icon derived from `action`
    # via `domain/replay.py::icon_for_action` — never LLM-assigned, same
    # discipline as Chief Ops's per-issue icon (CONFLICTS.md items 43/46).
    icon: str = "🔹"


class WorkLogListResponse(BaseModel):
    object: str = "list"
    data: list[WorkLogEntryDTO]
    has_more: bool
    next_cursor: str | None = None


# --- Sprint 4 Step 1: Demo Incident Generator --------------------------------


class DemoIncidentResponse(BaseModel):
    success: bool = True
    scenario_id: str
    created_discount_codes: list[str]
    timestamp: datetime
    # Sprint 4 Step 3: surfaces scenario-specific caveats (e.g. Scenario 2's
    # theme-file half not being auto-triggerable) directly to the caller
    # rather than silently under-delivering — see
    # `TriggerDemoIncident.DemoIncidentResult`'s own docstring.
    notes: str | None = None
    # Sprint 5 Phase 4: the Celery task id of the `tasks.inspect_catalog`
    # run this same request also dispatched — lets the Chaos Panel confirm a
    # background shift was actually enqueued, not just that the incident
    # itself was created.
    shift_dispatch_task_id: str | None = None


# --- Sprint 4 Step 4: Chief Ops AI / Ask NightShift --------------------------


class AskNightShiftRequest(BaseModel):
    question: str


class AskNightShiftResponse(BaseModel):
    answer: str
    # Which persisted ShiftReport rows this answer was actually grounded in —
    # lets the frontend link back to "Shift #N" the way `issue_evidence`
    # already lets Work Log entries link back to their originating Issue.
    grounded_in_shift_ids: list[UUID]
    used_llm: bool
    timestamp: datetime


# --- Sprint 4 Step 5: Shift Replay --------------------------------------------


class ShiftReplayResponse(BaseModel):
    shift_id: UUID
    shift_number: int
    entries: list[WorkLogEntryDTO]


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class PlanFeatureSetDTO(BaseModel):
    plan: str
    display_name: str
    monthly_price_usd: float
    features: list[str]


class BillingPlansResponse(BaseModel):
    plans: list[PlanFeatureSetDTO]


class BillingSubscribeRequest(BaseModel):
    plan: str  # PRO or BUSINESS — validated against PlanTier in the route, not here


class BillingSubscribeResponse(BaseModel):
    subscription_id: UUID
    plan: str
    status: str
    confirmation_url: str
    monthly_price_usd: float
    timestamp: datetime


class BillingStatusResponse(BaseModel):
    plan: str
    status: str
    monthly_price_usd: float
    activated_at: datetime | None = None
    cancelled_at: datetime | None = None
    shopify_charge_gid: str | None = None
