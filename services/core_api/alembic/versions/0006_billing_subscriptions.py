"""Billing: subscriptions table — the Shopify Billing API-backed NightShift
Free/Pro/Business plan record, added for the hackathon's "Business
Viability" judging criterion.

Revision ID: 0006_billing_subs
Revises: 0005_step3_theme_track
Create Date: 2026-08-09

--- Why this table exists ----------------------------------------------------

Every other table in this schema models "what NightShift observed/did to a
merchant's store." This one models "what NightShift is being paid for it" —
a genuinely new bounded context (billing), absent from the Database
Architecture doc entirely (that doc predates any monetization requirement).
Added as an additive, net-new table — same precedent as Sprint 3's
cognitive_tasks/approvals/executions/verifications/rollbacks and Sprint 4
Step 3's theme_snapshots/tracking_snapshots (see those migrations' own
docstrings).

One row is created automatically for every store at install time
(plan=FREE, status=ACTIVE, monthly_price_usd=0 — see
`CompleteOAuthInstallation`), so `store_id` always has at least one row and
"no subscription row" never has to be treated as an implicit Free tier
anywhere else in the codebase.

This is deliberately an APPEND-STYLE HISTORY table (same convention as
`cognitive_tasks`/`approvals`), not a single mutable one-row-per-store
record: `POST /api/v1/billing/subscribe` inserts a NEW row per subscribe
attempt (status=PENDING, one Shopify AppSubscription GID each) rather than
mutating the store's existing row in place. This preserves a genuine audit
trail of every subscribe attempt — including ones the merchant later
declined — and lets `SubscriptionRepository.get_current_for_store` simply
mean "this store's most-recently-created row," with no separate
`is_current` flag or second history table needed.
`GET /api/v1/billing/confirm` (the Shopify `returnUrl` redirect target)
updates that same PENDING row's `status` in place once the real Shopify-side
outcome has been re-queried — it never trusts the redirect's own `charge_id`
query param alone (same "always re-query Shopify, never trust the response/
redirect alone" axiom as Sprint 3's Verification Engine, see
`verify_execution.py`'s own module docstring).

`plan_tier` mirrors the three pricing tiers the product owner has already
decided (FREE / PRO / BUSINESS). BUSINESS exists in the enum and is billed
at its own price, but has NO enforced behavioral difference from PRO built
anywhere in this codebase yet — explicitly out of scope per the hackathon
brief ("a Business row with no enforced difference from Pro yet is fine,
clearly commented as not yet differentiated"; see `domain/billing_plans.py`
and `domain/enums.py::PlanTier`).

`subscription_status` mirrors Shopify's own `AppSubscriptionStatus` GraphQL
enum values verbatim (confirmed via shopify.dev, 2026-08-09) for the subset
this app's own lifecycle can actually produce: PENDING, ACTIVE, CANCELLED,
DECLINED, EXPIRED. Deliberately excludes Shopify's own `FROZEN` (a
non-payment hold — no webhook-driven billing sync exists in this build to
ever observe/set it) and `ACCEPTED` (Shopify's own docs mark it deprecated).

`shopify_charge_gid` is nullable because the FREE-tier row auto-created at
install time is never backed by a real Shopify `AppSubscription` at all —
Free costs the merchant nothing and requires zero Shopify Billing API calls
or merchant payment interaction, per the product's own requirement that the
judge/test account must remain free and fully usable with zero payment
interaction.

Per-tenant (`store_id` present): gets the same Row-Level Security policy as
every other merchant-data table (ADR-006 precedent, Sprint 1).

NOTE: this migration's own upgrade()/downgrade() have NOT been run against a
live Postgres instance in this sandbox (no local Postgres available) — only
validated via `alembic upgrade head --sql` style review of the generated
DDL and by-hand comparison against 0002/0003/0005's own CREATE TYPE / CREATE
TABLE / RLS-policy patterns. Flagged here exactly like Sprint 4 Step 3
flagged its own known scope limitations — not silently assumed correct.
"""

from __future__ import annotations

from alembic import op

revision = "0006_billing_subs"
down_revision = "0005_step3_theme_track"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE plan_tier AS ENUM ('FREE','PRO','BUSINESS')")
    op.execute(
        "CREATE TYPE subscription_status AS ENUM "
        "('PENDING','ACTIVE','CANCELLED','DECLINED','EXPIRED')"
    )

    op.execute(
        """
        CREATE TABLE subscriptions (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          plan plan_tier NOT NULL DEFAULT 'FREE',
          status subscription_status NOT NULL DEFAULT 'PENDING',
          shopify_charge_gid VARCHAR(255),
          monthly_price_usd NUMERIC(6,2) NOT NULL DEFAULT 0,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          activated_at TIMESTAMP WITH TIME ZONE,
          cancelled_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute("CREATE INDEX idx_subscriptions_store ON subscriptions(store_id)")
    # Speeds up `get_current_for_store` (effectively ORDER BY created_at DESC
    # LIMIT 1 per store_id) — the same lookup the nightly-dispatch gate
    # (`SelectNightlyDispatchStores`) also performs once per active store.
    op.execute(
        "CREATE INDEX idx_subscriptions_store_created ON subscriptions(store_id, created_at DESC)"
    )

    op.execute("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY store_isolation_policy ON subscriptions
          FOR ALL
          USING (store_id = current_setting('app.current_store_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS subscriptions CASCADE")
    op.execute("DROP TYPE IF EXISTS subscription_status")
    op.execute("DROP TYPE IF EXISTS plan_tier")
