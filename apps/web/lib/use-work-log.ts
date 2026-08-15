"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchWorkLog } from "./api";

/**
 * Fetches the first page of the AI Work Log (Sprint 3 AI Trust & Execution).
 * Pagination beyond the first page is handled locally by the `WorkLog`
 * component's own "Load more" button (a simple local-state append), not by
 * this hook, matching the sprint scope's "no full infinite-scroll machinery"
 * instruction.
 */
export function useWorkLog(shopDomain: string | null) {
  return useQuery({
    queryKey: ["work-log", shopDomain],
    queryFn: () => fetchWorkLog(shopDomain as string),
    enabled: Boolean(shopDomain),
    retry: 1,
  });
}
