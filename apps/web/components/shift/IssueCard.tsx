import type { IssueSeverity, ShiftIssue } from "../../lib/api";

/**
 * Detected Issue card (Sprint 2 Feature 5 UI Spec). Severity pill colors are
 * verbatim from the spec: CRITICAL red #EF4444, HIGH orange #F97316, MEDIUM
 * amber #F59E0B, LOW blue #3B82F6 — a distinct palette from
 * HealthScoreGauge's own red/amber/emerald health-tier coloring (same two
 * hex reused, different semantic meaning; not a conflict, just worth noting
 * for anyone editing colors later).
 */

interface SeverityStyle {
  bg: string;
  fg: string;
  label: string;
}

// Keyed by the exact IssueSeverity union (not a generic string index) so
// every lookup by a known severity is guaranteed non-undefined; unexpected
// values still fall back safely via `severityStyle` below.
const SEVERITY_STYLES: Record<IssueSeverity, SeverityStyle> = {
  CRITICAL: { bg: "#EF4444", fg: "#FFFFFF", label: "Critical" },
  HIGH: { bg: "#F97316", fg: "#FFFFFF", label: "High" },
  MEDIUM: { bg: "#F59E0B", fg: "#111827", label: "Medium" },
  LOW: { bg: "#3B82F6", fg: "#FFFFFF", label: "Low" },
};

function severityStyle(severity: string): SeverityStyle {
  return SEVERITY_STYLES[severity as IssueSeverity] ?? SEVERITY_STYLES.LOW;
}

function formatUsd(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function IssueCard({ issue, index = 0 }: { issue: ShiftIssue; index?: number }) {
  const severity = severityStyle(issue.severity);
  const confidencePercent = Math.round(issue.confidence_score * 100);
  const primaryEvidence = issue.affected_resources[0];
  const extraEvidenceCount = Math.max(0, issue.affected_resources.length - 1);

  return (
    <li
      className="issue-card-entrance list-none rounded-lg border border-gray-200 bg-white p-4 opacity-0"
      style={{
        animation: "issue-card-in 0.3s ease-out forwards",
        animationDelay: `${index * 50}ms`,
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="rounded-full px-2 py-0.5 text-xs font-semibold"
            style={{ backgroundColor: severity.bg, color: severity.fg }}
          >
            {severity.label}
          </span>
          <h3 className="font-medium text-gray-900">{issue.title}</h3>
        </div>
        <span className="whitespace-nowrap text-sm font-semibold text-gray-900">
          {formatUsd(issue.revenue_impact_estimate)}
        </span>
      </div>

      <p className="mt-2 text-sm text-gray-600">{issue.description}</p>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
        <span>Confidence: {confidencePercent}%</span>
        {primaryEvidence ? (
          <span className="truncate" title={issue.affected_resources.join(", ")}>
            Evidence: {primaryEvidence}
            {extraEvidenceCount > 0 ? ` (+${extraEvidenceCount} more)` : ""}
          </span>
        ) : null}
      </div>
    </li>
  );
}
