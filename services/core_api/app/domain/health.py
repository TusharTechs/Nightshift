"""Store Health Score computation — Sprint 2 Feature 3 (Issue Prioritization
& Composite Store Health Engine).

This REPLACES Sprint 1's weighted-average-of-category-subscores model.
Sprint 2's own spec defines a different formula for the same computation
(deduction-from-100, keyed by the `issue_category` DB enum rather than
Sprint 1's ad hoc lowercase category strings) without acknowledging that
`domain/health.py` already existed with a conflicting implementation. This
was flagged and approved before implementation began — see CONFLICTS.md
item 10 / DECISIONS.md ADR-014: Sprint 2's "Composite Store Health Engine"
is the intended successor, not a coexisting variant, so the Sprint 1
formula, its category keys, and its dedicated unit tests are retired here
rather than kept alongside a second code path.

Pure domain logic — no framework imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import IssueCategory, IssueSeverity

# Category weights double as each category's maximum possible deduction
# ("CategoryMax"), per Sprint 2 Feature 3 (verbatim): "Checkout (25%), Pixel
# Tracking (20%), Product Quality (20%), SEO (15%), Discounts (10%),
# Performance (10%)."
CATEGORY_CAPS: dict[IssueCategory, int] = {
    IssueCategory.CHECKOUT: 25,
    IssueCategory.PIXEL_TRACKING: 20,
    IssueCategory.PRODUCT_QUALITY: 20,
    IssueCategory.SEO: 15,
    IssueCategory.DISCOUNT: 10,
    IssueCategory.PERFORMANCE: 10,
}

assert sum(CATEGORY_CAPS.values()) == 100, "Category caps must sum to 100 deduction points"

# Per-issue deduction points by severity — Sprint 2 AI Specification, verbatim.
SEVERITY_PENALTIES: dict[IssueSeverity, int] = {
    IssueSeverity.CRITICAL: 15,
    IssueSeverity.HIGH: 8,
    IssueSeverity.MEDIUM: 3,
    IssueSeverity.LOW: 1,
}

# `issue_category` has 8 values (matching the DB Architecture doc); only 6
# have an assigned category cap here — INVENTORY and TRUST_INDICATOR are not
# yet covered by any Sprint 2 agent. Sprint 2's own reference implementation
# silently drops issues in those two categories from the score with no
# warning at all (see `unscored_categories` below, which fixes that).


@dataclass(frozen=True)
class ScoredIssue:
    """Minimal shape the Store Health Engine needs from a persisted Issue row."""

    category: IssueCategory
    severity: IssueSeverity


@dataclass(frozen=True)
class HealthScoreResult:
    score: int
    category_deductions: dict[str, int] = field(default_factory=dict)
    unscored_categories: list[str] = field(default_factory=list)


class InvalidCategoryScoreError(Exception):
    """Kept as the domain's public exception type name for continuity with
    Sprint 1 callers/tests; raised for any malformed input to this module."""


def calculate_store_health(issues: list[ScoredIssue]) -> HealthScoreResult:
    """HealthScore = 100 - sum(Deductions_Category), each category's total
    deduction bounded at its own cap (Sprint 2 Feature 3 edge case,
    verbatim): "Multiple issues in a single category causing negative
    sub-scores; sub-scores bounded strictly at 0 <= SubScore <= CategoryMax."

    Issues in a category with no assigned cap (INVENTORY, TRUST_INDICATOR —
    not yet monitored by any Sprint 2 agent) are excluded from the numeric
    score but are surfaced via `unscored_categories` rather than silently
    dropped without a trace, unlike Sprint 2's own reference implementation
    (see CONFLICTS.md item 11). Callers (the shift compiler / worker layer)
    are responsible for logging this — this module stays a pure function
    with no logging side effects so it remains trivially unit-testable.
    """
    deductions: dict[IssueCategory, int] = {category: 0 for category in CATEGORY_CAPS}
    unscored: set[IssueCategory] = set()

    for issue in issues:
        if issue.category not in CATEGORY_CAPS:
            unscored.add(issue.category)
            continue
        deductions[issue.category] += SEVERITY_PENALTIES[issue.severity]

    total_deduction = 0
    bounded_deductions: dict[str, int] = {}
    for category, cap in CATEGORY_CAPS.items():
        bounded = max(0, min(cap, deductions[category]))
        bounded_deductions[category.value] = bounded
        total_deduction += bounded

    score = max(0, min(100, 100 - total_deduction))

    return HealthScoreResult(
        score=score,
        category_deductions=bounded_deductions,
        unscored_categories=sorted(category.value for category in unscored),
    )
