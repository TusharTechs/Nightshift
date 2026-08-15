/**
 * Typed client for the Sprint 1 backend API surface. A thin fetch wrapper
 * today; swapping to the full tRPC client (Technical Blueprint 2.2) is a
 * drop-in replacement once the tRPC router exists server-side.
 */

import { getSessionToken } from "./app-bridge";

export interface StoreSnapshot {
  id: string;
  shopify_domain: string;
  store_name: string;
  currency_code: string;
  iana_timezone: string;
  health_score: number;
  autonomy_level: number;
  is_discovery_completed: boolean;
  installed_at: string;
  // Sprint 5 Phase 4: real reflection of the backend's Settings.demo_mode_enabled
  // — the Chaos Panel only renders when this is true, since the demo
  // endpoints themselves 404 otherwise (see `api/v1/demo.py`).
  demo_mode_enabled?: boolean;
}

// --- Sprint 2: Morning Shift Report -----------------------------------------

export type IssueSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface ShiftIssue {
  id: string;
  category: string;
  severity: IssueSeverity;
  status: string;
  title: string;
  description: string;
  revenue_impact_estimate: number;
  confidence_score: number;
  affected_resources: string[];
}

export interface ShiftMetrics {
  issues_detected: number;
  issues_resolved: number;
  estimated_revenue_protected_usd: number;
  time_saved_hours: number;
}

// --- Sprint 3: AI Trust & Execution -----------------------------------------

export type RiskLevel = "LEVEL_1_SAFE" | "LEVEL_2_MODERATE" | "LEVEL_3_HIGH" | "LEVEL_4_CRITICAL";
export type ApprovalDecisionAction = "APPROVE" | "REJECT" | "DEFER";

export interface PendingApproval {
  approval_id: string;
  issue_id: string;
  title: string;
  risk_level: RiskLevel;
  recommended_action: string;
  revenue_impact_usd: number;
  confidence_score: number;
  expires_at: string;
  // Sprint 5 Phase 3.2: the originating Issue's own description/evidence_data,
  // passed through unmodified — lets the Approval Center render Theme
  // Guardian's real baseline/current file diff before a merchant decides.
  // Most other action types' evidence_data is a lightweight fix_check
  // identifier; description defaults to "" server-side if genuinely blank.
  description?: string;
  evidence_data?: Record<string, unknown> | null;
  // Sprint 5 Phase 5: grounded verbatim from the task's own confidence
  // signal breakdown — see `domain/confidence.py::merchant_memory_note`.
  // Absent/null whenever this merchant has no real prior approval history
  // for this action type at this store.
  merchant_memory_note?: string | null;
}

export interface CompletedTask {
  task_id: string;
  // Sprint 4 Step 4: added so Chief Ops AI could match a completed task back
  // to its originating issue; reused by the Step 5 Counterfactual ROI Widget
  // to pull each resolved issue's real revenue_impact_estimate. `report_json`
  // is immutable once persisted (PRD Part 2), so a report published before
  // this field existed genuinely lacks it — optional, not just defensive.
  issue_id?: string;
  category: string;
  title: string;
  risk_level: RiskLevel;
  verified: boolean;
  verified_at: string | null;
}

export interface WorkLogEntry {
  id: string;
  timestamp: string;
  actor_type: string;
  actor_id: string;
  action: string;
  rationale: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  task_id: string | null;
  execution_id: string | null;
  // Sprint 4 Step 5: deterministic icon assigned server-side from `action`
  // (never client-derived) — see `domain/replay.py::icon_for_action`.
  icon: string;
}

