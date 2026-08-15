"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchPendingApprovals,
  submitApprovalAction,
  type ApprovalDecisionAction,
} from "./api";

/**
 * Fetches the Approval Center's pending queue (Sprint 3 AI Trust &
 * Execution). Not polled — the merchant drives refetches by acting on a
 * card (see `useSubmitApprovalAction` below) or manually via `refetch()`,
 * matching `useLatestShift`'s own "cheap to refetch on demand, not a
 * moment-to-moment live feed" posture.
 */
export function usePendingApprovals(shopDomain: string | null) {
  return useQuery({
    queryKey: ["pending-approvals", shopDomain],
    queryFn: () => fetchPendingApprovals(shopDomain as string),
    enabled: Boolean(shopDomain),
    retry: 1,
  });
}

/**
 * Submits an APPROVE/REJECT/DEFER (or APPROVE-with-overrides, i.e. "Modify")
 * decision for a single pending approval. On success, invalidates both the
 * pending-approvals list (the acted-on card should disappear) and the
 * latest-shift query (approving/rejecting moves the shift report's own
 * `pending_approvals`/`completed_tasks` counts).
 */
export function useSubmitApprovalAction(shopDomain: string | null) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (vars: {
      approvalId: string;
      action: ApprovalDecisionAction;
      rejection_reason?: string;
      execution_override_params?: Record<string, unknown>;
    }) =>
      submitApprovalAction(shopDomain as string, vars.approvalId, {
        action: vars.action,
        rejection_reason: vars.rejection_reason,
        execution_override_params: vars.execution_override_params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pending-approvals", shopDomain] });
      queryClient.invalidateQueries({ queryKey: ["latest-shift", shopDomain] });
    },
  });
}
