"""Sprint 2 / Sprint 3 domain enums.

Mirror the Postgres enum types created by
`alembic/versions/0002_sprint2_ai_employee_schema.py` and
`alembic/versions/0003_sprint3_trust_execution_schema.py` verbatim so Python
and the database never drift. Values match the Database Architecture
document's `issue_category` (8 values), `issue_severity` (4 values), and
`issue_status` (6 values) exactly, plus Sprint 3's `risk_level`,
`task_status`, `approval_status`, `execution_status`, and
`verification_status`.
"""

from __future__ import annotations

from enum import Enum


class IssueCategory(str, Enum):
    CHECKOUT = "CHECKOUT"
    PIXEL_TRACKING = "PIXEL_TRACKING"
    PRODUCT_QUALITY = "PRODUCT_QUALITY"
    SEO = "SEO"
    DISCOUNT = "DISCOUNT"
    INVENTORY = "INVENTORY"
    PERFORMANCE = "PERFORMANCE"
    TRUST_INDICATOR = "TRUST_INDICATOR"


class IssueSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    MUTED = "MUTED"
    FAILED = "FAILED"


class ShiftStatus(str, Enum):
    """Mirrors the `shift_status` Postgres enum from
    `0001_sprint1_baseline_schema.py` verbatim."""

    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"


# --- Sprint 3: AI Trust & Execution -----------------------------------------
#
# Mirror the Postgres enum types created by
# `alembic/versions/0003_sprint3_trust_execution_schema.py` verbatim so
# Python and the database never drift.


class RiskLevel(str, Enum):
    LEVEL_1_SAFE = "LEVEL_1_SAFE"
    LEVEL_2_MODERATE = "LEVEL_2_MODERATE"
    LEVEL_3_HIGH = "LEVEL_3_HIGH"
    LEVEL_4_CRITICAL = "LEVEL_4_CRITICAL"


class TaskStatus(str, Enum):
    PLANNED = "PLANNED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    BYPASSED = "BYPASSED"


class ExecutionStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class VerificationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ApprovalAction(str, Enum):
    """Merchant-submitted decision on POST /api/v1/approvals/{id}/action —
    per the API Contract Specification's ApprovalActionRequest.action enum.
    'Modify' (per the PRD's Approval Center UI) is NOT a separate enum value:
    it is APPROVE with execution_override_params populated — approved
    resolution this session, no new schema needed."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    DEFER = "DEFER"


class ActionType(str, Enum):
    """Concrete auto-fixable action types this sprint. Extensible — new
    action types are added here as new agents/fix-paths are built in later
    sprints."""

    GENERATE_ALT_TEXT = "GENERATE_ALT_TEXT"
    REWRITE_PRODUCT_DESCRIPTION = "REWRITE_PRODUCT_DESCRIPTION"
    # Sprint 4 Step 2: Checkout Specialist — Duplicate Discount lifecycle.
    DEACTIVATE_DUPLICATE_DISCOUNT = "DEACTIVATE_DUPLICATE_DISCOUNT"
    # Sprint 4 Step 3: Theme Guardian — never writes Shopify directly (see
    # CONFLICTS.md's themeFilesUpsert-exemption entry); this action type's
    # "execution" only generates a guided restore bundle (patch + Theme
    # Editor deep link) for the merchant to apply themselves.
    GENERATE_THEME_RESTORE_GUIDE = "GENERATE_THEME_RESTORE_GUIDE"
    # Sprint 4 Step 3: Tracking Specialist — recreates a removed script tag
    # from its own snapshot.
    RECREATE_TRACKING_SCRIPT_TAG = "RECREATE_TRACKING_SCRIPT_TAG"


# --- Billing: NightShift Free / Pro / Business monetization -----------------
#
# Mirror the Postgres enum types created by
# `alembic/versions/0006_billing_subscriptions.py` verbatim so Python and the
# database never drift, same convention as every enum above.


class PlanTier(str, Enum):
    """The three pricing tiers already decided by the product owner (not a
    product decision made here). BUSINESS is a stretch tier: the enum value
    exists and is billed at its own price, but it has NO enforced behavioral
    difference from PRO anywhere in this codebase yet — e.g.
    `workers/tasks/scheduler.py`'s nightly-dispatch gate treats PRO and
    BUSINESS identically. Do not add BUSINESS-specific differentiators
    without a corresponding product decision — see
    `domain/billing_plans.py`'s own comment."""

    FREE = "FREE"
    PRO = "PRO"
    BUSINESS = "BUSINESS"


class SubscriptionStatus(str, Enum):
    """Mirrors Shopify's own `AppSubscriptionStatus` GraphQL enum values
    verbatim (confirmed via shopify.dev, 2026-08-09) for the subset this
    app's own billing lifecycle can actually produce/observe — never an
    invented parallel taxonomy that doesn't map 1:1 onto what Shopify itself
    reports back for a real `AppSubscription`.

    Deliberately excludes two of Shopify's own values: `FROZEN` (an
    on-hold-for-non-payment state — this app has no webhook-driven billing
    sync yet that could ever observe or transition into it) and `ACCEPTED`
    (Shopify's own docs mark it deprecated). Both are out of scope, not
    oversights — add them only alongside the billing webhook handling that
    would actually keep them in sync with Shopify's real state.

    A FREE-tier subscription row (auto-created at install time, never backed
    by a real Shopify `AppSubscription`) also uses ACTIVE — there is no
    separate "N/A"/free-forever status; a $0 ACTIVE row is exactly how
    "every store always has exactly one current subscription row" is
    modeled (see the Sprint 6 migration's own docstring)."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
