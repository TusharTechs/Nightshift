"use client";

import { useEffect, useMemo, useState } from "react";

import type { WorkLogEntry } from "../../lib/api";
import { useShiftReplay } from "../../lib/use-shift-replay";
import { avatarForActionType, labelForActionType } from "../../lib/specialist-identity";

const AUTOPLAY_STEP_MS = 1200;

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatShortTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Human-readable label for a raw `action` string, without inventing any
 * detail not already in `rationale` — this is purely a header/label. */
function humanizeAction(action: string): string {
  return action
    .toLowerCase()
    .split("_")
    .map((word) => word[0]?.toUpperCase() + word.slice(1))
    .join(" ");
}

export function ShiftReplayLoading() {
  return (
    <div className="h-32 animate-pulse rounded-lg border border-gray-200 bg-gray-100" aria-hidden="true" />
  );
}

export function ShiftReplayError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-center">
      <p className="text-sm font-medium text-red-800">Couldn&apos;t load Shift Replay</p>
      <p className="text-xs text-red-700">{message}</p>
      <button
        onClick={onRetry}
        className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
      >
        Retry
      </button>
    </div>
  );
}

export function ShiftReplayEmpty() {
  return (
    <div className="rounded-lg border border-dashed border-gray-300 p-4 text-center text-sm text-gray-500">
      No specialist activity in recent shifts yet — Shift Replay will populate once NightShift
      takes its first action.
    </div>
  );
}

function ReplayScrubber({ entries }: { entries: WorkLogEntry[] }) {
  const [index, setIndex] = useState(entries.length - 1);
  const [isPlaying, setIsPlaying] = useState(false);
  const current = entries[index];

  useEffect(() => {
    if (!isPlaying) return;
    if (index >= entries.length - 1) {
      setIsPlaying(false);
      return;
    }
    const timer = setTimeout(() => setIndex((i) => Math.min(i + 1, entries.length - 1)), AUTOPLAY_STEP_MS);
    return () => clearTimeout(timer);
  }, [isPlaying, index, entries.length]);

  function handlePlayToggle() {
    if (!isPlaying && index >= entries.length - 1) {
      setIndex(0);
    }
    setIsPlaying((p) => !p);
  }

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handlePlayToggle}
          aria-label={isPlaying ? "Pause replay" : "Play replay"}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-900 text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={entries.length - 1}
          value={index}
          onChange={(event) => {
            setIsPlaying(false);
            setIndex(Number(event.target.value));
          }}
          aria-label="Shift replay scrubber"
          className="min-w-0 flex-1 accent-gray-900"
        />
        <span className="w-14 shrink-0 text-right text-xs tabular-nums text-gray-500">
          {index + 1}/{entries.length}
        </span>
      </div>

      {/* Sprint 5 Phase 2.2: clickable timestamp nodes, upgraded from a bare
          dot row — each node carries its own specialist avatar (Phase 2.3,
          real per-action_type identity, see `lib/specialist-identity.ts`),
          action-status icon, and time label, and the active node scales up
          with a glow ring as the shift is scrubbed or autoplayed through. */}
      <div className="flex snap-x gap-2 overflow-x-auto pb-1" aria-label="Timeline of specialist actions this shift">
        {entries.map((entry, i) => {
          const isActive = i === index;
          const specialistAvatar = avatarForActionType(entry.actor_id);
          const specialistLabel = labelForActionType(entry.actor_id);
          return (
            <button
              key={entry.id}
              type="button"
              onClick={() => {
                setIsPlaying(false);
                setIndex(i);
              }}
              title={`${specialistLabel ?? humanizeAction(entry.action)} — ${humanizeAction(entry.action)}`}
              aria-current={isActive}
              className={`flex shrink-0 snap-start flex-col items-center gap-1 rounded-lg border px-2.5 py-2 text-center transition-all duration-200 ${
                isActive
                  ? "scale-105 border-gray-900 bg-gray-900 text-white shadow-[0_0_0_3px_rgba(17,24,39,0.15)]"
                  : "border-gray-200 bg-gray-50 hover:border-gray-300"
              }`}
            >
              <span className="flex items-center gap-0.5 text-base leading-none" aria-hidden="true">
                {specialistAvatar ? <span>{specialistAvatar}</span> : null}
                <span>{entry.icon}</span>
              </span>
              <span className={`text-[10px] font-medium tabular-nums ${isActive ? "text-gray-200" : "text-gray-500"}`}>
                {formatShortTime(entry.timestamp)}
              </span>
            </button>
          );
        })}
      </div>

      {current ? (
        <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-sm font-medium text-gray-900">
              {avatarForActionType(current.actor_id) ? (
                <span aria-hidden="true">{avatarForActionType(current.actor_id)}</span>
              ) : null}
              <span aria-hidden="true">{current.icon}</span> {humanizeAction(current.action)}
            </p>
            <span className="text-xs text-gray-500">{formatTime(current.timestamp)}</span>
          </div>
          <p className="mt-1 text-xs font-medium text-gray-500">
            {labelForActionType(current.actor_id) ?? current.actor_id}
          </p>
          <p className="mt-1 text-sm text-gray-600">{current.rationale}</p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Shift Replay (Sprint 4 Step 5, Phase C). An animated scrubber over
 * whichever recent shift actually has `audit_logs` activity — "every
 * specialist's action that shift," per the Vision doc — with the same
 * deterministic icon treatment used in the Work Log
 * (`domain/replay.py::icon_for_action`, CONFLICTS.md item 46).
 *
 * Sprint 5 Phase 1.2: defaults to the server-side "latest active shift"
 * fallback (`GET /api/v1/shifts/replay/latest-active`) rather than
 * whichever shift happens to be the very latest — a clean, all-clear shift
 * has nothing to replay, and defaulting to it made this section look
 * broken instead of showing what NightShift actually did most recently.
 */
export function ShiftReplay({ shopDomain }: { shopDomain: string | null }) {
  const { data, isLoading, isError, error, refetch } = useShiftReplay(shopDomain);
  const entries = useMemo(() => data?.entries ?? [], [data]);

  return (
    <section aria-labelledby="shift-replay-heading" className="space-y-3">
      <h2 id="shift-replay-heading" className="text-lg font-semibold text-gray-900">
        Shift Replay{data ? ` — Shift #${data.shift_number}` : ""}
      </h2>
      {isLoading ? (
        <ShiftReplayLoading />
      ) : isError ? (
        <ShiftReplayError
          message={error instanceof Error ? error.message : "Unknown error"}
          onRetry={() => refetch()}
        />
      ) : entries.length > 0 ? (
        <ReplayScrubber entries={entries} />
      ) : (
        <ShiftReplayEmpty />
      )}
    </section>
  );
}
