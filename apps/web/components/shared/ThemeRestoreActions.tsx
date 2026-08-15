"use client";

import { useState } from "react";

/**
 * The guided-resolution action area for a Theme Guardian restore: copy the
 * exact restore patch to the clipboard, and a 1-click deep link to the
 * theme's code editor in Shopify Admin. Extracted from `WorkLog.tsx`'s
 * `RestoreGuidePanel` (Sprint 4 Step 3) so Sprint 5 Phase 3.2's Approval
 * Center diff card can reuse the exact same action area rather than
 * rebuilding it — the merchant sees identical Copy/Open-Editor behavior
 * whether they're looking at a pending approval or an already-executed
 * Work Log entry.
 */
export function ThemeRestoreActions({
  filename,
  themeEditorUrl,
  patchContent,
}: {
  filename: string;
  themeEditorUrl: string;
  patchContent: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(patchContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API can fail (permissions, non-secure context) — the
      // content is still visible/selectable alongside this, so this never
      // blocks the merchant from finishing the restore manually.
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <a
        href={themeEditorUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
      >
        Open Theme Editor ↗
      </a>
      <button
        type="button"
        onClick={handleCopy}
        className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-all duration-150 hover:bg-gray-100 active:scale-95"
      >
        {copied ? "✓ Copied" : `Copy restored ${filename}`}
      </button>
    </div>
  );
}
