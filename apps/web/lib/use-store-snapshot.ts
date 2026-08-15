"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchStoreSnapshot } from "./api";

/**
 * Fetches the baseline store snapshot (Sprint 1 Story 3: Operations Center
 * View Initialization). Polls every 5s while discovery is still in
 * progress so the health snapshot card updates without a manual refresh;
 * stops polling once discovery completes. Full real-time updates via SSE
 * (Section 2.2) are a follow-up — polling is a deliberately simple stand-in
 * that satisfies Sprint 1's "reflects scan progress" requirement without
 * needing a live SSE endpoint wired up yet.
 */
export function useStoreSnapshot(shopDomain: string | null) {
  return useQuery({
    queryKey: ["store-snapshot", shopDomain],
    queryFn: () => fetchStoreSnapshot(shopDomain as string),
    enabled: Boolean(shopDomain),
    refetchInterval: (query) => (query.state.data?.is_discovery_completed ? false : 5000),
    retry: 1,
  });
}
