"""AI Explanation Engine — Sprint 3.

Deterministic, template-based (no LLM call — avoids extra cost/latency and
non-determinism; every field is populated directly from structured data
already computed by the pipeline, per the "never parse free-form text when
structured output is possible" principle). Produces both a structured
bundle (for the API/UI) and a single human-readable narrative paragraph
matching the Sprint 3 brief's example style ("I found two overlapping
discounts... I disabled the older campaign. Verification confirmed.
Estimated revenue protected: $312").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExplanationBundle:
    what_happened: str
    why: str
    business_impact: str
    what_changed: str
    how_verified: str

    def to_dict(self) -> dict:
        return {
            "what_happened": self.what_happened,
            "why": self.why,
            "business_impact": self.business_impact,
            "what_changed": self.what_changed,
            "how_verified": self.how_verified,
            "narrative": self.narrative(),
        }

    def narrative(self) -> str:
        return " ".join(
            [self.what_happened, self.why, self.what_changed, self.how_verified, self.business_impact]
        )


def build_explanation(
    *,
    issue_title: str,
    issue_description: str,
    action_type: str,
    revenue_impact_estimate: float,
    verification_passed: bool,
    verification_method: str,
    before_value: str | None,
    after_value: str | None,
) -> ExplanationBundle:
    what_happened = f"I found: {issue_title}."
    why = issue_description
    if action_type == "GENERATE_ALT_TEXT":
        what_changed = (
            f"I generated descriptive ALT text (from \"{before_value or 'none'}\" to \"{after_value}\")."
        )
    elif action_type == "REWRITE_PRODUCT_DESCRIPTION":
        what_changed = "I rewrote the product description to meet the minimum content length for SEO and conversion."
    else:
        what_changed = f"I applied the {action_type} fix."

    how_verified = (
        f"Verification confirmed via {verification_method}: the live Shopify data now matches the intended change."
        if verification_passed
        else f"Verification via {verification_method} FAILED: the change was rolled back and no lasting edit was made."
    )
    business_impact = (
        f"Estimated revenue protected: ${revenue_impact_estimate:,.2f}."
        if verification_passed
        else "No revenue impact — the action was reverted."
    )
    return ExplanationBundle(
        what_happened=what_happened,
        why=why,
        business_impact=business_impact,
        what_changed=what_changed,
        how_verified=how_verified,
    )
