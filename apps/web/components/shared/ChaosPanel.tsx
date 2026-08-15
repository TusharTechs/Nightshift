"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, triggerDemoIncident, type DemoIncidentResponse, type DemoScenarioId } from "../../lib/api";

/**
 * Demo Incident Control Panel ("Chaos Panel") — Sprint 5 Phase 4. A
 * floating, dev/demo-only trigger for the three named Demo Incident
 * Generator scenarios (`domain/demo_incidents.py`), so a live demo never
 * has to wait for something to organically go wrong.
 *
 * Only rendered when the backend's own `Settings.demo_mode_enabled` is true
 * (`StoreSnapshot.demo_mode_enabled`, threaded down from `page.tsx`) — the
 * same flag `POST /api/v1/demo/incidents/{scenario_id}` itself is gated on,
 * so this panel can never appear promising a capability that would 404.
 *
 * Each scenario button both triggers the incident and (per Phase 4's own
 * requirement) dispatches a background shift in the same request — this
 * component's job after that is to make the result show up without a
 * manual refresh: it invalidates the pending-approvals/latest-shift/
 * work-log queries immediately, then again every few seconds for a short
 * window (the background shift is genuinely asynchronous — a Celery worker
 * has to pick it up and run the full Observe -> ... -> Persist chain, which
 * takes longer than one request/response cycle). This is a deliberate,
 * scoped exception to the rest of the app's "cheap to refetch on demand,
 * not a moment-to-moment live feed" posture (see `use-pending-approvals.ts`'s
 * own comment) — justified here because the merchant just personally
 * triggered the exact background job they're waiting to see finish.
 */

const POLL_INTERVAL_MS = 4_000;
const POLL_DURATION_MS = 60_000;

const SCENARIOS: { id: DemoScenarioId; label: string }[] = [
  { id: "midnight_pricing_disaster", label: "Scenario 1: Midnight Pricing Disaster" },
  { id: "rogue_developer_theme_break", label: "Scenario 2: Rogue Developer Theme Break" },
  { id: "catalog_seo_collapse", label: "Scenario 3: Catalog SEO Collapse" },
];

type ScenarioResult =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; response: DemoIncidentResponse }
  | { status: "error"; message: string };

export function ChaosPanel({ shopDomain }: { shopDomain: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [results, setResults] = useState<Record<DemoScenarioId, ScenarioResult>>({
    midnight_pricing_disaster: { status: "idle" },
    rogue_developer_theme_break: { status: "idle" },
    catalog_seo_collapse: { status: "idle" },
  });
  const queryClient = useQueryClient();
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  function invalidateLiveQueries() {
    queryClient.invalidateQueries({ queryKey: ["pending-approvals", shopDomain] });
    queryClient.invalidateQueries({ queryKey: ["latest-shift", shopDomain] });
    queryClient.invalidateQueries({ queryKey: ["work-log", shopDomain] });
    queryClient.invalidateQueries({ queryKey: ["shift-replay-latest-active", shopDomain] });
  }

  function startLivePolling() {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    invalidateLiveQueries();
    const startedAt = Date.now();
    pollTimerRef.current = setInterval(() => {
      if (Date.now() - startedAt >= POLL_DURATION_MS) {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        return;
      }
      invalidateLiveQueries();
    }, POLL_INTERVAL_MS);
  }

  async function handleTrigger(scenarioId: DemoScenarioId) {
    setResults((prev) => ({ ...prev, [scenarioId]: { status: "loading" } }));
    try {
      const response = await triggerDemoIncident(shopDomain, scenarioId);
      setResults((prev) => ({ ...prev, [scenarioId]: { status: "success", response } }));
      startLivePolling();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't trigger this scenario.";
      setResults((prev) => ({ ...prev, [scenarioId]: { status: "error", message } }));
    }
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-2">
      {isOpen ? (
        <div className="w-80 space-y-3 rounded-lg border border-gray-700 bg-gray-950 p-3 shadow-2xl">
          <p className="font-mono text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            [ Chaos Panel — Dev/Demo Only ]
          </p>
          <div className="space-y-2">
            {SCENARIOS.map((scenario) => {
              const result = results[scenario.id];
              return (
                <div key={scenario.id} className="space-y-1">
                  <button
                    type="button"
                    onClick={() => handleTrigger(scenario.id)}
                    disabled={result.status === "loading"}
                    className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-left font-mono text-xs text-gray-200 transition-all duration-150 hover:border-amber-500 hover:bg-gray-800 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {result.status === "loading" ? "Triggering…" : `[ ${scenario.label} ]`}
                  </button>
                  {result.status === "success" ? (
                    <p className="px-1 text-[11px] text-emerald-400">
                      ✓ Triggered — background shift dispatched
                      {result.response.notes ? `. ${result.response.notes}` : "."}
                    </p>
                  ) : result.status === "error" ? (
                    <p className="px-1 text-[11px] text-red-400">✗ {result.message}</p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        aria-label={isOpen ? "Close Chaos Panel" : "Open Chaos Panel"}
        className="rounded-full border border-amber-500 bg-gray-950 px-4 py-2 font-mono text-xs font-semibold text-amber-400 shadow-lg transition-all duration-150 hover:bg-gray-900 active:scale-95"
      >
        [ ⚡ CHAOS PANEL ]
      </button>
    </div>
  );
}
