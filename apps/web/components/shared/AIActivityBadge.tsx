/**
 * AI Activity status pill (Section 1.10, Components #1). Idle vs Scanning
 * states styled per the exact UI spec; text label always accompanies the
 * color for WCAG AA compliance (Section 1.10 Accessibility).
 */

export type AIActivityState = "idle" | "scanning" | "executing";

const STATE_STYLES: Record<AIActivityState, string> = {
  idle: "bg-emerald-50 text-emerald-700",
  scanning: "bg-amber-50 text-amber-700 animate-pulse",
  executing: "bg-amber-50 text-amber-700 animate-pulse",
};

const STATE_LABELS: Record<AIActivityState, string> = {
  idle: "Idle",
  scanning: "Scanning...",
  executing: "Executing...",
};

export function AIActivityBadge({ state }: { state: AIActivityState }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium transition-all duration-150 ${STATE_STYLES[state]}`}
      role="status"
      aria-label={`AI activity: ${STATE_LABELS[state]}`}
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-full bg-current" />
      {STATE_LABELS[state]}
    </span>
  );
}
