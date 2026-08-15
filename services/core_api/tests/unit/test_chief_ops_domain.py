"""Unit tests for Chief Ops AI's domain logic (Sprint 4 Step 4; synthesis
gate updated in Sprint 5 Phase 1.3) — turn building/icon assignment, the
1+-finding synthesis gate, and `ChiefOpsSynthesizer`'s LLM call with
fallback. Mirrors `test_theme_guardian_agent.py`'s fake-LLM-client style.
"""

from __future__ import annotations

from app.domain.chief_ops import (
    _PROMPT_TEMPLATE,
    ChiefOpsSynthesisSchema,
    ChiefOpsSynthesizer,
    build_specialist_turns,
    deterministic_briefing,
    should_synthesize,
)
from app.domain.shift_compiler import CompiledIssue
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError
from app.infrastructure.llm.errors import LlmSchemaValidationError


def _issue(**overrides) -> CompiledIssue:
    defaults = dict(
        id="issue-1",
        category="PRODUCT_QUALITY",
        severity="HIGH",
        status="OPEN",
        title="Missing alt text",
        description="A product image has no alt text.",
        revenue_impact_estimate=50.0,
        confidence_score=0.9,
        affected_resources=["gid://shopify/Product/1"],
    )
    defaults.update(overrides)
    return CompiledIssue(**defaults)


class _FakeLlmClientSuccess:
    model_name = "fake-model-success"

    async def generate_structured(self, *, prompt: str, response_schema):
        assert response_schema is ChiefOpsSynthesisSchema
        return ChiefOpsSynthesisSchema(
            narrative="Theme Guardian and Tracking Specialist both flagged changes at the same time — likely one incident.",
            correlated=True,
        )


class _FakeLlmClientFailure:
    model_name = "fake-model-failure"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise LlmSchemaValidationError("simulated schema validation failure")


class _ClientThatMustNotBeCalled:
    model_name = "must-not-be-called"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise AssertionError("LLM must never be called when synthesis should be skipped")


class _AlwaysExceededBudgetGuard:
    async def check_and_increment(self) -> int:
        raise LlmBudgetExceededError("simulated daily budget exhausted")


# --- build_specialist_turns / icon assignment -------------------------------


def test_build_specialist_turns_assigns_lightning_icon_to_auto_resolved_issue():
    issue = _issue(id="issue-1", status="RESOLVED")
    turns = build_specialist_turns([issue], resolved_issue_ids={"issue-1"})
    assert turns[0].icon == "⚡"


def test_build_specialist_turns_assigns_brain_icon_to_awaiting_approval_issue():
    issue = _issue(id="issue-2", status="AWAITING_APPROVAL")
    turns = build_specialist_turns([issue], resolved_issue_ids=set())
    assert turns[0].icon == "🧠"


def test_build_specialist_turns_assigns_green_icon_to_open_informational_issue():
    issue = _issue(id="issue-3", status="OPEN")
    turns = build_specialist_turns([issue], resolved_issue_ids=set())
    assert turns[0].icon == "🟢"


def test_build_specialist_turns_maps_category_to_known_agent_display_name():
    issue = _issue(category="PIXEL_TRACKING")
    turns = build_specialist_turns([issue])
    assert turns[0].agent_name == "Tracking Specialist"


def test_build_specialist_turns_falls_back_to_raw_category_for_unmapped_category():
    issue = _issue(category="INVENTORY")
    turns = build_specialist_turns([issue])
    assert turns[0].agent_name == "INVENTORY"


def test_build_specialist_turns_uses_supplied_timestamp_and_never_fabricates_one():
    issue = _issue(id="issue-4")
    turns = build_specialist_turns([issue], timestamps={"issue-4": "2026-08-01T02:00:00+00:00"})
    assert turns[0].timestamp == "2026-08-01T02:00:00+00:00"

    turns_no_ts = build_specialist_turns([issue])
    assert turns_no_ts[0].timestamp == ""


# --- Sprint 5 Phase 5: Merchant Memory threaded onto turns ------------------


def test_build_specialist_turns_attaches_supplied_merchant_memory_note():
    issue = _issue(id="issue-5")
    turns = build_specialist_turns(
        [issue], merchant_memory_notes={"issue-5": "Based on 2 previous approvals..."}
    )
    assert turns[0].merchant_memory_note == "Based on 2 previous approvals..."


def test_build_specialist_turns_never_fabricates_a_merchant_memory_note():
    issue = _issue(id="issue-6")
    turns = build_specialist_turns([issue])
    assert turns[0].merchant_memory_note is None

    turns_with_unrelated_map = build_specialist_turns(
        [issue], merchant_memory_notes={"some-other-issue": "unrelated note"}
    )
    assert turns_with_unrelated_map[0].merchant_memory_note is None


def test_specialist_turn_to_dict_includes_merchant_memory_note():
    issue = _issue(id="issue-7")
    turns = build_specialist_turns([issue], merchant_memory_notes={"issue-7": "note text"})
    assert turns[0].to_dict()["merchant_memory_note"] == "note text"


