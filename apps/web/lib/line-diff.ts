/**
 * Client-side line-level diff (Sprint 5 Phase 3.2, Theme Guardian's diff
 * card). A classic LCS-based diff, not the backend's own coarse
 * index-aligned `_line_diff_count` (`domain/theme_inspection.py`) — that
 * one only needs a rough "how many lines changed" count to size an LLM
 * prompt/severity signal, never to render to a merchant. This one has to be
 * accurate: a single inserted/removed line must not make every following
 * line look "changed." No new dependency — the algorithm itself is
 * deterministic and operates only on the two real file contents already
 * fetched, never inventing a line that isn't in one of them.
 */

export type DiffLine = { type: "unchanged" | "added" | "removed"; content: string };

// Guards against pathological input sizes (an LCS table is O(n*m) time and
// space) — a Liquid section/snippet file is realistically a few hundred
// lines at most. Above this, callers should fall back to showing both
// files without line-level highlighting rather than freeze the tab.
const MAX_DIFF_CELLS = 400_000;

export function computeLineDiff(baseline: string, current: string): DiffLine[] | null {
  const a = baseline.split("\n");
  const b = current.split("\n");
  const n = a.length;
  const m = b.length;

  if (n * m > MAX_DIFF_CELLS) return null;

  // Non-null assertions below are safe: every index used is within
  // [0, n]/[0, m] by construction of the loop bounds and the `dp` array's
  // own dimensions ((n+1) x (m+1)), which `noUncheckedIndexedAccess` can't
  // itself prove.
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
    }
  }

  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      result.push({ type: "unchanged", content: a[i]! });
      i++;
      j++;
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      result.push({ type: "removed", content: a[i]! });
      i++;
    } else {
      result.push({ type: "added", content: b[j]! });
      j++;
    }
  }
  while (i < n) {
    result.push({ type: "removed", content: a[i]! });
    i++;
  }
  while (j < m) {
    result.push({ type: "added", content: b[j]! });
    j++;
  }
  return result;
}
