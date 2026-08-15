import Image from "next/image";

import { AIActivityBadge, type AIActivityState } from "./AIActivityBadge";

const SPECIALIST_COUNT = 4; // Theme Guardian, Checkout Specialist, Tracking
// Specialist, Product Quality — a fixed roster count (see
// `domain/chief_ops.py::CATEGORY_DISPLAY_NAMES`), not derived from any
// per-shift activity, so it's always true regardless of what ran tonight.

/** A single `[ LABEL ]` Command Center status badge (Sprint 5 Phase 2.1) —
 * monospace, bracketed, so the header reads like an operations console
 * rather than a settings page. Every badge here renders a real, currently-
 * true fact (never a static "ACTIVE" claim with nothing behind it). */
function CommandBadge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "active" | "neutral" }) {
  const toneClasses =
    tone === "active"
      ? "border-emerald-800 bg-emerald-950 text-emerald-300"
      : "border-gray-700 bg-gray-900 text-gray-300";
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-1 font-mono text-[11px] font-medium tracking-wide ${toneClasses}`}
    >
      [ {children} ]
    </span>
  );
}

/**
 * Persistent global header (Section 1.10, Components #1): store selector +
 * AI activity indicator. Multi-store dropdown is explicitly deferred
 * (Section 1.22, Future Considerations — Sprint 6); Sprint 1 shows the
 * single connected store's domain as static text.
 *
 * Sprint 5 Phase 2.1 ("Command Center" polish): adds a row of bracketed
 * status badges below the original title/domain row. `chiefOpsActive`
 * reflects whether the latest shift's `chief_ops_briefing` actually ran a
 * real LLM synthesis pass (`used_llm: true`) — not just whether the
 * briefing object exists. Since Sprint 5 Phase 1.3, `chief_ops_briefing` is
 * a populated object on every shift with 1+ findings regardless of outcome
 * (`deterministic_briefing()` never returns null), so checking for its mere
 * presence would make this badge read "ACTIVE" permanently after a store's
 * first shift, never "STANDBY" — confirmed live against real shift data
 * before this fix. Binding to `used_llm` instead makes it a genuine,
 * per-shift-varying fact: STANDBY on a fallback shift (budget exhausted, a
 * bad structured response, or zero findings), ACTIVE only when Chief Ops
 * genuinely narrated something this shift. `latestShiftNumber`/
 * `latestShiftStatus` are the same
 * shift already rendered elsewhere on this page — omitted entirely (not
 * shown as "Shift #0") until a first shift has actually run.
 */
export function OperationsHeader({
  shopifyDomain,
  aiActivityState,
  chiefOpsActive,
  latestShiftNumber,
  latestShiftStatus,
}: {
  shopifyDomain: string;
  aiActivityState: AIActivityState;
  chiefOpsActive?: boolean;
  latestShiftNumber?: number | null;
  latestShiftStatus?: string | null;
}) {
  return (
    <header className="space-y-3 border-b border-gray-200 pb-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Image src="/logo.png" alt="" width={28} height={28} priority />
          <div>
            <p className="text-lg font-semibold text-gray-900">NightShift AI</p>
            <p className="text-sm text-gray-500">{shopifyDomain}</p>
          </div>
        </div>
        <AIActivityBadge state={aiActivityState} />
      </div>

      <div className="flex flex-wrap gap-2" aria-label="Operations status">
        <CommandBadge tone={chiefOpsActive ? "active" : "neutral"}>
          CHIEF OPS AI: {chiefOpsActive ? "ACTIVE" : "STANDBY"}
        </CommandBadge>
        <CommandBadge>{SPECIALIST_COUNT} SPECIALISTS ON WATCH</CommandBadge>
        {latestShiftNumber != null ? (
          <CommandBadge tone={latestShiftStatus === "COMPLETED" ? "active" : "neutral"}>
            SHIFT #{latestShiftNumber} {latestShiftStatus === "COMPLETED" ? "COMPLETE" : "IN PROGRESS"}
          </CommandBadge>
        ) : null}
      </div>
    </header>
  );
}
