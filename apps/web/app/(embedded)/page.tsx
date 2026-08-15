"use client";

import { useEffect, useState } from "react";

import {
  getShopifyAdminDeepLink,
  initializeAppBridge,
  isEmbeddedInShopifyAdmin,
} from "../../lib/app-bridge";
import { useStoreSnapshot } from "../../lib/use-store-snapshot";
import { useLatestShift } from "../../lib/use-latest-shift";
import { usePendingApprovals } from "../../lib/use-pending-approvals";
import { useWorkLog } from "../../lib/use-work-log";
import { HealthScoreGauge } from "../../components/shared/HealthScoreGauge";
import { OperationsHeader } from "../../components/shared/OperationsHeader";
import { ScanProgressBanner } from "../../components/shared/ScanProgressBanner";
import { ShiftStatusCard } from "../../components/shift/ShiftStatusCard";
import {
  ShiftReportView,
  ShiftReportViewEmpty,
  ShiftReportViewError,
  ShiftReportViewLoading,
} from "../../components/shift/ShiftReportView";
import { StatTile } from "../../components/shared/StatTile";
import { CategoryHealthTile } from "../../components/shared/CategoryHealthTile";
import {
  ApprovalCenter,
  ApprovalCenterEmpty,
  ApprovalCenterError,
  ApprovalCenterLoading,
} from "../../components/approvals/ApprovalCenter";
import { WorkLog, WorkLogEmpty, WorkLogError, WorkLogLoading } from "../../components/work-log/WorkLog";
import { AskNightShift } from "../../components/ask/AskNightShift";
import { ChaosPanel } from "../../components/shared/ChaosPanel";

