"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchLatestActiveShiftReplay } from "./api";

/**
 * Fetches the Shift Replay timeline for whichever recent shift actually has
 * activity (Sprint 5 Phase 1.2 — server-side fallback through recent
 * shifts, see `GET /api/v1/shifts/replay/latest-active`). Only enabled once
 * a shop domain is known.
 */
export function useShiftReplay(shopDomain: string | null) {
  return useQuery({
    queryKey: ["shift-replay-latest-active", shopDomain],
    queryFn: () => fetchLatestActiveShiftReplay(shopDomain as string),
    enabled: Boolean(shopDomain),
    retry: 1,
  });
}
