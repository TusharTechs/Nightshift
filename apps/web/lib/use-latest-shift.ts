"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchLatestShift } from "./api";

/**
 * Fetches the latest Morning Shift Report (Sprint 2 Story 3: Morning Shift
 * Report Review). Not polled by default — shift compilation is a nightly
 * background job, not something that changes moment-to-moment while the
 * Operations Center is open — but stays cheap to refetch on demand via the
 * hook's own `refetch()`.
 */
export function useLatestShift(shopDomain: string | null) {
  return useQuery({
    queryKey: ["latest-shift", shopDomain],
    queryFn: () => fetchLatestShift(shopDomain as string),
    enabled: Boolean(shopDomain),
    retry: 1,
  });
}
