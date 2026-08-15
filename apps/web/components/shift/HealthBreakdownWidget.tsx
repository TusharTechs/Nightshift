/**
 * Store Health Score category breakdown (Sprint 2 Feature 5 / PRD Part 2
 * Store Health page: "each category shows Current Score ... "). Renders the
 * per-category point deductions the backend's deterministic Store Health
 * Engine computed (`domain/health.py::calculate_store_health`), so a
 * merchant can see exactly why the score is what it is instead of just a
 * single opaque number.
 *
 * Sprint 5: category caps/labels moved to `lib/health-categories.ts` so the
 * dashboard's category health tiles (`CategoryHealthTile.tsx`) and this
 * widget share one table instead of two independently-maintained copies.
 */

import { CATEGORY_CAPS, CATEGORY_KEYS, CATEGORY_LABELS } from "../../lib/health-categories";

export function HealthBreakdownWidget({
  deductions,
}: {
  deductions: Record<string, number>;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-900">Health Score Breakdown</h3>
      <ul className="mt-3 space-y-2">
        {CATEGORY_KEYS.map((category) => {
          const deducted = deductions[category] ?? 0;
          const cap = CATEGORY_CAPS[category];
          const remaining = cap - deducted;
          const pct = cap > 0 ? Math.round((remaining / cap) * 100) : 100;

          return (
            <li key={category} className="list-none">
              <div className="flex items-center justify-between text-xs text-gray-600">
                <span>{CATEGORY_LABELS[category]}</span>
                <span>
                  {remaining}/{cap} pts
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all duration-500"
                  style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
