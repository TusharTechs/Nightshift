"""Sprint 3 AI Trust & Execution schema: cognitive_tasks, approvals,
executions, verifications, rollbacks, audit_logs.

Revision ID: 0003_sprint3_trust_exec_schema
Revises: 0002_sprint2_ai_employee_schema

NOTE: the revision id below is `0003_sprint3_trust_exec_schema`, not
`..._trust_execution_schema` matching this file's own name — Alembic's
default `alembic_version.version_num` column is VARCHAR(32), and the full
`_execution_` spelling is 3 characters over that limit (raises
`StringDataRightTruncationError` on upgrade). Shortened only the revision id
string; the filename is unchanged since nothing depends on the two matching.
Create Date: 2026-08-01

This migration implements the Observe -> Reason -> Assess Risk -> Request
Approval -> Execute -> Verify -> Explain -> Persist -> Learn lifecycle's
persistence layer for the two Sprint 3 auto-fixable Product Quality action
types (GENERATE_ALT_TEXT, REWRITE_PRODUCT_DESCRIPTION).

Additive, user-approved deviations from the Database Architecture doc
(2026-08-01 session — see CONFLICTS.md "Sprint 3 Conflict Log" for the full
write-up):

  * `executions.retry_count` — the base doc's `executions` table has no
    retry counter at all, but the master engineering brief's retry
    requirement (bounded re-attempts of a failed mutation) has nowhere else
    to persist a per-execution attempt count.
  * `approvals.execution_override_params` — the base doc's `approvals` table
    has no column for merchant-supplied parameter overrides, but the PRD's
    Approval Center "Modify" action (approve a proposed fix with edited
    parameters, e.g. a hand-edited ALT text string) has nothing to persist
    those edits to otherwise. Modeled as APPROVE + populated
    `execution_override_params`, not a new `MODIFY` approval_status value
    (see `domain/enums.py::ApprovalAction`'s docstring for the full
    reasoning).
  * `audit_logs.execution_id` — the base doc's `audit_logs` table has no FK
    back to the specific execution attempt an audit entry narrates, making
    it impossible to correlate an audit trail entry with the exact mutation
    call (and its request/response payloads) that produced it.
  * The entire `rollbacks` table — absent from the base doc entirely, but
    the master engineering brief's mandatory rollback capability (every
    Level-1/Level-2 autonomous action must be deterministically reversible)
    has no table to record a rollback attempt's outcome, reverted state, or
    reason without one. Reuses the existing `execution_status` enum type for
    its own `status` column (STARTED/COMPLETED/FAILED apply naturally to a
    rollback attempt too) rather than defining a redundant sixth enum type.

  All four of the above were explicitly approved by the user this session
  (2026-08-01) as necessary to satisfy requirements the base schema doc
  itself could not otherwise support — not scope creep, additive-only.

  `approvals.approver_user_id` is intentionally a bare nullable UUID with NO
  foreign key constraint: no `users` table exists anywhere in this codebase
  (Sprint 1/2 never built per-user auth — `get_current_store_id` resolves
  only to a store, never a user). It's reserved for when user accounts land;
  until then, merchant-initiated actions are attributed via
  `audit_logs.actor_type = 'MERCHANT'` / `audit_logs.actor_id` instead.

  `uuid-ossp` / `pgcrypto` extensions already exist (created in
  0001_sprint1_baseline_schema.py) — not recreated here.
"""

from __future__ import annotations

from alembic import op

revision = "0003_sprint3_trust_exec_schema"
down_revision = "0002_sprint2_ai_employee_schema"
branch_labels = None
depends_on = None

