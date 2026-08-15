"use client";

import { useState } from "react";

import { ApiError, type PendingApproval, type RiskLevel } from "../../lib/api";
import { useSubmitApprovalAction } from "../../lib/use-pending-approvals";
import { ThemeGuardianDiffCard } from "./ThemeGuardianDiffCard";

/**
 * Approval Center card (Sprint 3 mockup, verbatim layout):
 *
 *   Pending Actions
 *   ────────────────────
 *   Duplicate Discount        <- issue title
 *   Revenue Impact  $248      <- revenue_impact_usd
 *   Risk            Low       <- risk_level, mapped to a readable label/color
 *   AI Confidence   96%       <- confidence_score * 100
 *   [Approve] [Reject] [Modify]
 *
 * "Modify" IS "Approve with execution_override_params" per the backend
 * design (`ApprovalAction` only has APPROVE/REJECT/DEFER) — there is no
 * separate MODIFY action value sent to the API.
 */

interface RiskStyle {
  bg: string;
  fg: string;
  label: string;
}

// Colors verbatim from the Sprint 3 mockup's own mapping (Low green, Moderate
// amber, High orange, Critical red) — the same HIGH/MODERATE/CRITICAL hex
// values as IssueCard's SEVERITY_STYLES, with LOW swapped from blue to green
// per this screen's explicit spec.
const RISK_STYLES: Record<RiskLevel, RiskStyle> = {
  LEVEL_1_SAFE: { bg: "#10B981", fg: "#FFFFFF", label: "Low" },
  LEVEL_2_MODERATE: { bg: "#F59E0B", fg: "#111827", label: "Moderate" },
  LEVEL_3_HIGH: { bg: "#F97316", fg: "#FFFFFF", label: "High" },
  LEVEL_4_CRITICAL: { bg: "#EF4444", fg: "#FFFFFF", label: "Critical" },
};

function riskStyle(riskLevel: string): RiskStyle {
  return RISK_STYLES[riskLevel as RiskLevel] ?? RISK_STYLES.LEVEL_2_MODERATE;
}

const ACTION_TYPE_LABELS: Record<string, string> = {
  GENERATE_ALT_TEXT: "Generate ALT text",
  REWRITE_PRODUCT_DESCRIPTION: "Rewrite product description",
};

export function humanizeActionType(actionType: string): string {
  const known = ACTION_TYPE_LABELS[actionType];
  if (known) return known;

  // Fallback for action types not yet in the lookup: SOME_ACTION_TYPE ->
  // "Some action type" (future sprints add action types faster than this
  // lookup can be kept in sync — this must never crash or render blank).
  const words = actionType.toLowerCase().split("_").filter(Boolean);
  if (words.length === 0) return actionType;
  return words.map((word, i) => (i === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word)).join(" ");
}

