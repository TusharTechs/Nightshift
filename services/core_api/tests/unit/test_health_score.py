"""Unit tests: Store Health Score computation (Sprint 2 Feature 3 — Issue
Prioritization & Composite Store Health Engine).

Replaces Sprint 1's weighted-average-of-category-subscores tests. See
CONFLICTS.md item 10 / DECISIONS.md ADR-014 for why the algorithm changed
and why the old tests were retired rather than kept alongside these.
Required test name per Sprint 2's own Testing section:
test_health_score_calculation (covered by the suite below).
"""

from __future__ import annotations

from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.health import (
    CATEGORY_CAPS,
    SEVERITY_PENALTIES,
    ScoredIssue,
    calculate_store_health,
)


def test_health_score_calculation_no_issues_is_100():
    result = calculate_store_health([])
    assert result.score == 100
    assert all(deduction == 0 for deduction in result.category_deductions.values())
    assert result.unscored_categories == []


def test_health_score_calculation_single_critical_checkout_issue():
    result = calculate_store_health(
        [ScoredIssue(category=IssueCategory.CHECKOUT, severity=IssueSeverity.CRITICAL)]
    )
    # 100 - 15 (CRITICAL penalty) = 85
    assert result.score == 85
    assert result.category_deductions["CHECKOUT"] == 15


def test_health_score_calculation_deductions_bounded_at_category_cap():
    # Checkout cap is 25; 3 CRITICAL issues would be 45 points of deduction
    # uncapped — must bound at 25 (Sprint 2 Feature 3 edge case, verbatim).
    issues = [ScoredIssue(category=IssueCategory.CHECKOUT, severity=IssueSeverity.CRITICAL)] * 3
    result = calculate_store_health(issues)
    assert result.category_deductions["CHECKOUT"] == CATEGORY_CAPS[IssueCategory.CHECKOUT]
    assert result.score == 100 - CATEGORY_CAPS[IssueCategory.CHECKOUT]


def test_health_score_calculation_multiple_categories_sum_correctly():
    issues = [
        ScoredIssue(category=IssueCategory.PRODUCT_QUALITY, severity=IssueSeverity.HIGH),  # -8
        ScoredIssue(category=IssueCategory.SEO, severity=IssueSeverity.MEDIUM),  # -3
        ScoredIssue(category=IssueCategory.DISCOUNT, severity=IssueSeverity.LOW),  # -1
    ]
    result = calculate_store_health(issues)
    assert result.score == 100 - 8 - 3 - 1


def test_health_score_calculation_never_goes_below_zero():
    issues = [ScoredIssue(category=category, severity=IssueSeverity.CRITICAL) for category in CATEGORY_CAPS] * 5
    result = calculate_store_health(issues)
    assert result.score == 0


def test_health_score_calculation_unscored_categories_are_reported_not_dropped():
    # INVENTORY has no assigned category cap (only 6 of the 8 issue_category
    # enum values are scored) — must be surfaced, not silently discarded.
    issues = [ScoredIssue(category=IssueCategory.INVENTORY, severity=IssueSeverity.HIGH)]
    result = calculate_store_health(issues)
    assert result.score == 100  # not scored, so no deduction applied
    assert result.unscored_categories == ["INVENTORY"]


def test_category_caps_sum_to_100():
    assert sum(CATEGORY_CAPS.values()) == 100


def test_severity_penalties_cover_all_severities():
    assert set(SEVERITY_PENALTIES.keys()) == set(IssueSeverity)
