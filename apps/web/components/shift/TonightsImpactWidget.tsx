import type { LatestShift } from "../../lib/api";

/**
 * "Tonight's Impact" widget (Sprint 5 Phase 5). Four grounded, real metrics
 * only — no fabricated conversion-rate estimate (see CONFLICTS.md item 56):
 * this shift's real `metrics.issues_resolved` /
 * `metrics.estimated_revenue_protected_usd`, a real Store Health Delta
 * (current `health_score` vs. the previous shift's, via the additive
 * `previous_shift_health_score` field — `api/v1/shifts.py::get_latest_shift`),
 * and a Merchant Actions Status line built from the real
 * `pending_approvals[]`/`completed_tasks[]` counts already on this payload.
 */

function formatUsd(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function healthDeltaText(current: number, previous: number | null | undefined): string {
  if (previous === null || previous === undefined) {
    return `${current}/100 — first shift, no prior score to compare yet.`;
  }
  const delta = current - previous;
  const sign = delta > 0 ? "+" : "";
  return `${previous} ➔ ${current} (${sign}${delta} pts)`;
}

function merchantActionsText(shift: LatestShift): string {
  const pendingCount = (shift.pending_approvals ?? []).length;
  const verifiedCount = (shift.completed_tasks ?? []).filter((t) => t.verified).length;
  const approvalPart = `${pendingCount} Approval${pendingCount === 1 ? "" : "s"} Required`;
  // Deliberately "Fixes Verified", not "Autonomous Fixes Verified" — nothing
  // in `completed_tasks[]` distinguishes a fully-autonomous execution from
  // one that ran only after a merchant approved it, so claiming "autonomous"
  // here would be an unverifiable label this data can't actually back up.
  const verifiedPart = `${verifiedCount} Fix${verifiedCount === 1 ? "" : "es"} Verified`;
  return `${approvalPart} • ${verifiedPart}`;
}

export function TonightsImpactWidget({ shift }: { shift: LatestShift }) {
  const issuesFixed = shift.metrics.issues_resolved;

  return (
    <section
      aria-labelledby="tonights-impact-heading"
      className="space-y-3 rounded-xl border-2 border-emerald-200 bg-emerald-50/70 p-4 shadow-sm sm:p-5"
    >
      <h2 id="tonights-impact-heading" className="flex items-center gap-2 text-base font-semibold text-emerald-900">
        <span aria-hidden="true">🌙</span> Tonight&apos;s Impact
      </h2>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border border-emerald-100 bg-white p-3">
          <p className="text-xs text-gray-500">Issues Fixed Overnight</p>
          <p className="mt-1 text-xl font-semibold text-gray-900">{issuesFixed}</p>
        </div>
        <div className="rounded-lg border border-emerald-100 bg-white p-3">
          <p className="text-xs text-gray-500">Revenue Protected</p>
          <p className="mt-1 text-xl font-semibold text-emerald-700">
            {formatUsd(shift.metrics.estimated_revenue_protected_usd)}
          </p>
        </div>
        <div className="rounded-lg border border-emerald-100 bg-white p-3">
          <p className="text-xs text-gray-500">Store Health</p>
          <p className="mt-1 text-lg font-semibold text-gray-900">
            {healthDeltaText(shift.health_score, shift.previous_shift_health_score)}
          </p>
        </div>
        <div className="rounded-lg border border-emerald-100 bg-white p-3">
          <p className="text-xs text-gray-500">Merchant Actions</p>
          <p className="mt-1 text-sm font-semibold text-gray-900">{merchantActionsText(shift)}</p>
        </div>
      </div>
    </section>
  );
}