function formatUsd(value: number): string {
  return value.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function SkeletonCard() {
  return (
    <div className="h-24 animate-pulse rounded-lg border border-gray-200 bg-gray-100" aria-hidden="true" />
  );
}

export default function OperationsCenterPage() {
  const [shopParam, setShopParam] = useState<string | null>(null);
  const [embedded, setEmbedded] = useState<boolean | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const shop = params.get("shop");
    const host = params.get("host");
    setShopParam(shop);

    const inAdmin = isEmbeddedInShopifyAdmin();
    setEmbedded(inAdmin);

    if (inAdmin && host) {
      initializeAppBridge({
        apiKey: process.env.NEXT_PUBLIC_SHOPIFY_APP_CLIENT_ID ?? "",
        host,
      });
    }
  }, []);

  const { data: store, isLoading, isError, error, refetch } = useStoreSnapshot(shopParam);
  const {
    data: latestShift,
    isLoading: isShiftLoading,
    isError: isShiftError,
    error: shiftError,
    refetch: refetchShift,
  } = useLatestShift(shopParam);
  const {
    data: pendingApprovals,
    isLoading: isApprovalsLoading,
    isError: isApprovalsError,
    error: approvalsError,
    refetch: refetchApprovals,
  } = usePendingApprovals(shopParam);
  const {
    data: workLog,
    isLoading: isWorkLogLoading,
    isError: isWorkLogError,
    error: workLogError,
    refetch: refetchWorkLog,
  } = useWorkLog(shopParam);

  // --- Error state: not embedded in Shopify Admin --------------------------
  if (embedded === false) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
        <p className="text-lg font-medium text-gray-900">
          NightShift AI must be opened from Shopify Admin
        </p>
        <a
          href={getShopifyAdminDeepLink(shopParam)}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
        >
          Open in Shopify Admin
        </a>
      </div>
    );
  }

  // --- Loading state ---------------------------------------------------------
  if (embedded === null || isLoading) {
    return (
      <div className="space-y-6" aria-busy="true" aria-label="Loading store snapshot">
        <div className="h-10 animate-pulse rounded bg-gray-100" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  }

  // --- Error state: API request failed ---------------------------------------
  if (isError) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-4 text-center">
        <p className="text-lg font-medium text-gray-900">Couldn&apos;t load your store snapshot</p>
        <p className="text-sm text-gray-500">{error instanceof Error ? error.message : "Unknown error"}</p>
        <button
          onClick={() => refetch()}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95"
        >
          Retry
        </button>
      </div>
    );
  }

  // --- Empty state: no store connected yet (shouldn't normally happen once
  // OAuth has completed, but the embedded shell can render before the shop
  // param arrives) ------------------------------------------------------------
  if (!store) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-2 text-center">
        <p className="text-lg font-medium text-gray-900">No store connected yet</p>
        <p className="text-sm text-gray-500">Install NightShift AI from the Shopify App Store to get started.</p>
      </div>
    );
  }

  // --- Success state -----------------------------------------------------------
  const aiState = store.is_discovery_completed ? "idle" : "scanning";

  return (
    <div className="space-y-6">
      <OperationsHeader
        shopifyDomain={store.shopify_domain}
        aiActivityState={aiState}
        chiefOpsActive={Boolean(latestShift?.chief_ops_briefing?.used_llm)}
        latestShiftNumber={latestShift?.shift_number}
        latestShiftStatus={latestShift?.status}
      />

      <ScanProgressBanner isComplete={store.is_discovery_completed} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="flex items-center justify-center rounded-lg border border-gray-200 bg-white p-4">
          <HealthScoreGauge score={store.health_score} />
        </div>
        <div className="grid grid-cols-1 gap-4">
          <StatTile
            label="Issues Fixed Overnight"
            value={
              latestShift
                ? String((latestShift.completed_tasks ?? []).filter((t) => t.verified).length)
                : "—"
            }
          />
          <StatTile
            label="Revenue Protected"
            value={latestShift ? formatUsd(latestShift.metrics.estimated_revenue_protected_usd) : "—"}
          />
        </div>
        <div className="grid grid-cols-1 gap-4">
          {/* Sprint 5 Phase 1.1: all three tiles are now live — Checkout
              Specialist (discount/pricing), Product Quality (catalog
              content), and Theme Guardian (theme/storefront integrity) are
              real, running specialists as of Sprint 4/2/4 respectively.
              Bound to `DISCOUNT`/`PRODUCT_QUALITY`/`CHECKOUT` — `CHECKOUT` is
              Theme Guardian's reused category, not a literal "checkout"
              specialist (see `lib/health-categories.ts`'s own naming note).
              Labeled "Catalog Quality," not "SEO" — Product Quality checks
              images/alt-text/descriptions, which is SEO-adjacent but not a
              dedicated SEO specialist; no tile here claims a capability that
              doesn't exist yet. Icons mirror the same specialist avatars
              used in the Work Log/Shift Replay/Executive Briefing
              (`lib/specialist-identity.ts`, Sprint 5 Phase 5 micro-polish). */}
          <CategoryHealthTile
            label="Checkout (Discounts & Pricing)"
            icon="💳"
            category="DISCOUNT"
            deductions={latestShift?.health_category_deductions ?? null}
          />
          <CategoryHealthTile
            label="Catalog Quality"
            icon="📦"
            category="PRODUCT_QUALITY"
            deductions={latestShift?.health_category_deductions ?? null}
          />
          <CategoryHealthTile
            label="Theme & Storefront"
            icon="🛡️"
            category="CHECKOUT"
            deductions={latestShift?.health_category_deductions ?? null}
          />
        </div>
      </div>

      <section aria-labelledby="baseline-shift-heading">
        <h2 id="baseline-shift-heading" className="sr-only">
          Baseline Operational Snapshot
        </h2>
        <ShiftStatusCard isComplete={store.is_discovery_completed} />
      </section>

      {/* OperationsHeader's AIActivityBadge already carries role="status" +
          aria-label, so screen readers announce state changes from there —
          no separate live region needed. */}

      {/* Sprint 3 AI Trust & Execution: the Approval Center — NightShift AI's
          signature screen. Positioned above the Morning Shift Report since
          it's the one section that actually needs the merchant's action;
          same four-state pattern as everything else in this file. */}
      <section aria-labelledby="pending-actions-heading" className="space-y-3">
        <h2 id="pending-actions-heading" className="text-lg font-semibold text-gray-900">
          Pending Actions
        </h2>
        {isApprovalsLoading ? (
          <ApprovalCenterLoading />
        ) : isApprovalsError ? (
          <ApprovalCenterError
            message={approvalsError instanceof Error ? approvalsError.message : "Unknown error"}
            onRetry={() => refetchApprovals()}
          />
        ) : pendingApprovals && pendingApprovals.length > 0 ? (
          <ApprovalCenter
            approvals={pendingApprovals}
            shopDomain={shopParam}
            onRequireRefetch={() => refetchApprovals()}
          />
        ) : (
          <ApprovalCenterEmpty />
        )}
      </section>

      {/* Sprint 2 Feature 5: Morning Shift Report, integrated into the
          Operations Center. Its four states (loading/empty/success/error)
          are independent of the baseline snapshot's own states above — the
          shift report simply hasn't been generated yet on a brand-new
          install, which is a normal empty state, not an error. */}
      {isShiftLoading ? (
        <ShiftReportViewLoading />
      ) : isShiftError ? (
        <ShiftReportViewError
          message={shiftError instanceof Error ? shiftError.message : "Unknown error"}
          onRetry={() => refetchShift()}
        />
      ) : latestShift ? (
        <ShiftReportView shift={latestShift} shopDomain={shopParam} />
      ) : (
        <ShiftReportViewEmpty />
      )}

      {/* Sprint 4 Step 4: Ask NightShift — the conversational surface over
          Chief Ops AI's same underlying synthesis (Vision doc Phase B).
          Only rendered once a shop domain is known, same guard the rest of
          this page already relies on for every shop-scoped API call. */}
      {shopParam ? <AskNightShift shopDomain={shopParam} /> : null}

      {/* Sprint 3 AI Trust & Execution: the Work Log — the append-only audit
          trail everything the AI has done, building merchant trust over
          time. Same four-state pattern. */}
      <section aria-labelledby="work-log-heading" className="space-y-3">
        <h2 id="work-log-heading" className="text-lg font-semibold text-gray-900">
          Work Log
        </h2>
        {isWorkLogLoading ? (
          <WorkLogLoading />
        ) : isWorkLogError ? (
          <WorkLogError
            message={workLogError instanceof Error ? workLogError.message : "Unknown error"}
            onRetry={() => refetchWorkLog()}
          />
        ) : workLog && workLog.data.length > 0 ? (
          <WorkLog
            key={workLog.data[0]?.id ?? "work-log"}
            initialEntries={workLog.data}
            hasMore={workLog.has_more}
            nextCursor={workLog.next_cursor}
            shopDomain={shopParam}
          />
        ) : (
          <WorkLogEmpty />
        )}
      </section>

      {/* Sprint 5 Phase 4: the Chaos Panel — only rendered when the
          backend's own demo_mode_enabled flag is on, so this floating
          control never appears (and never promises a capability that
          would 404) in a real merchant's production deployment. */}
      {shopParam && store.demo_mode_enabled ? <ChaosPanel shopDomain={shopParam} /> : null}
    </div>
  );
}
