import { categoryHealthPercent, type HealthCategoryKey } from "../../lib/health-categories";

/**
 * Sprint 5 Phase 1.1: replaces the dashboard's old "Not yet monitored"
 * placeholder tiles now that a real specialist backs each category shown
 * here. Renders a live health meter (percentage + mini bar) sourced from
 * the latest shift's own `health_category_deductions` — never a
 * placeholder once a shift has run.
 *
 * Sprint 5 Phase 5 micro-polish: an optional specialist avatar (`icon`)
 * renders next to the label, matching the same identity badges already used
 * in the Work Log/Shift Replay/Executive Briefing (`lib/specialist-identity.ts`)
 * — purely presentational, no new data.
 */
export function CategoryHealthTile({
  label,
  icon,
  category,
  deductions,
}: {
  label: string;
  icon?: string;
  category: HealthCategoryKey;
  deductions: Record<string, number> | null;
}) {
  const titleLine = (
    <p className="flex items-center gap-1.5 text-sm text-gray-500">
      {icon ? <span aria-hidden="true">{icon}</span> : null}
      {label}
    </p>
  );

  if (!deductions) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        {titleLine}
        <p className="mt-1 text-xl font-semibold text-gray-900">—</p>
        <p className="mt-1 text-xs text-gray-400">Awaiting first shift</p>
      </div>
    );
  }

  const pct = categoryHealthPercent(deductions, category);
  const barColor = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      {titleLine}
      <p className="mt-1 text-xl font-semibold text-gray-900">{pct}% healthy</p>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
