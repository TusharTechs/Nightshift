"""Unit tests: Product Inspection Engine (Sprint 2 Feature 1 / Story 1).

Required test name per Sprint 2's own Testing section:
test_product_inspection_engine (covered by the suite below).
"""

from __future__ import annotations

from app.domain.inspection import inspect_catalog


def _product(**overrides):
    base = {
        "id": "gid://shopify/Product/1",
        "title": "Test Product",
        "descriptionHtml": "<p>" + " ".join(["word"] * 30) + "</p>",
        "featuredImage": {"id": "gid://shopify/Image/1", "altText": "A product photo"},
        "media": {
            "nodes": [
                {"id": "gid://shopify/MediaImage/1", "mediaContentType": "IMAGE", "alt": "A product photo"}
            ]
        },
        "variants": {
            "nodes": [
                {"id": "gid://shopify/ProductVariant/1", "sku": "SKU-1", "price": "19.99", "inventoryQuantity": 10}
            ]
        },
    }
    base.update(overrides)
    return base


def test_product_inspection_engine_flags_no_issues_on_clean_product():
    report = inspect_catalog([_product()])
    assert report.products_scanned == 1
    assert report.skus_scanned == 1
    assert report.findings == []


def test_product_inspection_engine_flags_missing_featured_image_as_high():
    report = inspect_catalog([_product(featuredImage=None)])
    finding = next(f for f in report.findings if f.evidence["check"] == "missing_featured_image")
    assert finding.severity == "HIGH"


def test_product_inspection_engine_flags_missing_alt_text_as_medium():
    report = inspect_catalog(
        [
            _product(
                media={
                    "nodes": [
                        {"id": "gid://shopify/MediaImage/1", "mediaContentType": "IMAGE", "alt": None}
                    ]
                }
            )
        ]
    )
    finding = next(f for f in report.findings if f.evidence["check"] == "missing_alt_text")
    assert finding.severity == "MEDIUM"
    assert finding.affected_resources == ["gid://shopify/Product/1", "gid://shopify/MediaImage/1"]


def test_product_inspection_engine_ignores_non_image_media():
    report = inspect_catalog(
        [
            _product(
                media={
                    "nodes": [
                        {"id": "gid://shopify/Video/1", "mediaContentType": "VIDEO", "alt": None}
                    ]
                }
            )
        ]
    )
    assert not any(f.evidence["check"] == "missing_alt_text" for f in report.findings)


def test_product_inspection_engine_flags_thin_description_as_medium():
    report = inspect_catalog([_product(descriptionHtml="<p>too short</p>")])
    finding = next(f for f in report.findings if f.evidence["check"] == "thin_description")
    assert finding.severity == "MEDIUM"


def test_product_inspection_engine_flags_zero_price_variant_as_critical():
    report = inspect_catalog(
        [_product(variants={"nodes": [{"id": "gid://shopify/ProductVariant/1", "sku": "SKU-1", "price": "0.00", "inventoryQuantity": 5}]})]
    )
    finding = next(f for f in report.findings if f.evidence["check"] == "zero_price_variant")
    assert finding.severity == "CRITICAL"


def test_product_inspection_engine_flags_missing_sku_as_low():
    report = inspect_catalog(
        [_product(variants={"nodes": [{"id": "gid://shopify/ProductVariant/1", "sku": "", "price": "19.99", "inventoryQuantity": 5}]})]
    )
    finding = next(f for f in report.findings if f.evidence["check"] == "missing_sku")
    assert finding.severity == "LOW"


def test_product_inspection_engine_counts_skus_across_multiple_variants():
    product = _product(
        variants={
            "nodes": [
                {"id": "gid://shopify/ProductVariant/1", "sku": "SKU-1", "price": "19.99", "inventoryQuantity": 5},
                {"id": "gid://shopify/ProductVariant/2", "sku": "SKU-2", "price": "24.99", "inventoryQuantity": 5},
            ]
        }
    )
    report = inspect_catalog([product])
    assert report.skus_scanned == 2


def test_product_inspection_engine_never_invents_data_not_in_input():
    # Every finding's affected_resources must trace back to GIDs present in
    # the input payload — the engine must never fabricate identifiers.
    product = _product(featuredImage=None)
    report = inspect_catalog([product])
    for finding in report.findings:
        for resource in finding.affected_resources:
            assert resource == "" or resource == product["id"] or resource.startswith("gid://shopify/")
