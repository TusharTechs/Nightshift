/**
 * Store Health Score radial gauge (Section 1.10, Components #2).
 * Color mapping: 0-49 red, 50-79 amber, 80-100 emerald.
 */

const RADIUS = 54;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function colorForScore(score: number): string {
  if (score >= 80) return "#10B981"; // emerald
  if (score >= 50) return "#F59E0B"; // amber
  return "#EF4444"; // red
}

function labelForScore(score: number): string {
  if (score >= 80) return "Healthy";
  if (score >= 50) return "Needs attention";
  return "Critical";
}

export function HealthScoreGauge({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  const offset = CIRCUMFERENCE * (1 - clamped / 100);
  const color = colorForScore(clamped);

  return (
    <div className="flex flex-col items-center gap-2">
      <svg
        viewBox="0 0 120 120"
        width={120}
        height={120}
        role="img"
        aria-label={`Store health score ${clamped} out of 100 — ${labelForScore(clamped)}`}
      >
        <circle cx="60" cy="60" r={RADIUS} fill="none" stroke="#E5E7EB" strokeWidth="10" />
        <circle
          cx="60"
          cy="60"
          r={RADIUS}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          transform="rotate(-90 60 60)"
          style={{ transition: "stroke-dashoffset 1.2s ease-out" }}
        />
        <text
          x="60"
          y="65"
          textAnchor="middle"
          className="fill-gray-900"
          style={{ fontSize: "24px", fontWeight: 600 }}
        >
          {clamped}
        </text>
      </svg>
      <span className="text-sm font-medium text-gray-600">{labelForScore(clamped)}</span>
    </div>
  );
}
