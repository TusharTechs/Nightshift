import type { ChiefOpsBriefing } from "../../lib/api";
import { CHIEF_OPS_AVATAR, avatarForAgentName } from "../../lib/specialist-identity";

/**
 * Executive Briefing (Sprint 4 Step 4, Phase B). Renders Chief Ops AI's
 * Multi-Agent Handshake — one "turn" per specialist finding this shift, plus
 * a final synthesis narrative — per the Vision doc's Three Locked Additions
 * #2. Icons are the backend's own deterministic assignment (never
 * LLM-chosen — see `domain/chief_ops.py`), so this component only renders
 * whatever string it's given rather than re-deriving one from status/icon
 * logic itself.
 */
export function ExecutiveBriefing({ briefing }: { briefing: ChiefOpsBriefing }) {
  return (
    <section aria-labelledby="executive-briefing-heading" className="space-y-3">
      <h2 id="executive-briefing-heading" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
        <span aria-hidden="true">{CHIEF_OPS_AVATAR}</span> Executive Briefing
      </h2>

      <div
        className={`rounded-lg border p-4 ${
          briefing.correlated ? "border-amber-200 bg-amber-50" : "border-gray-200 bg-white"
        }`}
      >
        {briefing.correlated ? (
          // Sprint 5 Phase 5: Chief Ops AI's prompt now instructs the LLM to
          // prefix a correlated narrative with "Root Cause: " itself (see
          // `domain/chief_ops.py::_PROMPT_TEMPLATE`) — this badge is a purely
          // visual reinforcement of that same, backend-decided `correlated`
          // flag, never a separate claim of its own.
          <p className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-amber-800">
            <span aria-hidden="true">🔗</span> Root Cause Identified
          </p>
        ) : null}
        <p className="text-sm text-gray-700">{briefing.narrative}</p>
        {/* Sprint 5 Phase 1.3: Chief Ops now attempts an LLM pass for any
            shift with 1+ findings, so `used_llm` is false only on a genuine
            in-shift LLM failure (budget ceiling or a bad response) — worded
            as a normal operational note, not as a "something's broken"
            message. */}
        {!briefing.used_llm && briefing.turns.length > 0 ? (
          <p className="mt-2 text-xs text-gray-400">
            Rule-based summary — AI synthesis hit a temporary limit this shift.
          </p>
        ) : null}
      </div>

      {briefing.turns.length > 0 ? (
        <ol className="space-y-2" aria-label="Specialist findings this shift">
          {briefing.turns.map((turn) => {
            // Sprint 5 Phase 2.3: the specialist's own visual identity
            // badge, alongside (not replacing) the existing per-issue
            // ⚡/🧠/🟢 status icon — two distinct, deliberately separate
            // icon systems (see CONFLICTS.md item 46).
            const specialistAvatar = avatarForAgentName(turn.agent_name);
            return (
              <li
                key={turn.issue_id}
                className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3"
              >
                <span aria-hidden="true" className="text-lg leading-none">
                  {turn.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-1 text-xs font-medium text-gray-500">
                    {specialistAvatar ? <span aria-hidden="true">{specialistAvatar}</span> : null}
                    {turn.agent_name}
                  </p>
                  <p className="truncate text-sm font-medium text-gray-900">{turn.finding_title}</p>
                  <p className="mt-0.5 text-xs text-gray-500">{turn.finding_summary}</p>
                  {turn.merchant_memory_note ? (
                    // Sprint 5 Phase 5: grounded verbatim in the task's own
                    // confidence signal — see
                    // `domain/confidence.py::merchant_memory_note`.
                    <p className="mt-1.5 flex items-start gap-1 rounded-md bg-indigo-50 px-2 py-1 text-xs text-indigo-800">
                      <span aria-hidden="true">🧠</span>
                      <span>
                        <span className="font-semibold">Merchant Preference Applied:</span>{" "}
                        {turn.merchant_memory_note}
                      </span>
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </section>
  );
}
