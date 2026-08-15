"""Sprint 1 baseline schema: tenant, auth, store metadata, shifts, metrics,
and store memories.

Revision ID: 0001_sprint1_baseline_schema
Revises:
Create Date: 2026-07-31

This migration reconciles Sprint 1's own DDL draft with the Database
Architecture document and the conflict resolutions approved for Sprint 1
(see the engineering brief, Section 7, and CONFLICTS.md at the repo root):

  * metrics_hourly and store_memories are pulled forward from the Database
    Architecture doc — Sprint 1's own draft omitted them despite its
    Feature 3 / Story 2 / AI Spec sections requiring writes to both (7.1).
  * shifts.shift_number is a plain INT, not SERIAL — application code
    assigns the next per-store number so the UNIQUE(store_id, shift_number)
    constraint behaves as a per-store counter, not a globally-gapped one (7.5).
  * estimated_revenue_protected is the column name (matches Sprint 1's own
    migration and the DB Architecture doc; NOT estimated_revenue_saved) (7.5).
  * Row-Level Security is enabled on every table holding merchant data
    (stores, store_tokens, shifts, metrics_hourly, store_memories), per the
    Technical Blueprint's and DB Architecture doc's multi-tenancy axiom,
    which Sprint 1's own draft had omitted despite naming "tenant security
    boundaries" as a stated goal (7.7).
  * `issues` and `audit_logs` are intentionally NOT created here — no Sprint
    1 acceptance criterion exercises them; deferred pending confirmation
    with the Tech Lead (7.1, 7.12).
"""

from __future__ import annotations

from alembic import op

revision = "0001_sprint1_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    op.execute(
        "CREATE TYPE shift_status AS ENUM "
        "('SCHEDULED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'PARTIALLY_RESOLVED')"
    )

    op.execute(
        """
        CREATE TABLE organizations (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          name VARCHAR(255) NOT NULL,
          slug VARCHAR(255) UNIQUE NOT NULL,
          billing_email VARCHAR(255) NOT NULL,
          plan_tier VARCHAR(50) DEFAULT 'GROWTH' NOT NULL,
          is_active BOOLEAN DEFAULT TRUE NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          deleted_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_organizations_slug ON organizations(slug) WHERE deleted_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE stores (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          shopify_domain VARCHAR(255) UNIQUE NOT NULL,
          myshopify_domain VARCHAR(255) UNIQUE NOT NULL,
          store_name VARCHAR(255) NOT NULL,
          currency_code VARCHAR(10) DEFAULT 'USD' NOT NULL,
          iana_timezone VARCHAR(100) DEFAULT 'America/New_York' NOT NULL,
          autonomy_level INT DEFAULT 1 NOT NULL,
          preferred_shift_time TIME DEFAULT '02:00:00' NOT NULL,
          is_autonomous_execution_enabled BOOLEAN DEFAULT TRUE NOT NULL,
          health_score INT DEFAULT 100 NOT NULL CHECK (health_score BETWEEN 0 AND 100),
          is_active BOOLEAN DEFAULT TRUE NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          deleted_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_stores_org_domain ON stores(organization_id, shopify_domain) "
        "WHERE deleted_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE store_tokens (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          access_token_encrypted TEXT NOT NULL,
          scopes TEXT[] NOT NULL,
          token_type VARCHAR(50) DEFAULT 'offline' NOT NULL,
          expires_at TIMESTAMP WITH TIME ZONE,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          CONSTRAINT uq_store_tokens_store UNIQUE (store_id)
        )
        """
    )

    # shift_number: plain INT, not SERIAL (brief Section 7.5 — application
    # logic assigns the next per-store number; see ShiftRepository).
    op.execute(
        """
        CREATE TABLE shifts (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          shift_number INT NOT NULL,
          status shift_status DEFAULT 'SCHEDULED' NOT NULL,
          started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          completed_at TIMESTAMP WITH TIME ZONE,
          issues_detected_count INT DEFAULT 0 NOT NULL,
          issues_resolved_count INT DEFAULT 0 NOT NULL,
          pending_approvals_count INT DEFAULT 0 NOT NULL,
          estimated_revenue_protected NUMERIC(12, 2) DEFAULT 0.00 NOT NULL,
          estimated_time_saved_hours NUMERIC(5, 2) DEFAULT 0.00 NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          CONSTRAINT uq_store_shift_number UNIQUE (store_id, shift_number)
        )
        """
    )
    op.execute("CREATE INDEX idx_shifts_store_started ON shifts(store_id, started_at DESC)")

    # --- Pulled forward from the Database Architecture doc (brief 7.1) ------

    op.execute(
        """
        CREATE TABLE metrics_hourly (
          id UUID DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          health_score INT NOT NULL,
          open_issues_count INT NOT NULL DEFAULT 0,
          revenue_protected_delta NUMERIC(10, 2) DEFAULT 0.00 NOT NULL,
          recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          PRIMARY KEY (id, recorded_at)
        ) PARTITION BY RANGE (recorded_at)
        """
    )
    op.execute(
        """
        CREATE TABLE metrics_hourly_y2026 PARTITION OF metrics_hourly
          FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00')
        """
    )
    op.execute(
        "CREATE INDEX idx_metrics_hourly_tenant ON metrics_hourly(store_id, recorded_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE store_memories (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          memory_type VARCHAR(50) NOT NULL,
          content TEXT NOT NULL,
          embedding vector(768) NOT NULL,
          metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_store_memories_embedding ON store_memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # --- Row-Level Security (brief Section 7.7) -----------------------------
    # Every table holding merchant data gets RLS, matching the DB Architecture
    # doc's Core Design Axiom #1 and the Technical Blueprint's stated pattern.
    # `stores` is scoped on its own `id` (it *is* the tenant boundary); the
    # rest are scoped on `store_id`.

    op.execute("ALTER TABLE stores ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY store_isolation_policy ON stores
          FOR ALL
          USING (id = current_setting('app.current_store_id', true)::uuid)
        """
    )

    for table in ("store_tokens", "shifts", "metrics_hourly", "store_memories"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY store_isolation_policy ON {table}
              FOR ALL
              USING (store_id = current_setting('app.current_store_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in ("store_memories", "metrics_hourly", "shifts", "store_tokens", "organizations", "stores"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS shift_status")