function formatUsd(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function formatExpiry(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  if (diffMs <= 0) return "Expired";
  const diffHours = diffMs / (1000 * 60 * 60);
  if (diffHours < 1) {
    const mins = Math.max(1, Math.round(diffMs / 60000));
    return `Expires in ${mins}m`;
  }
  return `Expires in ${Math.round(diffHours)}h`;
}

type CardMode = "idle" | "reject" | "modify";
type LastSubmission = "approve" | "reject" | "modify" | null;

const buttonBaseClass =
  "rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-150 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50";

export function ApprovalCard({
  approval,
  shopDomain,
  onRequireRefetch,
}: {
  approval: PendingApproval;
  shopDomain: string | null;
  onRequireRefetch: () => void;
}) {
  const mutation = useSubmitApprovalAction(shopDomain);

  const [mode, setMode] = useState<CardMode>("idle");
  const [lastSubmission, setLastSubmission] = useState<LastSubmission>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [rejectError, setRejectError] = useState<string | null>(null);
  const [modifyValue, setModifyValue] = useState("");
  const [modifyError, setModifyError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const risk = riskStyle(approval.risk_level);
  const confidencePercent = Math.round(approval.confidence_score * 100);

  function handleMutationError(error: unknown) {
    if (error instanceof ApiError) {
      if (error.code === "APPROVAL_ALREADY_DECIDED") {
        setActionError("This action was already decided — refreshing the list.");
        onRequireRefetch();
        return;
      }
      if (error.code === "TASK_APPROVAL_EXPIRED") {
        setActionError("This approval window expired — it will resurface as a new issue.");
        onRequireRefetch();
        return;
      }
      setActionError(error.message);
      return;
    }
    setActionError(error instanceof Error ? error.message : "Something went wrong. Please try again.");
  }

  function handleApprove() {
    setActionError(null);
    setLastSubmission("approve");
    mutation.mutate(
      { approvalId: approval.approval_id, action: "APPROVE" },
      { onError: handleMutationError }
    );
  }

  function handleRejectSubmit() {
    if (rejectReason.trim().length < 5) {
      setRejectError("Please provide a reason of at least 5 characters.");
      return;
    }
    setRejectError(null);
    setActionError(null);
    setLastSubmission("reject");
    mutation.mutate(
      { approvalId: approval.approval_id, action: "REJECT", rejection_reason: rejectReason.trim() },
      { onError: handleMutationError }
    );
  }

  function handleModifySubmit() {
    let overrideParams: Record<string, unknown>;

    if (approval.recommended_action === "GENERATE_ALT_TEXT") {
      if (!modifyValue.trim()) {
        setModifyError("ALT text can't be empty.");
        return;
      }
      overrideParams = { new_alt_text: modifyValue.trim() };
    } else if (approval.recommended_action === "REWRITE_PRODUCT_DESCRIPTION") {
      if (!modifyValue.trim()) {
        setModifyError("Description can't be empty.");
        return;
      }
      overrideParams = { new_description_html: modifyValue.trim() };
    } else {
      try {
        const parsed: unknown = JSON.parse(modifyValue);
        if (typeof parsed !== "object" || parsed === null) {
          setModifyError("Must be a JSON object, e.g. {\"key\": \"value\"}.");
          return;
        }
        overrideParams = parsed as Record<string, unknown>;
      } catch {
        setModifyError("That's not valid JSON.");
        return;
      }
    }

    setModifyError(null);
    setActionError(null);
    setLastSubmission("modify");
    mutation.mutate(
      { approvalId: approval.approval_id, action: "APPROVE", execution_override_params: overrideParams },
      { onError: handleMutationError }
    );
  }

  const isBusy = mutation.isPending;
  const justSucceeded = mutation.isSuccess;

  return (
    <li
      className="issue-card-entrance list-none rounded-lg border border-gray-200 bg-white p-4 opacity-0"
      style={{ animation: "issue-card-in 0.3s ease-out forwards" }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-medium text-gray-900">{approval.title}</h3>
        <span
          className="rounded-full px-2 py-0.5 text-xs font-semibold"
          style={{ backgroundColor: risk.bg, color: risk.fg }}
        >
          {risk.label}
        </span>
      </div>

      <p className="mt-1 text-sm text-gray-600">{humanizeActionType(approval.recommended_action)}</p>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-gray-500">Revenue Impact</dt>
          <dd className="font-semibold text-gray-900">{formatUsd(approval.revenue_impact_usd)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">Risk</dt>
          <dd className="font-semibold text-gray-900">{risk.label}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">AI Confidence</dt>
          <dd className="font-semibold text-gray-900">{confidencePercent}%</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">Window</dt>
          <dd className="font-semibold text-gray-900">{formatExpiry(approval.expires_at)}</dd>
        </div>
      </dl>

      {approval.merchant_memory_note ? (
        // Sprint 5 Phase 5: grounded verbatim in the task's own confidence
        // signal breakdown — see `domain/confidence.py::merchant_memory_note`.
        // Only rendered when this merchant has real prior approval history
        // for this action type at this store.
        <p className="mt-3 flex items-start gap-1.5 rounded-md bg-indigo-50 px-3 py-2 text-sm text-indigo-800">
          <span aria-hidden="true">🧠</span>
          <span>
            <span className="font-semibold">Merchant Preference Applied:</span>{" "}
            {approval.merchant_memory_note}
          </span>
        </p>
      ) : null}

      {/* Sprint 5 Phase 3.2: Theme Guardian's visual inspection card — only
          for this one action type, and only when evidence_data actually has
          the expected shape (ThemeGuardianDiffCard itself renders nothing
          otherwise). */}
      {approval.recommended_action === "GENERATE_THEME_RESTORE_GUIDE" ? (
        <ThemeGuardianDiffCard
          shopDomain={shopDomain}
          description={approval.description ?? ""}
          evidenceData={approval.evidence_data}
        />
      ) : null}

      {actionError ? (
        <p role="alert" className="mt-3 text-sm font-medium text-red-700">
          {actionError}
        </p>
      ) : null}

      {justSucceeded ? (
        <p role="status" className="mt-3 text-sm font-medium text-emerald-700">
          {lastSubmission === "reject" ? "✓ Rejected" : "✓ Approved"} — refreshing the list…
        </p>
      ) : mode === "reject" ? (
        <div className="mt-3 space-y-2">
          <label htmlFor={`reject-reason-${approval.approval_id}`} className="text-xs font-medium text-gray-700">
            Why are you rejecting this?
          </label>
          <textarea
            id={`reject-reason-${approval.approval_id}`}
            aria-label="Rejection reason"
            className="w-full rounded-md border border-gray-300 p-2 text-sm text-gray-900 focus:border-gray-500 focus:outline-none"
            rows={2}
            value={rejectReason}
            disabled={isBusy}
            onChange={(e) => setRejectReason(e.target.value)}
          />
          {rejectError ? (
            <p role="alert" className="text-xs font-medium text-red-700">
              {rejectError}
            </p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              aria-label="Submit rejection"
              disabled={isBusy}
              onClick={handleRejectSubmit}
              className={`${buttonBaseClass} bg-red-600 text-white hover:bg-red-700`}
            >
              {isBusy ? "Submitting…" : "Confirm reject"}
            </button>
            <button
              type="button"
              aria-label="Cancel rejection"
              disabled={isBusy}
              onClick={() => {
                setMode("idle");
                setRejectError(null);
              }}
              className={`${buttonBaseClass} border border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : mode === "modify" ? (
        <div className="mt-3 space-y-2">
          <label htmlFor={`modify-input-${approval.approval_id}`} className="text-xs font-medium text-gray-700">
            {approval.recommended_action === "GENERATE_ALT_TEXT"
              ? "New ALT text"
              : approval.recommended_action === "REWRITE_PRODUCT_DESCRIPTION"
                ? "New product description"
                : "Override parameters (JSON)"}
          </label>
          {approval.recommended_action === "GENERATE_ALT_TEXT" ? (
            <input
              id={`modify-input-${approval.approval_id}`}
              aria-label="New ALT text"
              type="text"
              className="w-full rounded-md border border-gray-300 p-2 text-sm text-gray-900 focus:border-gray-500 focus:outline-none"
              value={modifyValue}
              disabled={isBusy}
              onChange={(e) => setModifyValue(e.target.value)}
            />
          ) : (
            <textarea
              id={`modify-input-${approval.approval_id}`}
              aria-label={
                approval.recommended_action === "REWRITE_PRODUCT_DESCRIPTION"
                  ? "New product description"
                  : "Override parameters as JSON"
              }
              className="w-full rounded-md border border-gray-300 p-2 font-mono text-sm text-gray-900 focus:border-gray-500 focus:outline-none"
              rows={4}
              value={modifyValue}
              disabled={isBusy}
              onChange={(e) => setModifyValue(e.target.value)}
            />
          )}
          {modifyError ? (
            <p role="alert" className="text-xs font-medium text-red-700">
              {modifyError}
            </p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              aria-label="Submit modified action"
              disabled={isBusy}
              onClick={handleModifySubmit}
              className={`${buttonBaseClass} bg-gray-900 text-white hover:bg-gray-700`}
            >
              {isBusy ? "Submitting…" : "Submit"}
            </button>
            <button
              type="button"
              aria-label="Cancel modification"
              disabled={isBusy}
              onClick={() => {
                setMode("idle");
                setModifyError(null);
              }}
              className={`${buttonBaseClass} border border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            aria-label={`Approve: ${approval.title}`}
            disabled={isBusy}
            onClick={handleApprove}
            className={`${buttonBaseClass} bg-gray-900 text-white hover:bg-gray-700`}
          >
            {isBusy && lastSubmission === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            aria-label={`Reject: ${approval.title}`}
            disabled={isBusy}
            onClick={() => setMode("reject")}
            className={`${buttonBaseClass} border border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
          >
            Reject
          </button>
          <button
            type="button"
            aria-label={`Modify: ${approval.title}`}
            disabled={isBusy}
            onClick={() => setMode("modify")}
            className={`${buttonBaseClass} border border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
          >
            Modify
          </button>
        </div>
      )}
    </li>
  );
}
