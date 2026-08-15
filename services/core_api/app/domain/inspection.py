"""Product Inspection Engine — Sprint 2 Feature 1 / Story 1.

Pure domain logic — no framework or Shopify-client imports. Takes raw
Shopify product GraphQL nodes (as returned by
`ShopifyGraphQLClient.fetch_catalog_for_inspection`) and deterministically
identifies catalog-quality defects, grouped by severity, before any AI
reasoning runs.

The Product Quality Agent (`domain/agents/product_quality.py`) consumes this
engine's findings as its `inspection_data` context and layers on
revenue-impact estimation, confidence scoring, and natural-language
explanations. This engine itself never estimates revenue impact or invents
data not present in the Shopify catalog payload — it only reports what it
observed (AI Specification Safety Rule: "Never invent fake product GIDs";
Ground all ... estimates in catalog data actually provided").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_DESCRIPTION_WORDS = 20
"""Sprint 2 Feature 1, verbatim: "Products with empty or short descriptions
(<20 words)"."""


@dataclass(frozen=True)
class InspectionFinding:
    title: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    description: str
    affected_resources: list[str]
    evidence: dict


@dataclass(frozen=True)
class InspectionReport:
    products_scanned: int
    skus_scanned: int
    findings: list[InspectionFinding] = field(default_factory=list)


def _word_count(html_or_text: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    return len([w for w in text.split() if w.strip()])


def inspect_catalog(products: list[dict]) -> InspectionReport:
    """Scans Shopify product nodes and returns grouped catalog-quality
    findings.

    Story 1 acceptance criteria groups findings by severity (verbatim):
      CRITICAL — $0 price / broken variant
      HIGH     — missing main image
      MEDIUM   — missing ALT text / short description
      LOW      — unorganized tags

    This engine does not fetch collection/tag data (out of scope for the
    catalog GraphQL query built this sprint — see Known Limitations in the
    Sprint 2 completion report), so the LOW bucket here instead covers the
    two remaining sub-conditions named in Feature 1's combined check
    ("Variants with missing SKU identifiers, zero inventory, or $0 pricing
    anomalies"): a variant missing a SKU, and a variant with zero on-hand
    inventory. Neither is called "critical" or "broken" anywhere in Story 1,
    so LOW is the most defensible bucket for them.
    """
    findings: list[InspectionFinding] = []
    sku_count = 0

    for product in products:
        gid = product.get("id", "")
        title = product.get("title") or "(untitled product)"

        if not product.get("featuredImage"):
            findings.append(
                InspectionFinding(
                    title=f"Missing primary image on '{title}'",
                    severity="HIGH",
                    description=(
                        f"Product '{title}' has no primary/featured image set. "
                        "Shoppers are significantly less likely to purchase a "
                        "product with no visible image."
                    ),
                    affected_resources=[gid],
                    evidence={"product_id": gid, "check": "missing_featured_image"},
                )
            )

        # `media` (not `images`) — `product.images`'s `ProductImage` GIDs
        # cannot be used to actually fix a missing ALT text (Shopify's
        # `fileUpdate` mutation only accepts a `MediaImage` GID, a distinct
        # ID from `ProductImage`'s, not just a differently-typed alias for
        # the same one — confirmed via live schema introspection during
        # Sprint 3 E2E testing). Detection must capture the same GID type
        # the Execute step will actually need, or the auto-fix mutation
        # fails against every real finding this engine produces.
        for media_node in product.get("media", {}).get("nodes", []):
            if media_node.get("mediaContentType") != "IMAGE":
                continue
            if not media_node.get("alt"):
                findings.append(
                    InspectionFinding(
                        title=f"Image missing ALT text on '{title}'",
                        severity="MEDIUM",
                        description=(
                            f"An image on '{title}' has no ALT text, hurting "
                            "accessibility and SEO image-search visibility."
                        ),
                        affected_resources=[gid, media_node.get("id", "")],
                        evidence={
                            "product_id": gid,
                            "image_id": media_node.get("id"),
                            "check": "missing_alt_text",
                        },
                    )
                )

        description_words = _word_count(product.get("descriptionHtml", ""))
        if description_words < MIN_DESCRIPTION_WORDS:
            findings.append(
                InspectionFinding(
                    title=f"Thin product description on '{title}'",
                    severity="MEDIUM",
                    description=(
                        f"Product '{title}' has only {description_words} word(s) of "
                        f"description (minimum recommended: {MIN_DESCRIPTION_WORDS}). "
                        "Thin descriptions reduce SEO ranking and conversion rate."
                    ),
                    affected_resources=[gid],
                    evidence={
                        "product_id": gid,
                        "word_count": description_words,
                        "check": "thin_description",
                    },
                )
            )

        for variant in product.get("variants", {}).get("nodes", []):
            sku_count += 1
            variant_gid = variant.get("id", "")
            price = variant.get("price")
            price_value = float(price) if price not in (None, "") else 0.0

            if price_value <= 0:
                findings.append(
                    InspectionFinding(
                        title=f"Zero-price variant on '{title}'",
                        severity="CRITICAL",
                        description=(
                            f"A variant of '{title}' is priced at $0 or has no "
                            "price set — this variant cannot be sold as configured."
                        ),
                        affected_resources=[gid, variant_gid],
                        evidence={
                            "product_id": gid,
                            "variant_id": variant_gid,
                            "check": "zero_price_variant",
                        },
                    )
                )

            if not variant.get("sku"):
                findings.append(
                    InspectionFinding(
                        title=f"Variant missing SKU on '{title}'",
                        severity="LOW",
                        description=(
                            f"A variant of '{title}' has no SKU identifier set, "
                            "which complicates inventory reconciliation."
                        ),
                        affected_resources=[gid, variant_gid],
                        evidence={
                            "product_id": gid,
                            "variant_id": variant_gid,
                            "check": "missing_sku",
                        },
                    )
                )

            inventory_quantity = variant.get("inventoryQuantity")
            if inventory_quantity is not None and inventory_quantity <= 0:
                findings.append(
                    InspectionFinding(
                        title=f"Zero inventory on '{title}'",
                        severity="LOW",
                        description=(
                            f"A variant of '{title}' shows zero on-hand inventory."
                        ),
                        affected_resources=[gid, variant_gid],
                        evidence={
                            "product_id": gid,
                            "variant_id": variant_gid,
                            "check": "zero_inventory",
                        },
                    )
                )

    return InspectionReport(
        products_scanned=len(products),
        skus_scanned=sku_count,
        findings=findings,
    )
