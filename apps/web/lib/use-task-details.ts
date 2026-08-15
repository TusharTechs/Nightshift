"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchTaskDetail, type TaskDetailResponse } from "./api";

/**
 * Fetches full task detail for a small, bounded set of task ids in parallel
 * (Sprint 4 Step 5's Counterfactual ROI Widget — typically 1-3 completed
 * tasks per shift, never the whole task history). Only enabled once both a
 * shop domain and at least one task id are known.
 */
export function useTaskDetails(shopDomain: string | null, taskIds: string[]) {
  return useQuery<TaskDetailResponse[]>({
    queryKey: ["task-details", shopDomain, taskIds],
    queryFn: () => Promise.all(taskIds.map((id) => fetchTaskDetail(shopDomain as string, id))),
    enabled: Boolean(shopDomain) && taskIds.length > 0,
    retry: 1,
  });
}
