"""Integration test: Chief Ops AI synthesis wired into shift compilation
(Sprint 4 Step 4). Mirrors `test_full_shift_compilation_workflow.py`'s own
style — domain-level workflow, no database — exercising
`build_specialist_turns` -> `should_synthesize` -> `ChiefOpsSynthesizer`/
`deterministic_briefing` -> `compile_shift_report` end-to-end, then asserting
the final `report_json` shape `GET /api/v1/shifts/latest` would return.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.chief_ops import (
    ChiefOpsSynthesisSchema,
    ChiefOpsSynthesizer,
    build_specialist_turns,
    deterministic_briefing,
    should_synthesize,
)
from app.domain.health import calculate_store_health
from app.domain.shift_compiler import CompiledIssue, compile_shift_report


class _FakeLlmClient:
    model_name = "fake-model-for-integration-test"

    async def generate_structured(self, *, prompt: str, response_schema):
        assert response_schema is ChiefOpsSynthesisSchema
        return ChiefOpsSynthesisSchema(
            narrative="Theme Guardian caught a removed Buy Button block at the same time Tracking "
            "Specialist saw the Meta Pixel disappear — almost certainly one rogue deploy.",
            correlated=True,
        )


async def test_two_specialist_shift_produces_llm_synthesized_briefing_in_report_json():
    theme_issue = CompiledIssue(
        id="issue-theme",
        category="CHECKOUT",
        severity="HIGH",
        status="AWAITING_APPROVAL",
        title="Theme file 'sections/main-product.liquid' changed unexpectedly",
        description="The Buy Button include was removed.",
        revenue_impact_estimate=0.0,
        confidence_score=0.9,
        affected_resources=["gid://shopify/OnlineStoreTheme/1"],
    )
    tracking_issue = CompiledIssue(
        id="issue-tracking",
        category="PIXEL_TRACKING",
        severity="HIGH",
        status="RESOLVED",
        title="Meta Pixel script tag removed",
        description="A previously-installed Meta Pixel script tag is now missing.",
        revenue_impact_estimate=0.0,
        confidence_score=0.95,
        affected_resources=["https://connect.facebook.net/en_US/fbevents.js"],
    )

    turns = build_specialist_turns(
        [theme_issue, tracking_issue],
        resolved_issue_ids={"issue-tracking"},
        timestamps={"issue-theme": "2026-08-01T02:00:00+00:00", "issue-tracking": "2026-08-01T02:00:05+00:00"},
    )
    assert should_synthesize(turns) is True

    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClient())
    briefing = await synthesizer.synthesize(turns)
    assert briefing.correlated is True
    assert briefing.used_llm is True

    health_result = calculate_store_health([])
    payload = compile_shift_report(
        shift_id="44444444-4444-4444-4444-444444444444",
        shift_number=5,
        started_at=datetime(2026, 8, 1, 2, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 1, 2, 5, 0, tzinfo=timezone.utc),
        issues=[theme_issue, tracking_issue],
        health_result=health_result,
        chief_ops_briefing=briefing.to_dict(),
    )

    api_response = payload.to_api_response()
    assert api_response["chief_ops_briefing"]["correlated"] is True
    assert api_response["chief_ops_briefing"]["used_llm"] is True
    assert len(api_response["chief_ops_briefing"]["turns"]) == 2
    icons = {t["issue_id"]: t["icon"] for t in api_response["chief_ops_briefing"]["turns"]}
    assert icons["issue-tracking"] == "⚡"  # auto-resolved this shift
    assert icons["issue-theme"] == "🧠"  # awaiting merchant approval


async def test_single_specialist_shift_still_calls_llm_for_an_executive_memo():
    # Sprint 5 Phase 1.3: a single specialist's findings still get a real
    # LLM synthesis pass — this reverses the original 2+-category-only gate
    # (see CONFLICTS.md item 48).
    issue = CompiledIssue(
        id="issue-1",
        category="PRODUCT_QUALITY",
        severity="LOW",
        status="OPEN",
        title="Missing alt text",
        description="A product image has no alt text.",
        revenue_impact_estimate=10.0,
        confidence_score=0.8,
        affected_resources=["gid://shopify/Product/1"],
    )
    turns = build_specialist_turns([issue])
    assert should_synthesize(turns) is True

    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClient())
    briefing = await synthesizer.synthesize(turns)
    payload = compile_shift_report(
        shift_id="55555555-5555-5555-5555-555555555555",
        shift_number=6,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        issues=[issue],
        health_result=calculate_store_health([]),
        chief_ops_briefing=briefing.to_dict(),
    )

    api_response = payload.to_api_response()
    assert api_response["chief_ops_briefing"]["used_llm"] is True


async def test_zero_finding_shift_still_uses_deterministic_all_clear_briefing():
    # The one remaining case that skips the LLM entirely.
    turns = build_specialist_turns([])
    assert should_synthesize(turns) is False

    briefing = deterministic_briefing(turns)
    payload = compile_shift_report(
        shift_id="66666666-6666-6666-6666-666666666666",
        shift_number=7,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        issues=[],
        health_result=calculate_store_health([]),
        chief_ops_briefing=briefing.to_dict(),
    )

    api_response = payload.to_api_response()
    assert api_response["chief_ops_briefing"]["used_llm"] is False
    assert "all clear" in api_response["chief_ops_briefing"]["narrative"].lower()


def test_zero_issue_shift_report_json_carries_chief_ops_briefing_as_none_by_default():
    payload = compile_shift_report(
        shift_id="00000000-0000-0000-0000-000000000001",
        shift_number=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        issues=[],
        health_result=calculate_store_health([]),
    )
    # A caller that never wires Chief Ops (e.g. an older test/path) keeps the
    # field present, just None — never a KeyError for any existing consumer.
    assert payload.to_api_response()["chief_ops_briefing"] is None
