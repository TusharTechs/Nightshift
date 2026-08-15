"""Integration test: full Sprint 2 shift compilation workflow (scan → reason
→ compile → persist).

Named test required by Sprint 2's own Testing section:
test_full_shift_compilation_workflow.

Exercises `domain.inspection.inspect_catalog`, `ProductQualityAgent`
(LLM call swapped for a fake, matching the unit suite's pattern),
`domain.health.calculate_store_health`, and
`domain.shift_compiler.compile_shift_report` together end-to-end, then
asserts the final payload is exactly the shape `SqlShiftReportRepository`
would persist to `shift_reports.report_json` and that
`GET /api/v1/shifts/latest` would return verbatim.

This mirrors this repo's own established integration-test convention from
Sprint 1 (`test_oauth_callback.py`, `test_discovery_task_enqueued.py`):
persistence is swapped for in-memory/fake collaborators rather than a live
Postgres connection, since no Postgres instance is available in this test
environment. Exercising the same workflow against a real database (raw SQL
inserts into `shifts`/`issues`/`shift_reports` via Alembic-migrated schema)
is called out as a Known Limitation / manual QA step in the Sprint 2
completion report.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.agents.product_quality import (
    AgentAnalysisResponse,
    DetectedIssueSchema,
    ProductQualityAgent,
)
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.health import ScoredIssue, calculate_store_health
from app.domain.inspection import inspect_catalog
from app.domain.shift_compiler import CompiledIssue, compile_shift_report


class _FakeLlmClient:
    """Deterministic stand-in for the real LLM API call — returns issues
    that mirror the inspection findings 1:1, as a well-behaved model would.
    Provider-agnostic: works identically regardless of which concrete client
    is configured, since ProductQualityAgent only depends on this
    structural contract."""

    model_name = "fake-model-for-integration-test"

    async def generate_structured(self, *, prompt: str, response_schema):
        return AgentAnalysisResponse(
            executive_summary="Found 2 catalog issues.",
            issues=[
                DetectedIssueSchema(
                    title="Missing primary image on 'Widget'",
                    severity="HIGH",
                    description="Widget has no featured image; likely to lose conversions.",
                    revenue_impact_estimate=120.0,
                    confidence_score=0.95,
                    affected_resources=["gid://shopify/Product/1"],
                ),
                DetectedIssueSchema(
                    title="Zero-price variant on 'Widget'",
                    severity="CRITICAL",
                    description="A variant of Widget has no price set.",
                    revenue_impact_estimate=60.0,
                    confidence_score=0.99,
                    affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductVariant/9"],
                ),
            ],
        )


_RAW_PRODUCTS = [
    {
        "id": "gid://shopify/Product/1",
        "title": "Widget",
        "descriptionHtml": "<p>" + " ".join(["word"] * 30) + "</p>",
        "featuredImage": None,  # triggers HIGH: missing_featured_image
        "images": {"nodes": [{"id": "gid://shopify/Image/1", "altText": "ok"}]},
        "variants": {
            "nodes": [
                {"id": "gid://shopify/ProductVariant/9", "sku": "W-1", "price": "0.00", "inventoryQuantity": 3}
            ]
        },
    }
]


@pytest.mark.asyncio
async def test_full_shift_compilation_workflow_scan_reason_compile_persist():
    # --- Scan --------------------------------------------------------------
    inspection_report = inspect_catalog(_RAW_PRODUCTS)
    assert inspection_report.products_scanned == 1
    assert inspection_report.skus_scanned == 1
    finding_checks = {f.evidence["check"] for f in inspection_report.findings}
    assert "missing_featured_image" in finding_checks
    assert "zero_price_variant" in finding_checks

    # --- Reason (fake LLM call, matching the unit suite's pattern) --------
    agent = ProductQualityAgent(client=_FakeLlmClient())

    analysis = await agent.analyze_catalog_diff(
        {
            "findings": [
                {
                    "title": f.title,
                    "severity": f.severity,
                    "description": f.description,
                    "affected_resources": f.affected_resources,
                }
                for f in inspection_report.findings
            ]
        }
    )
    assert len(analysis.issues) == 2

    # --- Persist (simulated) issues, mirroring what SqlIssueRepository.create
    # would store as ORM rows --------------------------------------------
    persisted_issues = [
        CompiledIssue(
            id=f"issue-{i}",
            category=IssueCategory.PRODUCT_QUALITY.value,
            severity=detected.severity,
            status="OPEN",
            title=detected.title,
            description=detected.description,
            revenue_impact_estimate=detected.revenue_impact_estimate,
            confidence_score=detected.confidence_score,
            affected_resources=detected.affected_resources,
        )
        for i, detected in enumerate(analysis.issues)
    ]

    # --- Store Health Score --------------------------------------------------
    scored_issues = [
        ScoredIssue(category=IssueCategory.PRODUCT_QUALITY, severity=IssueSeverity(issue.severity))
        for issue in persisted_issues
    ]
    health_result = calculate_store_health(scored_issues)
    # HIGH (-8) + CRITICAL (-15) in PRODUCT_QUALITY (cap 20) = -20, bounded at cap
    assert health_result.category_deductions["PRODUCT_QUALITY"] == 20
    assert health_result.score == 80

    # --- Compile -------------------------------------------------------------
    started_at = datetime(2026, 7, 31, 2, 0, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 7, 31, 2, 3, 12, tzinfo=timezone.utc)
    payload = compile_shift_report(
        shift_id="33333333-3333-3333-3333-333333333333",
        shift_number=1,
        started_at=started_at,
        completed_at=completed_at,
        issues=persisted_issues,
        health_result=health_result,
    )

    assert payload.issues_detected == 2
    assert payload.issues_resolved == 0
    assert payload.estimated_revenue_protected == 180.0
    assert payload.health_score == 80

    # --- API response shape (what GET /api/v1/shifts/latest returns and what
    # SqlShiftReportRepository.create would persist to report_json) --------
    api_response = payload.to_api_response()
    assert api_response["shift_id"] == "33333333-3333-3333-3333-333333333333"
    assert api_response["health_score"] == 80
    assert api_response["metrics"]["issues_detected"] == 2
    assert api_response["metrics"]["estimated_revenue_protected_usd"] == 180.0
    assert len(api_response["issues"]) == 2
    # Issues sorted by revenue_impact_estimate descending (Story 3, verbatim:
    # "Displays sorted list of issues prioritized by revenue_impact_estimate").
    assert api_response["issues"][0]["revenue_impact_estimate"] >= api_response["issues"][1]["revenue_impact_estimate"]


@pytest.mark.asyncio
async def test_full_shift_compilation_workflow_zero_issues_renders_all_clear():
    payload = compile_shift_report(
        shift_id="00000000-0000-0000-0000-000000000000",
        shift_number=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        issues=[],
        health_result=calculate_store_health([]),
    )
    assert payload.health_score == 100
    assert "all systems operational" in payload.executive_summary.lower()
    assert payload.to_api_response()["issues"] == []
