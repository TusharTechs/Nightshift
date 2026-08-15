"use client";

import { useState } from "react";

import { fetchWorkLog, type WorkLogEntry } from "../../lib/api";
import { avatarForActionType, labelForActionType } from "../../lib/specialist-identity";
import { ThemeRestoreActions } from "../shared/ThemeRestoreActions";

/**
 * AI Work Log (Sprint 3 AI Trust & Execution): the append-only audit trail
 * of everything the AI has done, so the merchant can build trust in the
 * system over time. Supports all four required screen states, matching
 * ShiftReportView's own exported-function pattern.
 */

const ACTOR_LABELS: Record<string, string> = {
  AI_AGENT: "AI Employee",
  MERCHANT: "Merchant",
  SYSTEM: "System",
};

function humanizeActor(actorType: string): string {
  return ACTOR_LABELS[actorType] ?? actorType;
}

const ACTION_LABELS: Record<string, string> = {
  TASK_PLANNED: "Planned a fix",
  APPROVAL_REQUESTED: "Requested merchant approval",
  EXECUTION_COMPLETED: "Executed the fix",
  VERIFICATION_PASSED: "Verified successfully",
  VERIFICATION_FAILED: "Verification failed",
  ROLLBACK_COMPLETED: "Rolled back",
  APPROVAL_GRANTED: "Approved",
  APPROVAL_GRANTED_WITH_MODIFICATION: "Approved with changes",
  APPROVAL_REJECTED: "Rejected",
  APPROVAL_DEFERRED: "Deferred",
};

