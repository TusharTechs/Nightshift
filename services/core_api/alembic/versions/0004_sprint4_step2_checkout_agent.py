"""Sprint 4 Step 2: seed the Checkout Specialist agent registry row.

Revision ID: 0004_sprint4_checkout_agent
Revises: 0003_sprint3_trust_exec_schema
Create Date: 2026-08-02

NOTE: the revision id below is `0004_sprint4_checkout_agent`, not
`..._step2_checkout_agent` matching this file's own name — Alembic's default
`alembic_version.version_num` column is VARCHAR(32), and the full `_step2_`
spelling is 1 character over that limit (raises
`StringDataRightTruncationError` on upgrade — the exact same class of bug
already hit and fixed once this sprint, see 0003's own revision id note).
Shortened only the revision id string; the filename is unchanged since
nothing depends on the two matching.

Purely additive: one INSERT into the existing `agents` table (created by
0002_sprint2_ai_employee_schema.py), no schema changes. Mirrors that
migration's own seeding pattern for `product-quality-agent` exactly.

`domain_category = 'DISCOUNT'` — the Postgres `issue_category` enum already
has this value (created in 0002, unchanged since), so no enum migration is
needed — this is the concrete payoff of the Sprint 4 Step 1 agent-registry
design decision (CONFLICTS.md item 32 / ADR-035): every new specialist slots
in via a plain data row, never a schema change.

`system_prompt_template` is NOT NULL on `agents` (schema-level constraint
from Sprint 2), but this agent never calls an LLM — its detection step is a
deterministic rule (see `domain/discount_inspection.py`). The column is
filled with a short, honest note to that effect rather than a real prompt,
and `model_provider`/`model_name` are set to non-LLM sentinel values
('NONE' / 'deterministic-rule-engine') so nothing downstream could mistake
this row for an LLM-backed agent.
"""

from __future__ import annotations

from alembic import op

revision = "0004_sprint4_checkout_agent"
down_revision = "0003_sprint3_trust_exec_schema"
branch_labels = None
depends_on = None

_NO_PROMPT_NOTE = (
    "This agent never calls an LLM. Duplicate/overlapping stackable discount "
    "detection is a deterministic structural rule over live Shopify discount "
    "data (see app/domain/discount_inspection.py) — there is no prompt to run."
)


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO agents (identifier, display_name, description, domain_category,
                             system_prompt_template, model_provider, model_name)
        VALUES (
          'checkout-specialist-agent',
          'Checkout Specialist',
          'Detects duplicate/overlapping storewide discount codes that can '
          'stack unintentionally at checkout, and deactivates the redundant '
          'ones (approval-gated) once a merchant confirms.',
          'DISCOUNT',
          $prompt${_NO_PROMPT_NOTE}$prompt$,
          'NONE',
          'deterministic-rule-engine'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM agents WHERE identifier = 'checkout-specialist-agent'")