# --- should_synthesize gate ---------------------------------------------------


def test_should_synthesize_false_with_zero_categories():
    assert should_synthesize([]) is False


def test_should_synthesize_true_with_exactly_one_category():
    # Sprint 5 Phase 1.3: a single specialist's findings are still worth an
    # LLM pass (a clean executive-memo sentence beats a template one).
    turns = build_specialist_turns([_issue(category="PRODUCT_QUALITY"), _issue(id="issue-2", category="PRODUCT_QUALITY")])
    assert should_synthesize(turns) is True


def test_should_synthesize_true_with_two_or_more_categories():
    turns = build_specialist_turns([_issue(category="CHECKOUT"), _issue(id="issue-2", category="PIXEL_TRACKING")])
    assert should_synthesize(turns) is True


# --- deterministic_briefing (no LLM call at all) ------------------------------


def test_deterministic_briefing_with_zero_turns_says_all_clear():
    briefing = deterministic_briefing([])
    assert "all clear" in briefing.narrative.lower()
    assert briefing.used_llm is False
    assert briefing.correlated is False


def test_deterministic_briefing_with_one_category_names_the_lone_specialist():
    turns = build_specialist_turns([_issue(category="PRODUCT_QUALITY")])
    briefing = deterministic_briefing(turns)
    assert "Product Quality Employee" in briefing.narrative
    assert briefing.used_llm is False


# --- ChiefOpsSynthesizer -----------------------------------------------------


async def test_synthesizer_skips_llm_entirely_with_zero_turns():
    # The only case that still skips the LLM outright (Sprint 5 Phase 1.3) —
    # a shift with no specialist findings at all.
    synthesizer = ChiefOpsSynthesizer(client=_ClientThatMustNotBeCalled())
    briefing = await synthesizer.synthesize([])
    assert briefing.used_llm is False
    assert "all clear" in briefing.narrative.lower()


async def test_synthesizer_calls_llm_for_a_single_specialist_category():
    # Sprint 5 Phase 1.3: previously this fell back to a deterministic
    # one-liner without ever calling the LLM. Now it gets a real LLM pass.
    turns = build_specialist_turns([_issue(category="PRODUCT_QUALITY")])
    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClientSuccess())
    briefing = await synthesizer.synthesize(turns)
    assert briefing.used_llm is True


async def test_synthesizer_success_path_uses_llm_narrative_and_correlated_flag():
    turns = build_specialist_turns([_issue(category="CHECKOUT"), _issue(id="issue-2", category="PIXEL_TRACKING")])
    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClientSuccess())
    briefing = await synthesizer.synthesize(turns)

    assert briefing.used_llm is True
    assert briefing.correlated is True
    assert "same incident" in briefing.narrative or "same time" in briefing.narrative
    assert len(briefing.turns) == 2


async def test_synthesizer_falls_back_on_schema_validation_failure():
    turns = build_specialist_turns([_issue(category="CHECKOUT"), _issue(id="issue-2", category="PIXEL_TRACKING")])
    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClientFailure())
    briefing = await synthesizer.synthesize(turns)

    assert briefing.used_llm is False
    assert briefing.correlated is False
    assert briefing.narrative  # never blank


# --- Sprint 5 Phase 5: Root Cause correlation framing -----------------------


def test_prompt_template_instructs_root_cause_prefix_only_when_correlated():
    # The prompt itself is the one place this rule is expressed to the LLM —
    # asserting on its literal text is the only way to pin this behavior
    # down, since the LLM's own output can't be unit-tested directly.
    assert "Root Cause: " in _PROMPT_TEMPLATE
    assert "correlated` is false, do not use the words" in _PROMPT_TEMPLATE


async def test_synthesizer_passes_through_a_root_cause_prefixed_narrative_verbatim():
    class _FakeLlmClientRootCause:
        model_name = "fake-model-root-cause"

        async def generate_structured(self, *, prompt: str, response_schema):
            assert response_schema is ChiefOpsSynthesisSchema
            return ChiefOpsSynthesisSchema(
                narrative=(
                    "Root Cause: A theme deployment removed the Meta Pixel app embed, "
                    "breaking checkout tracking."
                ),
                correlated=True,
            )

    turns = build_specialist_turns([_issue(category="CHECKOUT"), _issue(id="issue-2", category="PIXEL_TRACKING")])
    synthesizer = ChiefOpsSynthesizer(client=_FakeLlmClientRootCause())
    briefing = await synthesizer.synthesize(turns)

    assert briefing.correlated is True
    assert briefing.narrative.startswith("Root Cause: ")


async def test_synthesizer_falls_back_when_daily_budget_exceeded():
    turns = build_specialist_turns([_issue(category="CHECKOUT"), _issue(id="issue-2", category="PIXEL_TRACKING")])
    synthesizer = ChiefOpsSynthesizer(
        client=_ClientThatMustNotBeCalled(), budget_guard=_AlwaysExceededBudgetGuard()
    )
    briefing = await synthesizer.synthesize(turns)

    assert briefing.used_llm is False
    assert briefing.narrative