export function humanizeAction(action: string): string {
  // Sane fallback to the raw string for anything unmapped — future sprints
  // will add action types faster than this lookup can be kept in sync, and
  // that must never silently break the log.
  return ACTION_LABELS[action] ?? action;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

/**
 * "Employee Notebook" step track (Sprint 5 Phase 5). `TASK_PLANNED` is one
 * real audit-log event that, in this deterministic pipeline, simultaneously
 * covers Observed (the issue itself), Reasoned (the risk assessment that
 * produced `rationale`), and Proposed (the recommended action) — it is
 * rendered as all three stages lit at once from that single real timestamp,
 * never as three fabricated, separately-timestamped events. Actions with no
 * place on this five-stage AI lifecycle track (merchant decisions,
 * rollbacks) render no track at all — see `MERCHANT_DECISION_ACTIONS` below
 * for those, kept as plain badges instead.
 */
const STAGE_LABELS = ["Observed", "Reasoned", "Proposed", "Executed", "Verified"] as const;

const STAGE_INDICES_FOR_ACTION: Record<string, number[]> = {
  TASK_PLANNED: [0, 1, 2],
  APPROVAL_REQUESTED: [2],
  EXECUTION_COMPLETED: [3],
  VERIFICATION_PASSED: [4],
  VERIFICATION_FAILED: [4],
};

const MERCHANT_DECISION_BADGES: Record<string, { label: string; icon: string; tone: "granted" | "rejected" | "neutral" }> = {
  APPROVAL_GRANTED: { label: "Merchant Approved", icon: "✅", tone: "granted" },
  APPROVAL_GRANTED_WITH_MODIFICATION: { label: "Merchant Approved (modified)", icon: "✅", tone: "granted" },
  APPROVAL_REJECTED: { label: "Merchant Rejected", icon: "🚫", tone: "rejected" },
  APPROVAL_DEFERRED: { label: "Merchant Deferred", icon: "⏸️", tone: "neutral" },
  ROLLBACK_COMPLETED: { label: "Rolled Back", icon: "↩️", tone: "rejected" },
};

function WorkLogStageTrack({ action }: { action: string }) {
  const activeIndices = STAGE_INDICES_FOR_ACTION[action];
  if (activeIndices) {
    const failed = action === "VERIFICATION_FAILED";
    return (
      <ol className="mt-2 flex flex-wrap items-center gap-1" aria-label="Execution lifecycle stage">
        {STAGE_LABELS.map((label, index) => {
          const isActive = activeIndices.includes(index);
          const activeClass = failed
            ? "bg-red-100 text-red-800"
            : "bg-indigo-100 text-indigo-800";
          return (
            <li
              key={label}
              className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                isActive ? activeClass : "bg-gray-100 text-gray-400"
              }`}
            >
              {label}
              {isActive && failed && index === 4 ? " ✗" : null}
            </li>
          );
        })}
      </ol>
    );
  }

  const decision = MERCHANT_DECISION_BADGES[action];
  if (!decision) return null;
  const toneClass =
    decision.tone === "granted"
      ? "bg-emerald-100 text-emerald-800"
      : decision.tone === "rejected"
        ? "bg-red-100 text-red-800"
        : "bg-gray-100 text-gray-600";
  return (
    <p className={`mt-2 inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${toneClass}`}>
      <span aria-hidden="true">{decision.icon}</span> {decision.label}
    </p>
  );
}

export function WorkLogLoading() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading work log">
      <div className="h-6 w-1/3 animate-pulse rounded bg-gray-100" />
      <div className="h-16 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
      <div className="h-16 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
      <div className="h-16 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
    </div>
  );
}

export function WorkLogEmpty() {
  return (
    <div className="flex min-h-[20vh] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 p-8 text-center">
      <p className="text-lg font-medium text-gray-900">No activity yet</p>
      <p className="max-w-sm text-sm text-gray-500">
        Everything NightShift does — decisions, executions, verifications, rollbacks — will show
        up here.
      </p>
    </div>
  );
}

export function WorkLogError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-6 text-center"
    >
      <p className="font-medium text-red-800">Couldn&apos;t load the work log</p>
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

/**
 * Theme Guardian's `GENERATE_THEME_RESTORE_GUIDE` action never writes to
 * Shopify itself (no app-granted theme-write exemption exists — see
 * `execute_cognitive_task.py`'s own module comment). Its EXECUTION_COMPLETED
 * audit entry's `after_state` carries the guide the merchant must apply
 * themselves: `{items: [{filename, theme_editor_url, patch_content,
 * status: "guide_generated"}]}`. Before this, that payload was fetched by
 * this same Work Log query and simply never rendered — the only way to see
 * it was a raw API call, which a merchant using just the Shopify admin UI
 * has no way to make. Detected structurally (shape, not by re-deriving the
 * action type from anywhere else) so any future non-Shopify-writing guide
 * action renders the same way for free.
 */
function extractRestoreGuide(afterState: Record<string, unknown> | null): {
  filename: string;
  themeEditorUrl: string;
  patchContent: string;
} | null {
  const items = afterState?.items;
  if (!Array.isArray(items) || items.length === 0) return null;
  const item = items[0] as Record<string, unknown>;
  if (
    item?.status === "guide_generated" &&
    typeof item.theme_editor_url === "string" &&
    typeof item.patch_content === "string" &&
    typeof item.filename === "string"
  ) {
    return { filename: item.filename, themeEditorUrl: item.theme_editor_url, patchContent: item.patch_content };
  }
  return null;
}

function RestoreGuidePanel({ guide }: { guide: { filename: string; themeEditorUrl: string; patchContent: string } }) {
  return (
    <div className="mt-3 space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3">
      <p className="text-xs font-medium text-amber-900">
        NightShift can&apos;t write theme files directly (Shopify requires a manually-granted
        exemption this app doesn&apos;t have) — apply this yourself in the Theme Editor:
      </p>
      <ThemeRestoreActions
        filename={guide.filename}
        themeEditorUrl={guide.themeEditorUrl}
        patchContent={guide.patchContent}
      />
      <details className="text-xs text-gray-600">
        <summary className="cursor-pointer select-none font-medium">Preview restored content</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border border-gray-200 bg-white p-2 font-mono">
          {guide.patchContent}
        </pre>
      </details>
    </div>
  );
}

function WorkLogRow({ entry }: { entry: WorkLogEntry }) {
  const restoreGuide =
    entry.action === "EXECUTION_COMPLETED" ? extractRestoreGuide(entry.after_state) : null;
  // Sprint 5 Phase 2.3: a specialist's own visual identity badge, resolved
  // from the same real action_type already on this entry — null (no badge)
  // for merchant actions/demo triggers rather than a guessed identity.
  const specialistAvatar = avatarForActionType(entry.actor_id);
  const specialistLabel = labelForActionType(entry.actor_id);

  return (
    <li className="list-none rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium text-gray-900">
          {specialistAvatar ? (
            <span aria-hidden="true" title={specialistLabel ?? undefined} className="text-base leading-none">
              {specialistAvatar}
            </span>
          ) : null}
          {/* Sprint 4 Step 5: same deterministic 🟢/⚡/🧠/⚠️/🎬 treatment as
              Shift Replay (domain/replay.py::icon_for_action) — Vision doc
              Locked Addition #2 calls for it in both surfaces. */}
          <span aria-hidden="true">{entry.icon}</span> {humanizeAction(entry.action)}
        </span>
        <span className="text-xs text-gray-500">{formatTimestamp(entry.timestamp)}</span>
      </div>
      <p className="mt-1 text-xs font-medium text-gray-500">
        {specialistLabel ?? humanizeActor(entry.actor_type)}
      </p>
      <p className="mt-2 text-sm text-gray-600">{entry.rationale}</p>
      <WorkLogStageTrack action={entry.action} />
      {restoreGuide ? <RestoreGuidePanel guide={restoreGuide} /> : null}
    </li>
  );
}

export function WorkLog({
  initialEntries,
  hasMore: initialHasMore,
  nextCursor: initialNextCursor,
  shopDomain,
}: {
  initialEntries: WorkLogEntry[];
  hasMore: boolean;
  nextCursor: string | null;
  shopDomain: string | null;
}) {
  const [entries, setEntries] = useState(initialEntries);
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [cursor, setCursor] = useState(initialNextCursor);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  async function handleLoadMore() {
    if (!shopDomain || !cursor) return;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const page = await fetchWorkLog(shopDomain, { startingAfter: cursor });
      setEntries((prev) => [...prev, ...page.data]);
      setHasMore(page.has_more);
      setCursor(page.next_cursor);
    } catch (err) {
      setLoadMoreError(err instanceof Error ? err.message : "Couldn't load more entries.");
    } finally {
      setIsLoadingMore(false);
    }
  }

  return (
    <div className="space-y-3">
      <ul className="space-y-3" aria-label="AI work log entries, newest first">
        {entries.map((entry) => (
          <WorkLogRow key={entry.id} entry={entry} />
        ))}
      </ul>

      {loadMoreError ? (
        <p role="alert" className="text-sm font-medium text-red-700">
          {loadMoreError}
        </p>
      ) : null}

      {hasMore ? (
        <button
          type="button"
          aria-label="Load more work log entries"
          disabled={isLoadingMore}
          onClick={handleLoadMore}
          className="w-full rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-all duration-150 hover:bg-gray-100 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}
