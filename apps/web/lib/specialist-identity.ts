/**
 * Specialist visual identity (Sprint 5 Phase 2.3) — a distinct, high-tech
 * avatar per specialist AI employee, shared across the Work Log, Shift
 * Replay, and Executive Briefing surfaces. Purely presentational: an emoji
 * badge next to data every surface already renders, never a new fact.
 *
 * The action_type -> category mapping below is not invented — it mirrors
 * exactly what each backend Agent already declares as its own
 * `action_type`/`domain_category` (see `domain/risk.py::ACTION_RISK_LEVELS`
 * and each `domain/agents/*.py`'s own `domain_category`). An `actor_id` the
 * frontend doesn't recognize (a merchant's own action, or a demo-scenario
 * trigger id) simply gets no avatar rather than a guessed one.
 */

export type SpecialistCategory = "PRODUCT_QUALITY" | "DISCOUNT" | "CHECKOUT" | "PIXEL_TRACKING";

export const SPECIALIST_AVATARS: Record<SpecialistCategory, string> = {
  PRODUCT_QUALITY: "📦",
  DISCOUNT: "💳",
  // Reused CHECKOUT category, same as the backend's CATEGORY_DISPLAY_NAMES
  // — Theme Guardian, not a literal "checkout" specialist.
  CHECKOUT: "🛡️",
  PIXEL_TRACKING: "🎯",
};

// Mirrors `domain/chief_ops.py::CATEGORY_DISPLAY_NAMES` exactly, so a
// Chief Ops turn's `agent_name` string resolves to the same avatar/label
// the Work Log and Shift Replay use for the same specialist.
export const SPECIALIST_LABELS: Record<SpecialistCategory, string> = {
  PRODUCT_QUALITY: "Product Quality Employee",
  DISCOUNT: "Checkout Specialist",
  CHECKOUT: "Theme Guardian",
  PIXEL_TRACKING: "Tracking Specialist",
};

export const CHIEF_OPS_AVATAR = "🧠";
export const CHIEF_OPS_LABEL = "Chief Operations AI";

// audit_logs.actor_id is the underlying `action_type` for every AI-driven
// entry (see `execute_cognitive_task.py`/`plan_cognitive_tasks.py`, etc. —
// all pass `actor_id=task.action_type` or `actor_id=proposed.action_type`).
const ACTION_TYPE_TO_CATEGORY: Record<string, SpecialistCategory> = {
  GENERATE_ALT_TEXT: "PRODUCT_QUALITY",
  REWRITE_PRODUCT_DESCRIPTION: "PRODUCT_QUALITY",
  DEACTIVATE_DUPLICATE_DISCOUNT: "DISCOUNT",
  GENERATE_THEME_RESTORE_GUIDE: "CHECKOUT",
  RECREATE_TRACKING_SCRIPT_TAG: "PIXEL_TRACKING",
};

const AGENT_NAME_TO_CATEGORY: Record<string, SpecialistCategory> = {
  "Product Quality Employee": "PRODUCT_QUALITY",
  "Checkout Specialist": "DISCOUNT",
  "Theme Guardian": "CHECKOUT",
  "Tracking Specialist": "PIXEL_TRACKING",
};

export function categoryForActionType(actionType: string): SpecialistCategory | null {
  return ACTION_TYPE_TO_CATEGORY[actionType] ?? null;
}

export function avatarForActionType(actionType: string): string | null {
  const category = categoryForActionType(actionType);
  return category ? SPECIALIST_AVATARS[category] : null;
}

export function labelForActionType(actionType: string): string | null {
  const category = categoryForActionType(actionType);
  return category ? SPECIALIST_LABELS[category] : null;
}

export function avatarForAgentName(agentName: string): string | null {
  const category = AGENT_NAME_TO_CATEGORY[agentName];
  return category ? SPECIALIST_AVATARS[category] : null;
}
