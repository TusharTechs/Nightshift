"""Sprint 4 Step 3: theme_snapshots + tracking_snapshots tables, plus the
Theme Guardian / Tracking Specialist agent registry rows.

Revision ID: 0005_step3_theme_track
Revises: 0004_sprint4_checkout_agent
Create Date: 2026-08-02

NOTE: the revision id below is `0005_step3_theme_track`, deliberately short
— same VARCHAR(32) `alembic_version.version_num` constraint that forced a
shortened id in both 0003 and 0004; picked short from the start this time
rather than writing the full name and hitting the same truncation error
again. The filename is unchanged/full since nothing depends on the two
matching (see 0004's own note).

--- Context: why these two tables exist -------------------------------------

Both new specialists this step (Theme Guardian, Tracking Specialist) detect
"drift from a known-good baseline," not a structural fact computable from a
single live API read the way Checkout Specialist's duplicate-discount check
is (Step 2). A snapshot of "what the file/script tag looked like last time
we confirmed it was fine" has to be persisted somewhere to diff against —
neither table exists in the Database Architecture doc (theme/script-tag
monitoring wasn't envisioned there at all), so this is a net-new, additive
schema addition, following the exact precedent set by Sprint 3's own
CONFLICTS-logged additions (cognitive_tasks/approvals/executions/
verifications/rollbacks) and this sprint's own Step 1 (demo incident scenario
registry needed no new table, but the principle — "add a table only when the
brief's own tables genuinely cannot support a real requirement" — carries
forward identically here).

`theme_snapshots.content` stores the full baseline file content (not just a
checksum) because three different consumers need it: the diff engine (to
compute a human-readable line-level diff), `ThemeGuardianAgent`'s LLM prompt
(to explain what changed in plain English), and the guided-restore
execution_plan (the "exact Liquid patch" handed to the merchant via the
Theme Editor deep link — see `domain/agents/theme_guardian.py`). Storing
only a checksum would make the restore/explain steps impossible without a
second live fetch of a version that may no longer be retrievable from
Shopify at all (there is no theme file version history API).

`tracking_snapshots` stores one row per known/expected script tag `src`
(not a hash of anything — there's no "content" to diff, just presence).
`pattern_name` is a nullable, best-effort label (e.g. 'meta_pixel') from a
small known-pattern registry in `domain/script_tag_inspection.py`, purely for
human-readable findings/audit-log text — detection itself never depends on
recognizing the pattern, only on "a script tag we'd previously observed at
this store is no longer present."

Both tables are per-tenant (`store_id` present) and get the same Row-Level
Security policy as every other merchant-data table (ADR-006 precedent,
Sprint 1).

Known, documented scope limitation (not an oversight): the baseline snapshot
is captured once, on first-ever observation of a given file/script tag, and
is never automatically re-baselined afterward — including after a
successful guided restore (the restored content is expected to already
match the original baseline exactly, so no re-write is needed there either).
A merchant who *intentionally* edits a watched theme file later would have
that legitimate change flagged as "drift" on the next scan, with no
built-in "accept this as the new baseline" action yet. Out of scope for this
hackathon step — flagged here exactly like the ALT-text/description
rollback gaps were flagged in Sprint 3 (ADR-030), not silently built around.
"""

from __future__ import annotations

from alembic import op

revision = "0005_step3_theme_track"
down_revision = "0004_sprint4_checkout_agent"
branch_labels = None
depends_on = None

_THEME_GUARDIAN_PROMPT = (
    "You are the Theme Guardian at NightShift AI. You are given the baseline "
    "(last known-good) content and the current live content of one Shopify "
    "theme file, plus which lines differ. Explain, in plain English a "
    "non-technical merchant can act on immediately, what changed and why it "
    "matters for their storefront (e.g. a removed Buy Button, a broken "
    "include, a deleted section). Ground your explanation ONLY in the diff "
    "provided — never invent a cause not visible in the actual line changes. "
    "Classify severity as exactly one of: CRITICAL, HIGH, MEDIUM, LOW."
)

_TRACKING_SPECIALIST_NO_PROMPT_NOTE = (
    "This agent never calls an LLM. A previously-observed tracking script "
    "tag (e.g. a Meta Pixel) going missing from the live storefront is a "
    "deterministic, structural fact — see app/domain/script_tag_inspection.py "
    "— not a judgment call. There is no prompt to run."
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE theme_snapshots (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          theme_id VARCHAR(255) NOT NULL,
          filename VARCHAR(500) NOT NULL,
          content TEXT NOT NULL,
          checksum_md5 VARCHAR(64) NOT NULL,
          captured_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          UNIQUE(store_id, theme_id, filename)
        )
        """
    )
    op.execute("CREATE INDEX idx_theme_snapshots_store ON theme_snapshots(store_id)")

    op.execute(
        """
        CREATE TABLE tracking_snapshots (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          src VARCHAR(1000) NOT NULL,
          display_scope VARCHAR(50),
          pattern_name VARCHAR(100),
          captured_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          UNIQUE(store_id, src)
        )
        """
    )
    op.execute("CREATE INDEX idx_tracking_snapshots_store ON tracking_snapshots(store_id)")

    for table in ("theme_snapshots", "tracking_snapshots"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY store_isolation_policy ON {table}
              FOR ALL
              USING (store_id = current_setting('app.current_store_id', true)::uuid)
            """
        )

    # --- Agent registry rows -------------------------------------------------
    # `domain_category` values reuse the existing 8-value `issue_category`
    # enum (no ALTER TYPE needed, same payoff as Step 2's `DISCOUNT` choice —
    # CONFLICTS.md item 32/ADR-035):
    #   - Theme Guardian -> 'CHECKOUT'. Deliberately reused, not invented:
    #     `checkout_specialist.py`'s own docstring explicitly reserved
    #     'CHECKOUT' for "a genuinely broader future specialist" beyond
    #     discount pricing — a removed Buy Button block is exactly that: a
    #     storefront issue that breaks the customer's ability to check out,
    #     even though no discount/pricing object is involved.
    #   - Tracking Specialist -> 'PIXEL_TRACKING'. Exact semantic fit, no
    #     reservation needed — this value has existed since Sprint 2's own
    #     migration and was simply unused until now.
    op.execute(
        f"""
        INSERT INTO agents (identifier, display_name, description, domain_category,
                             system_prompt_template, model_provider, model_name)
        VALUES (
          'theme-guardian-agent',
          'Theme Guardian',
          'Snapshots critical live theme files, detects when their content '
          'diverges from the last known-good baseline, uses an LLM to '
          'explain the change in plain English, and proposes an '
          'approval-gated guided restore (exact patch + Theme Editor '
          'deep link) rather than an unsupervised write.',
          'CHECKOUT',
          $prompt${_THEME_GUARDIAN_PROMPT}$prompt$,
          'GEMINI',
          'gemini-2.5-pro'
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO agents (identifier, display_name, description, domain_category,
                             system_prompt_template, model_provider, model_name)
        VALUES (
          'tracking-specialist-agent',
          'Tracking Specialist',
          'Detects when a previously-observed tracking script tag (e.g. a '
          'Meta Pixel) has been removed from the live storefront, and '
          'recreates it from its own snapshot (approval-gated).',
          'PIXEL_TRACKING',
          $prompt${_TRACKING_SPECIALIST_NO_PROMPT_NOTE}$prompt$,
          'NONE',
          'deterministic-rule-engine'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM agents WHERE identifier IN ('theme-guardian-agent', 'tracking-specialist-agent')")
    op.execute("DROP TABLE IF EXISTS tracking_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS theme_snapshots CASCADE")
