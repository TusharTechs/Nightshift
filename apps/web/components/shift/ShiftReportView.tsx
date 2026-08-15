"use client";

import type { LatestShift } from "../../lib/api";
import { CounterfactualRoiWidget } from "./CounterfactualRoiWidget";
import { ExecutiveBriefing } from "./ExecutiveBriefing";
import { HealthBreakdownWidget } from "./HealthBreakdownWidget";
import { IssueCard } from "./IssueCard";
import { ShiftReplay } from "./ShiftReplay";
import { TonightsImpactWidget } from "./TonightsImpactWidget";
import { StatTile } from "../shared/StatTile";

/**
 * Morning Shift Report view (Sprint 2 Feature 5 / Story 3). Supports all
 * four required screen states per NightShift AI's engineering standard:
 * loading, empty ("no shifts yet"), success, and error.
 */

function formatUsd(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function formatCompletedAt(iso: string | null): string {
  if (!iso) return "in progress";
  return new Date(iso).toLocaleString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function ShiftReportViewLoading() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading Morning Shift Report">
      <div className="h-8 w-2/3 animate-pulse rounded bg-gray-100" />
      <div className="h-20 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="h-24 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
        <div className="h-24 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
        <div className="h-24 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
      </div>
    </div>
  );
}

export function ShiftReportViewEmpty() {
  return (
    <div className="flex min-h-[30vh] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 p-8 text-center">
      <p className="text-lg font-medium text-gray-900">No Morning Shift Report yet</p>
      <p className="max-w-sm text-sm text-gray-500">
        Your Product Quality Employee runs its first inspection shift overnight.
        Check back after your next scheduled shift completes.
      </p>
    </div>
  );
}

export function ShiftReportViewError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  // Story 3 failure case, verbatim: "Database query fails → Displays RFC
  // 7807 error banner with manual refresh button."
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-6 text-center"
    >
      <p className="font-medium text-red-800">Couldn&apos;t load the Morning Shift Report</p>
      <p className="text-sm text-red-700">{message}</p>
      <button
        onClick={onRetry}
        className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
      >
        Retry
      </button>
    </div>
  );
}

export function ShiftReportView({ shift, shopDomain }: { shift: LatestShift; shopDomain: string | null }) {
  const sortedIssues = [...shift.issues].sort(
    (a, b) => b.revenue_impact_estimate - a.revenue_impact_estimate
  );

  return (
    <section aria-labelledby="morning-shift-report-heading" className="space-y-4">
      <header>
        <h2 id="morning-shift-report-heading" className="text-lg font-semibold text-gray-900">
          Morning Shift Report #{shift.shift_number}
        </h2>
        <p className="text-sm text-gray-500">
          Shift completed: {formatCompletedAt(shift.completed_at)}
        </p>
      </header>

      {/* Sprint 5 Phase 5: "Tonight's Impact" is the new top-of-report
          at-a-glance summary — four grounded metrics only, no fabricated
          conversion-rate estimate (CONFLICTS.md item 56). */}
      <TonightsImpactWidget shift={shift} />

      {/* Sprint 5 Phase 3.1: the Counterfactual ROI banner sits directly
          below it — the roadmap's own framing is that this is the single
          most persuasive "here's what NightShift is worth" moment, and it
          was previously buried below the full issue list. */}
      {shopDomain && (shift.completed_tasks ?? []).length > 0 ? (
        <CounterfactualRoiWidget shopDomain={shopDomain} shift={shift} />
      ) : null}

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <p className="text-sm text-gray-700">{shift.executive_summary}</p>
      </div>

      {/* Sprint 4 Step 4: Chief Ops AI's cross-agent synthesis, rendered as
          its own "polished summary view" surface per the Vision doc's Phase
          B (`chief_ops_briefing` is null on shifts compiled before this
          shipped, or absent from an older cached report shape). */}
      {shift.chief_ops_briefing ? <ExecutiveBriefing briefing={shift.chief_ops_briefing} /> : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatTile label="Store Health Score" value={`${shift.health_score}/100`} />
        <StatTile
          label="Revenue Protected"
          value={formatUsd(shift.metrics.estimated_revenue_protected_usd)}
        />
        <StatTile label="Operational Issues" value={String(shift.metrics.issues_detected)} />
      </div>

      {sortedIssues.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <ul className="space-y-3 lg:col-span-2" aria-label="Detected issues, sorted by revenue impact">
            {sortedIssues.map((issue, index) => (
              <IssueCard key={issue.id} issue={issue} index={index} />
            ))}
          </ul>
          <HealthBreakdownWidget deductions={shift.health_category_deductions} />
        </div>
      ) : (
        // Sprint 2 Feature 4 edge case, verbatim: "Zero issues detected
        // during a shift; report renders a clean 'All Systems Operational'
        // reassurance state."
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-center text-sm font-medium text-emerald-800">
          ✓ All Systems Operational — no issues detected this shift.
        </div>
      )}

      {/* Sprint 4 Step 5: Shift Replay scrubber over whichever recent shift
          actually has audit_logs activity (Sprint 5 Phase 1.2 fallback). */}
      <ShiftReplay shopDomain={shopDomain} />
    </section>
  );
}
