/**
 * Shared mirror of `domain/health.py::CATEGORY_CAPS` (Sprint 2 Store Health
 * Engine) — the frontend has no live endpoint for "what's the max possible
 * deduction per category," so this is a presentation-only duplicate of a
 * fixed, rarely-changing table, same as `HealthBreakdownWidget.tsx` already
 * did inline before Sprint 5 pulled it out into one shared place.
 *
 * Naming note worth remembering when wiring a tile to a specialist: the
 * `IssueCategory.CHECKOUT` bucket is Theme Guardian's (reused from an
 * earlier reservation — see CONFLICTS.md item 36), NOT Checkout
 * Specialist's. Checkout Specialist's real category is `DISCOUNT`.
 */

export const CATEGORY_KEYS = [
  "CHECKOUT",
  "PIXEL_TRACKING",
  "PRODUCT_QUALITY",
  "SEO",
  "DISCOUNT",
  "PERFORMANCE",
] as const;

export type HealthCategoryKey = (typeof CATEGORY_KEYS)[number];

export const CATEGORY_LABELS: Record<HealthCategoryKey, string> = {
  CHECKOUT: "Theme & Checkout Health",
  PIXEL_TRACKING: "Pixel Tracking",
  PRODUCT_QUALITY: "Product Quality",
  SEO: "SEO",
  DISCOUNT: "Discounts",
  PERFORMANCE: "Performance",
};

export const CATEGORY_CAPS: Record<HealthCategoryKey, number> = {
  CHECKOUT: 25,
  PIXEL_TRACKING: 20,
  PRODUCT_QUALITY: 20,
  SEO: 15,
  DISCOUNT: 10,
  PERFORMANCE: 10,
};

/** Percentage of this category's own points still intact (0-100). */
export function categoryHealthPercent(
  deductions: Record<string, number>,
  category: HealthCategoryKey
): number {
  const deducted = deductions[category] ?? 0;
  const cap = CATEGORY_CAPS[category];
  const remaining = cap - deducted;
  return cap > 0 ? Math.max(0, Math.min(100, Math.round((remaining / cap) * 100))) : 100;
}
