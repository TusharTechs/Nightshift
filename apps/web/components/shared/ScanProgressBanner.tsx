/**
 * Baseline discovery scan progress banner (Section 1.10 wireframe).
 * aria-live="polite" per the Accessibility spec so screen readers announce
 * progress changes without interrupting the user.
 */

export function ScanProgressBanner({ isComplete }: { isComplete: boolean }) {
  if (isComplete) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
    >
      <span aria-hidden="true" className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
      NightShift AI is performing your store&apos;s baseline operational scan...
    </div>
  );
}