export interface WorkLogListResponse {
  object: string;
  data: WorkLogEntry[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface ApprovalActionResponse {
  success: boolean;
  approval_id: string;
  task_id: string;
  status: string;
  message: string;
  audit_log_id: string | null;
  timestamp: string;
}

// --- Sprint 4 Step 4: Chief Ops AI / Executive Briefing ---------------------

/**
 * One specialist's "turn" in the Multi-Agent Handshake log (Vision doc,
 * Three Locked Additions #2). `icon` is assigned deterministically by the
 * backend (`domain/chief_ops.py::_icon_for_issue`), never by the LLM — ⚡ =
 * auto-executed this shift, 🧠 = awaiting a merchant's approval, 🟢 =
 * detected, informational only.
 */
export interface ChiefOpsTurn {
  issue_id: string;
  category: string;
  agent_name: string;
  icon: string;
  finding_title: string;
  finding_summary: string;
  severity: IssueSeverity | string;
  status: string;
  revenue_impact_estimate: number;
  timestamp: string;
  // Sprint 5 Phase 5: grounded verbatim from the originating CognitiveTask's
  // confidence signals — see `domain/confidence.py::merchant_memory_note`.
  merchant_memory_note?: string | null;
}

export interface ChiefOpsBriefing {
  turns: ChiefOpsTurn[];
  narrative: string;
  correlated: boolean;
  used_llm: boolean;
}

export interface LatestShift {
  shift_id: string;
  shift_number: number;
  status: string;
  started_at: string;
  completed_at: string | null;
  health_score: number;
  // Sprint 5 Phase 5: additive, joined in at request time from a second,
  // already-published ShiftReport row — see `api/v1/shifts.py::get_latest_shift`
  // and CONFLICTS.md item 55. Null on a store's very first shift.
  previous_shift_health_score?: number | null;
  executive_summary: string;
  metrics: ShiftMetrics;
  issues: ShiftIssue[];
  health_category_deductions: Record<string, number>;
  // Sprint 3: added to the `/shifts/latest` payload by
  // `shift_compiler.py::ShiftReportPayload.to_api_response`. Optional here
  // (and defaulted with `?? []` at call sites) so an older cached report
  // shape that predates Sprint 3 doesn't blow up the UI.
  pending_approvals?: PendingApproval[];
  completed_tasks?: CompletedTask[];
  // Sprint 4 Step 4: null until 1+ shifts have compiled under the new
  // pipeline, or when fewer than 2 specialists had findings this shift (see
  // `domain/chief_ops.py::deterministic_briefing` — still populated in that
  // case, just `used_llm: false`).
  chief_ops_briefing?: ChiefOpsBriefing | null;
}

export interface AskNightShiftResponse {
  answer: string;
  grounded_in_shift_ids: string[];
  used_llm: boolean;
  timestamp: string;
}

// --- Sprint 4 Step 5: Shift Replay + Counterfactual ROI Widget --------------

export interface ShiftReplayResponse {
  shift_id: string;
  shift_number: number;
  entries: WorkLogEntry[];
}

/**
 * `GET /api/v1/tasks/{task_id}` (Sprint 3, never previously called from the
 * frontend). The Step 5 Counterfactual ROI Widget is this endpoint's first
 * caller — it needs `issue_evidence.duplicate_created_at` (Checkout
 * Specialist's real Shopify `createdAt` per duplicate discount) and
 * `execution.completed_at`/`verification.verified_at` for a real, non-
 * fabricated exposure/resolution-time story (see CONFLICTS.md item 47).
 */
export interface TaskDetailResponse {
  task_id: string;
  issue_id: string;
  action_type: string;
  status: string;
  risk_level: RiskLevel;
  risk_reasoning: string;
  confidence_assessment: Record<string, unknown>;
  explanation: Record<string, unknown>;
  issue_evidence: Record<string, unknown> | null;
  execution: { completed_at: string | null; [key: string]: unknown } | null;
  verification: { verified_at: string | null; [key: string]: unknown } | null;
  rollback: Record<string, unknown> | null;
  approval: Record<string, unknown> | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export async function fetchStoreSnapshot(shopDomain: string): Promise<StoreSnapshot> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/stores/me`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

// --- Sprint 3: Approval Center + Work Log -----------------------------------

/**
 * GET /api/v1/approvals (Sprint 3 Approval Center). Stripe-style list
 * envelope (`{object, data, has_more, next_cursor}` — see
 * `app/api/v1/approvals.py::list_pending_approvals`); this client unwraps it
 * to the flat array the UI wants, since pagination isn't implemented on the
 * backend this sprint (`has_more` is always `false`).
 */
export async function fetchPendingApprovals(shopDomain: string): Promise<PendingApproval[]> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/approvals`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  const body: { data: PendingApproval[] } = await response.json();
  return body.data ?? [];
}

/**
 * POST /api/v1/approvals/{id}/action (Sprint 3 Approval Center decision
 * endpoint). `Idempotency-Key` is a required header on the backend (FastAPI
 * `Header(...)`, 422s if missing) — generated fresh per submission via
 * `crypto.randomUUID()` (broadly supported in modern browsers; this is an
 * embedded-admin app, not a legacy-browser target).
 *
 * On non-2xx this throws `ApiError` with `code` populated from the RFC 7807
 * body so callers can special-case `APPROVAL_ALREADY_DECIDED` (409) and
 * `TASK_APPROVAL_EXPIRED` (409) with friendly messages instead of a raw dump.
 */
export async function submitApprovalAction(
  shopDomain: string,
  approvalId: string,
  body: {
    action: ApprovalDecisionAction;
    rejection_reason?: string;
    execution_override_params?: Record<string, unknown>;
  }
): Promise<ApprovalActionResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/approvals/${approvalId}/action`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * GET /api/v1/work-log (Sprint 3 Work Log). `opts.startingAfter` maps to the
 * `starting_after` query param the real route expects (an ISO-8601
 * timestamp cursor — see `app/api/v1/work_log.py`).
 */
export async function fetchWorkLog(
  shopDomain: string,
  opts?: { startingAfter?: string }
): Promise<WorkLogListResponse> {
  const token = await getSessionToken();

  const params = new URLSearchParams();
  if (opts?.startingAfter) {
    params.set("starting_after", opts.startingAfter);
  }
  const query = params.toString();

  const response = await fetch(`${API_BASE_URL}/api/v1/work-log${query ? `?${query}` : ""}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * GET /api/v1/shifts/latest (Sprint 2 Feature 4 / Story 3). Returns `null`
 * for the documented "no shifts completed yet" case (RFC 7807 `code:
 * NO_COMPLETED_SHIFT`, HTTP 404) rather than throwing — the Operations
 * Center renders this as a normal empty state, not an error banner.
 */
export async function fetchLatestShift(shopDomain: string): Promise<LatestShift | null> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/shifts/latest`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (response.status === 404) {
    const problem = await response.json().catch(() => null);
    if (problem?.code === "NO_COMPLETED_SHIFT") {
      return null;
    }
  }

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * GET /api/v1/shifts/{shift_id}/replay (Sprint 4 Step 5 Shift Replay, one
 * specific known shift). Not the frontend's default call path today — see
 * `fetchLatestActiveShiftReplay` below — kept for a future "browse shift
 * history" feature.
 */
export async function fetchShiftReplay(shopDomain: string, shiftId: string): Promise<ShiftReplayResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/shifts/${shiftId}/replay`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * GET /api/v1/shifts/replay/latest-active (Sprint 5 Phase 1.2). Walks
 * backward through recent shifts server-side and returns the newest one
 * with real activity — this is what the Shift Replay scrubber actually
 * calls, so a clean all-clear shift never makes the scrubber look broken.
 */
export async function fetchLatestActiveShiftReplay(shopDomain: string): Promise<ShiftReplayResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/shifts/replay/latest-active`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * GET /api/v1/tasks/{task_id} (Sprint 3, first called from the frontend by
 * the Step 5 Counterfactual ROI Widget — see `TaskDetailResponse`'s own
 * docstring above for why).
 */
export async function fetchTaskDetail(shopDomain: string, taskId: string): Promise<TaskDetailResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/tasks/${taskId}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

// --- Sprint 5 Phase 4: Demo Incident Control Panel ("Chaos Panel") ----------

export type DemoScenarioId = "midnight_pricing_disaster" | "rogue_developer_theme_break" | "catalog_seo_collapse";

export interface DemoIncidentResponse {
  success: boolean;
  scenario_id: string;
  created_discount_codes: string[];
  timestamp: string;
  notes: string | null;
  // The Celery task id of the background shift this same request also
  // dispatched (see `api/v1/demo.py`) — never null on success, since the
  // route always dispatches one after a scenario actually triggers.
  shift_dispatch_task_id: string | null;
}

/**
 * POST /api/v1/demo/incidents/{scenario_id} (Sprint 4 Step 1; Sprint 5
 * Phase 4 wires all three named scenarios and adds the background-shift
 * dispatch). 404s with code `DEMO_MODE_DISABLED` if the backend's
 * `Settings.demo_mode_enabled` is off — the Chaos Panel itself is only
 * rendered when `StoreSnapshot.demo_mode_enabled` is true, so this should
 * only happen if the flag flips between page load and the click.
 */
export async function triggerDemoIncident(
  shopDomain: string,
  scenarioId: DemoScenarioId
): Promise<DemoIncidentResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/demo/incidents/${scenarioId}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
    },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}

/**
 * POST /api/v1/ask (Sprint 4 Step 4 "Ask NightShift"). Stateless per call —
 * no conversation history is persisted server-side (see
 * `ask_nightshift.py`'s own docstring), so the caller owns any chat-turn
 * history it wants to keep in memory.
 */
export async function askNightShift(shopDomain: string, question: string): Promise<AskNightShiftResponse> {
  const token = await getSessionToken();

  const response = await fetch(`${API_BASE_URL}/api/v1/ask`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Shopify-Shop-Domain": shopDomain,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new ApiError(
      problem?.detail ?? `Request failed with status ${response.status}`,
      response.status,
      problem?.code
    );
  }

  return response.json();
}
