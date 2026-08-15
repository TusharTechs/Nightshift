/**
 * Baseline Shift status card (Section 1.10, Components #3).
 */

export function ShiftStatusCard({ isComplete }: { isComplete: boolean }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <p className="font-medium text-gray-900">
        {isComplete ? "Baseline Shift #1 Complete" : "Baseline Shift #1 In Progress"}
      </p>
      <p className="mt-1 text-sm text-gray-500">
        {isComplete
          ? "Baseline health snapshot established."
          : "Indexing catalog entities and validating pixel triggers."}
      </p>
    </div>
  );
}
