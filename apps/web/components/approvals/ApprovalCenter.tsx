"use client";

import type { PendingApproval } from "../../lib/api";
import { ApprovalCard } from "./ApprovalCard";

/**
 * Approval Center (Sprint 3 "signature screen ⭐⭐⭐⭐⭐"): the merchant-facing
 * queue of AI-proposed actions awaiting Approve / Reject / Modify. Supports
 * all four required screen states per NightShift AI's engineering standard,
 * matching ShiftReportView's own exported-function pattern.
 */

export function ApprovalCenterLoading() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading pending approvals">
      <div className="h-6 w-1/3 animate-pulse rounded bg-gray-100" />
      <div className="h-28 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
      <div className="h-28 animate-pulse rounded-lg border border-gray-200 bg-gray-100" />
    </div>
  );
}

export function ApprovalCenterEmpty() {
  return (
    <div className="flex min-h-[20vh] flex-col items-center justify-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-6 text-center">
      <p className="text-sm font-medium text-emerald-800">
        No pending approvals — NightShift is caught up.
      </p>
    </div>
  );
}

export function ApprovalCenterError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-6 text-center"
    >
      <p className="font-medium text-red-800">Couldn&apos;t load pending approvals</p>
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

export function ApprovalCenter({
  approvals,
  shopDomain,
  onRequireRefetch,
}: {
  approvals: PendingApproval[];
  shopDomain: string | null;
  onRequireRefetch: () => void;
}) {
  return (
    <ul className="space-y-3" aria-label="Pending AI-proposed actions">
      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.approval_id}
          approval={approval}
          shopDomain={shopDomain}
          onRequireRefetch={onRequireRefetch}
        />
      ))}
    </ul>
  );
}