_NEW_TABLES_CHILD_TO_PARENT = (
    "rollbacks",
    "audit_logs",
    "verifications",
    "executions",
    "approvals",
    "cognitive_tasks",
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE risk_level AS ENUM "
        "('LEVEL_1_SAFE','LEVEL_2_MODERATE','LEVEL_3_HIGH','LEVEL_4_CRITICAL')"
    )
    op.execute(
        "CREATE TYPE task_status AS ENUM "
        "('PLANNED','PENDING_APPROVAL','EXECUTING','VERIFYING','SUCCESS','FAILED','ROLLED_BACK')"
    )
    op.execute(
        "CREATE TYPE approval_status AS ENUM "
        "('PENDING','APPROVED','REJECTED','EXPIRED','BYPASSED')"
    )
    op.execute(
        "CREATE TYPE execution_status AS ENUM ('STARTED','COMPLETED','FAILED','ROLLED_BACK')"
    )
    op.execute(
        "CREATE TYPE verification_status AS ENUM ('PASSED','FAILED','SKIPPED')"
    )

    op.execute(
        """
        CREATE TABLE cognitive_tasks (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          issue_id UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          shift_id UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
          agent_id UUID NOT NULL REFERENCES agents(id),
          action_type VARCHAR(100) NOT NULL,
          execution_plan JSONB NOT NULL,
          risk_level risk_level NOT NULL,
          risk_reasoning TEXT NOT NULL,
          status task_status DEFAULT 'PLANNED' NOT NULL,
          idempotency_key VARCHAR(128) UNIQUE NOT NULL,
          confidence_assessment JSONB DEFAULT '{}'::jsonb NOT NULL,
          explanation JSONB DEFAULT '{}'::jsonb NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_cognitive_tasks_store_status ON cognitive_tasks(store_id, status)")
    op.execute("CREATE INDEX idx_cognitive_tasks_issue ON cognitive_tasks(issue_id)")

    op.execute(
        """
        CREATE TABLE approvals (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          task_id UUID UNIQUE NOT NULL REFERENCES cognitive_tasks(id) ON DELETE CASCADE,
          issue_id UUID NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          status approval_status DEFAULT 'PENDING' NOT NULL,
          approver_user_id UUID,
          merchant_rationale TEXT,
          execution_override_params JSONB,
          expires_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
          decided_at TIMESTAMP WITH TIME ZONE,
          created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
        # NOTE: `approver_user_id` deliberately has NO `REFERENCES users(id)`
        # clause — no `users` table exists anywhere in this codebase (see
        # module docstring above).
    )
    op.execute("CREATE INDEX idx_approvals_store_status ON approvals(store_id, status, expires_at)")

    op.execute(
        """
        CREATE TABLE executions (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          task_id UUID UNIQUE NOT NULL REFERENCES cognitive_tasks(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          status execution_status DEFAULT 'STARTED' NOT NULL,
          request_payload JSONB NOT NULL,
          response_payload JSONB,
          error_log TEXT,
          execution_duration_ms INT,
          retry_count INT DEFAULT 0 NOT NULL,
          started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          completed_at TIMESTAMP WITH TIME ZONE
        )
        """
    )
    op.execute("CREATE INDEX idx_executions_store_status ON executions(store_id, status)")

    op.execute(
        """
        CREATE TABLE verifications (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          execution_id UUID UNIQUE NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
          task_id UUID NOT NULL REFERENCES cognitive_tasks(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          status verification_status DEFAULT 'PASSED' NOT NULL,
          method VARCHAR(100) NOT NULL,
          result_data JSONB NOT NULL,
          verified_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_verifications_task ON verifications(task_id, status)")

    op.execute(
        """
        CREATE TABLE rollbacks (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          execution_id UUID UNIQUE NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
          task_id UUID NOT NULL REFERENCES cognitive_tasks(id) ON DELETE CASCADE,
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          status execution_status DEFAULT 'STARTED' NOT NULL,
          reverted_state JSONB NOT NULL,
          rollback_reason TEXT NOT NULL,
          error_log TEXT,
          started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
          completed_at TIMESTAMP WITH TIME ZONE
        )
        """
        # Reuses `execution_status` (STARTED/COMPLETED/FAILED) rather than a
        # dedicated sixth enum type — see module docstring.
    )
    op.execute("CREATE INDEX idx_rollbacks_task ON rollbacks(task_id)")

    op.execute(
        """
        CREATE TABLE audit_logs (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
          shift_id UUID REFERENCES shifts(id) ON DELETE SET NULL,
          task_id UUID REFERENCES cognitive_tasks(id) ON DELETE SET NULL,
          execution_id UUID REFERENCES executions(id) ON DELETE SET NULL,
          actor_type VARCHAR(50) NOT NULL,
          actor_id VARCHAR(100) NOT NULL,
          action VARCHAR(100) NOT NULL,
          before_state JSONB,
          after_state JSONB,
          rationale TEXT NOT NULL,
          model_identifier VARCHAR(100),
          prompt_version VARCHAR(50),
          "timestamp" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
        """
    )
    op.execute('CREATE INDEX idx_audit_logs_store_timestamp ON audit_logs(store_id, "timestamp" DESC)')
    op.execute("CREATE INDEX idx_audit_logs_task ON audit_logs(task_id)")

    # --- Row-Level Security (every new table has a store_id column) --------
    for table in ("cognitive_tasks", "approvals", "executions", "verifications", "rollbacks", "audit_logs"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY store_isolation_policy ON {table}
              FOR ALL
              USING (store_id = current_setting('app.current_store_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in _NEW_TABLES_CHILD_TO_PARENT:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS verification_status")
    op.execute("DROP TYPE IF EXISTS execution_status")
    op.execute("DROP TYPE IF EXISTS approval_status")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS risk_level")
