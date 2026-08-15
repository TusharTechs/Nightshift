"""Billing: backfill a FREE/ACTIVE subscription row for every store that
predates the subscriptions table.

Revision ID: 0007_backfill_free_subs
Revises: 0006_billing_subs
Create Date: 2026-08-09

--- Why this migration exists ------------------------------------------------

`0006_billing_subscriptions.py` only creates the `subscriptions` table — it
never backfills a row for stores that already existed before it ran.
`CompleteOAuthInstallation` only provisions a FREE row for installs that
happen AFTER Billing shipped, so any store installed earlier ends up with
ZERO subscription rows.

Confirmed live, not hypothetical: this exact gap was reproduced against a
real running store — `SelectNightlyDispatchStores.execute()`
(`app/application/use_cases/select_nightly_dispatch_stores.py`) treats a
missing subscription row the same as Free (`if subscription is None:
continue`), so a pre-existing store would be silently excluded from
automatic nightly dispatch forever, and `GET /api/v1/billing/status` would
404 for it indefinitely — never a crash, but never fixed either, since
nothing re-provisions the row after install time.

This migration is idempotent and additive: it inserts exactly one FREE/
ACTIVE row for every `store_id` present in `stores` that has no row at all
in `subscriptions` yet (`WHERE NOT EXISTS (...)`), and touches nothing for
a store that already has one — including a store an operator has already
manually upgraded (e.g. via a direct DB fix while Billing was still being
stabilized). Safe to run multiple times.
"""

from __future__ import annotations

from alembic import op

revision = "0007_backfill_free_subs"
down_revision = "0006_billing_subs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO subscriptions (store_id, plan, status, monthly_price_usd, activated_at)
        SELECT s.id, 'FREE', 'ACTIVE', 0, CURRENT_TIMESTAMP
        FROM stores s
        WHERE NOT EXISTS (
            SELECT 1 FROM subscriptions sub WHERE sub.store_id = s.id
        )
        """
    )


def downgrade() -> None:
    # Deliberately a no-op: there is no reliable way to distinguish "a row
    # this migration backfilled" from "a row CompleteOAuthInstallation
    # created normally at install time, which happens to also be FREE" after
    # the fact — downgrading would risk deleting a genuine install-time row.
    # 0006's own downgrade() already drops the whole table if that's ever
    # actually needed.
    pass
