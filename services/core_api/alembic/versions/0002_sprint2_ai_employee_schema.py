"""Sprint 2 AI Employee schema: agents, issues, shift_reports.

Revision ID: 0002_sprint2_ai_employee_schema
Revises: 0001_sprint1_baseline_schema
Create Date: 2026-07-31

Reconciles Sprint 2's own migration draft against conflict resolutions
approved before implementation began (see CONFLICTS.md items 10-18 /
DECISIONS.md ADR-014 onward):

  * `issue_category` keeps all 8 values from the Database Architecture doc
    even though the Store Health Engine (`domain/health.py`) only assigns a
    deduction cap to 6 of them — INVENTORY/TRUST_INDICATOR issues can still
    be recorded, just excluded from the health score until a future
    sprint's agent covers them (CONFLICTS.md item 11).
  * Row-Level Security is enabled on `issues` and `shift_reports` — both
    hold per-tenant merchant data — matching ADR-006's precedent from
    Sprint 1. Sprint 2's own DDL draft omitted this despite the Database
    Architecture doc's and Technical Blueprint's unconditional
    RLS-on-every-merchant-data-table axiom (CONFLICTS.md item 18). `agents`
    is a shared, non-tenant registry of AI Employee configs (no `store_id`
    column at all) — RLS does not apply to it.
  * `agents.model_name` originally seeded to `gemini-2.5-pro`, not the
    Sprint 2 Spec's literal `gemini-1.5-pro` (retired as of 2026) —
    approved substitution, CONFLICTS.md item 12 / ADR-016.
"""

from __future__ import annotations

from alembic import op

revision = "0002_sprint2_ai_employee_schema"
down_revision = "0001_sprint1_baseline_schema"
branch_labels = None
depends_on = None

_PRODUCT_QUALITY_SYSTEM_PROMPT = (
    "You are the Product Quality Employee at NightShift AI, acting strictly "
    "as a senior e-commerce operations manager for the merchant's Shopify "
    "store. You are given a structured JSON diff of catalog inspection "
    "findings (missing images, missing ALT text, short descriptions, "
    "pricing/inventory anomalies). Identify operational issues, classify "
    "each by severity (CRITICAL, HIGH, MEDIUM, LOW), estimate realistic USD "
    "revenue impact grounded only in the catalog price data provided, and "
    "assign a confidence score (0.0-1.0). Never invent product GIDs or data "
    "not present in the provided context. Output strict JSON matching the "
    "required schema only."
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE issue_category AS ENUM "
        "('CHECKOUT','PIXEL_TRACKING','PRODUCT_QUALITY','SEO','DISCOUNT',"
        "'INVENTORY','PERFORMANCE','TRUST_INDICATOR')"
    )
    op.execute("CREATE TYPE issue_severity AS ENUM ('LOW','MEDIUM','HIGH','CRITICAL')")
    op.execute(
        "CREATE TYPE issue_status AS ENUM "
        "('OPEN','IN_PROGRESS','AWAITING_APPROVAL','RESOLVED','MUTED','FAILED')"
    )

    op.execute(
        """
        CREATE TABLE agents (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          identifier VARCHAR(100) UNIQUE NOT NULL,
          display_name VARCHAR(255) NOT NULL,
          description TEXT NOT NULL,
          domain_category issue_category NOT NULL,
          system_prompt_template TEXT NOT NULL,
          model_provider VARCHAR(50) DEFAULT 'GEMINI' NOT NULL,
          model_name VARCHAR(100) DEFAULT 'gemini-2.5-pro' NOT NULL,
          is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )

    op.execute(
        f"""
        INSERT INTO agents (identifier, display_name, description, domain_category,
                             system_prompt_template, model_provider, model_name)
        VALUES (
          'product-quality-agent',
          'Product Quality Employee',
          'LLM-powered agent that evaluates raw catalog inspection diffs, detects operational issues, estimates revenue impact, and assigns confidence scores.',
          'PRODUCT_QUALITY',
          $prompt${_PRODUCT_QUALITY_SYSTEM_PROMPT}$prompt$,
          'GEMINI',
          'gemini-2.5-pro'
        )
        """
    )

    op.execute(
        """
        CREATE TABLE issues (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          shift_id UUID REFERENCES shifts(id) ON DELETE SET NULL,
          agent_id UUID REFERENCES agents(id),
          category issue_category NOT NULL,
          severity issue_severity NOT NULL,
          status issue_status DEFAULT 'OPEN' NOT NULL,
          title VARCHAR(255) NOT NULL,
          description TEXT NOT NULL,
          evidence_data JSONB DEFAULT '{}'::jsonb NOT NULL,
          affected_resources TEXT[] DEFAULT '{}' NOT NULL,
          revenue_impact_estimate NUMERIC(10, 2) DEFAULT 0.00 NOT NULL,
          confidence_score FLOAT NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_issues_store_status_sev ON issues(store_id, status, severity, created_at DESC)")
    op.execute("CREATE INDEX idx_issues_evidence_gin ON issues USING gin (evidence_data)")

    op.execute(
        """
        CREATE TABLE shift_reports (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          shift_id UUID UNIQUE NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          executive_summary TEXT NOT NULL,
          report_json JSONB NOT NULL,
          published_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_shift_reports_store_published ON shift_reports(store_id, published_at DESC)")

    # --- Row-Level Security (approved addition; see module docstring) -------
    for table in ("issues", "shift_reports"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY store_isolation_policy ON {table}
              FOR ALL
              USING (store_id = current_setting('app.current_store_id', true)::uuid)
            """
        )
    # `agents` intentionally has no store_id column and no RLS policy — it is
    # a shared AI Employee registry, not per-tenant merchant data.


def downgrade() -> None:
    for table in ("shift_reports", "issues", "agents"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS issue_status")
    op.execute("DROP TYPE IF EXISTS issue_severity")
    op.execute("DROP TYPE IF EXISTS issue_category")
