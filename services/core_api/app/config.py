"""Application configuration.

Centralizes every environment variable Sprint 1 depends on. No framework
(FastAPI/SQLAlchemy) logic lives here — this module is a pure settings object
so it can be imported from domain code without violating the Clean
Architecture layering rule (domain/ must have no framework imports).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# `env_file` paths are resolved relative to the process's CWD at runtime, not
# this file's location. Under `shopify app dev`, dev.sh `cd`s into
# services/core_api before launching uvicorn, but the real, filled-in `.env`
# only lives at the repo root (matching docker-compose.yml's
# `env_file: ../.env`) — services/core_api/.env has never existed. Listing
# the repo-root .env by absolute path first (falling back to a CWD-relative
# `.env` so a local override or a docker-context copy still works) means
# config loads correctly regardless of which directory the process was
# launched from.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(_REPO_ROOT_ENV, ".env"), extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # Shopify OAuth (Sprint 1 Dependencies). Shopify CLI injects the app's
    # credentials as SHOPIFY_API_KEY / SHOPIFY_API_SECRET (its own naming
    # convention, distinct from this app's .env variable names) into every
    # process it spawns — those must take priority so `shopify app dev` works
    # without duplicating the client id/secret into .env by hand.
    shopify_app_client_id: str = Field(
        default="", validation_alias=AliasChoices("SHOPIFY_API_KEY", "SHOPIFY_APP_CLIENT_ID")
    )
    shopify_app_secret: str = Field(
        default="", validation_alias=AliasChoices("SHOPIFY_API_SECRET", "SHOPIFY_APP_SECRET")
    )
    shopify_api_version: str = "2024-07"

    # Public base URL used to build the OAuth redirect_uri. When running
    # under `shopify app dev`, Shopify CLI injects APP_URL (and HOST) into
    # this process with the current tunnel's live URL — that takes priority
    # over SHOPIFY_APP_URL from .env so the tunnel's ever-changing URL never
    # has to be copied in by hand. Falls back to SHOPIFY_APP_URL (.env) for
    # non-CLI setups (e.g. a manually managed ngrok/production domain).
    shopify_app_url: str = Field(
        default="https://api.nightshift.ai",
        validation_alias=AliasChoices("APP_URL", "HOST", "SHOPIFY_APP_URL"),
    )

    # Required scopes — exact 7-scope list mandated by Sprint 1 Story 1 AC
    # and corroborated verbatim by SATDD Section 7.4. Do not add
    # write_payment_gateways or any other scope without a spec change.
    shopify_oauth_scopes: tuple[str, ...] = (
        "read_products",
        "write_products",
        "read_discounts",
        "write_discounts",
        "read_themes",
        "write_themes",
        "read_script_tags",
        "write_script_tags",
    )

    # HMAC / timestamp freshness window (Risk 1 mitigation: configurable drift)
    oauth_timestamp_drift_seconds: int = 300

    # Database
    database_url: str = "postgresql+asyncpg://nightshift:nightshift_dev_password@localhost:5432/nightshift"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Token encryption. kms_key_id names the production KMS key reference
    # (AWS KMS / GCP KMS ARN). local_data_key is a base64 32-byte key used only
    # when no KMS integration is configured (local/dev/test).
    kms_key_id: str = ""
    nightshift_local_data_key: str = ""

    # AI embeddings (Sprint 1 AI Spec — infra only, not invoked until Sprint 2).
    # Sprint 2's own Dependencies section names a distinct `GEMINI_API_KEY`
    # variable; this key already exists for exactly this purpose ("not
    # invoked until Sprint 2") so it is reused rather than adding a second,
    # redundant credential — see CONFLICTS.md item 13 / DECISIONS.md ADR-017.
    google_ai_api_key: str = ""

    # Google/XPRIZE Agentic AI hackathon requirement: Gemini "accessed
    # through Gemini API OR Vertex AI" — either counts, but the Gemini
    # Developer API (`google_ai_api_key` above, AI Studio) draws down a
    # SEPARATE "Prepay" credit balance from the project's normal Google
    # Cloud Billing account (confirmed live: a real GCP project with
    # available credit still hit `429 RESOURCE_EXHAUSTED` on the Prepay
    # path). Vertex AI bills the exact same Gemini models through the
    # project's standard Cloud Billing account instead — the same GCP
    # credit that pays for hosting also pays for these calls. When
    # `gcp_project_id` is set, `GeminiClient` constructs a Vertex-mode
    # `genai.Client` (`vertexai=True, project=..., location=...`) instead of
    # an API-key one; empty (the default) keeps the original Developer API
    # path so this remains fully backward compatible with no GCP project
    # configured at all. Vertex auth is Application Default Credentials
    # (`gcloud auth application-default login` locally, or a service
    # account's attached identity in Cloud Run/GCE) — never a second API
    # key to manage.
    gcp_project_id: str = Field(default="", validation_alias=AliasChoices("GCP_PROJECT_ID"))
    # Confirmed live against a real GCP project: gemini-3.6-flash 404s on the
    # regional us-central1 endpoint but works on 'global' — newer Gemini
    # models are increasingly served only via the global Vertex AI endpoint,
    # not every regional one. Don't assume a region works without checking
    # Vertex AI Model Garden for the specific model.
    gcp_location: str = Field(default="global", validation_alias=AliasChoices("GCP_LOCATION"))

    # Cloud Run migration: Celery Beat (a persistent process) has no direct
    # equivalent on Cloud Run, which only runs request/event-triggered
    # services. Cloud Scheduler replaces it — a cron-style HTTP call to
    # POST /internal/dispatch-nightly-shifts, authenticated by this shared
    # secret rather than a merchant session (Cloud Scheduler is not a
    # Shopify-embedded caller). Empty by default: the endpoint always 401s
    # until explicitly configured, same "off unless configured" posture as
    # DEMO_MODE_ENABLED/BILLING_ENABLED above.
    internal_dispatch_secret: str = Field(default="", validation_alias=AliasChoices("INTERNAL_DISPATCH_SECRET"))

    # Gemini model identifier, shared by the per-specialist agents (via
    # `llm_provider`) and Chief Ops AI's always-on Executive Briefing (via
    # `chief_ops_llm_provider`, which defaults to GEMINI independent of
    # `llm_provider` — see `infrastructure/llm/factory.py`). History: every
    # source document hardcoded 'gemini-1.5-pro' (retired), which was
    # substituted for 'gemini-2.5-pro' (CONFLICTS.md item 12 / ADR-016);
    # bumped again to 'gemini-3.6-flash' — a stable, GA Flash-tier model —
    # to satisfy the Google/XPRIZE hackathon's "Gemini 3.5 or newer" access
    # requirement (2.5-pro no longer qualifies). Kept fully configurable via
    # GEMINI_MODEL_NAME so a future model swap never requires a code change.
    gemini_model_name: str = Field(default="gemini-3.6-flash", validation_alias=AliasChoices("GEMINI_MODEL_NAME"))

    # --- LLM provider selection & cost hardening -----------------------------
    # Gemini is the sole supported provider — every LLM call in this
    # codebase, for every specialist agent and for Chief Ops AI, goes
    # through Gemini (optionally via Vertex AI, see `gcp_project_id` above).
    # `infrastructure/llm/factory.py` picks the concrete client from this
    # setting; ProductQualityAgent itself has no idea which provider is
    # active, so this is purely a config value, never a code change.
    llm_provider: str = Field(default="GEMINI", validation_alias=AliasChoices("LLM_PROVIDER"))

    # --- Chief Ops AI / Executive Briefing provider ------------------------
    # A SEPARATE setting from `llm_provider` above (not merged into one) so
    # the two call sites — every specialist agent's own detection reasoning
    # vs. Chief Ops AI's shift-level executive synthesis
    # (`infrastructure/llm/factory.py::build_chief_ops_llm_client`,
    # `workers/tasks/shift_report.py`) — can be pointed at different
    # models/regions independently if ever needed, even though both resolve
    # to Gemini today.
    chief_ops_llm_provider: str = Field(
        default="GEMINI", validation_alias=AliasChoices("CHIEF_OPS_LLM_PROVIDER")
    )

    # Hard cap on output tokens per LLM call — the single biggest lever on
    # per-call cost. 1024 is comfortably enough for a
    # handful of issue descriptions; raise only if analyses start getting
    # truncated (ProductQualityAgent falls back deterministically if they do
    # rather than accepting a partial/invalid response).
    llm_max_output_tokens: int = Field(default=1024, validation_alias=AliasChoices("LLM_MAX_OUTPUT_TOKENS"))

    # Hard daily ceiling on LLM calls, enforced by LlmCallBudgetGuard
    # (Redis-backed, so it holds across Celery worker processes/restarts,
    # not just a single Python process). Exists so a bug, a Celery retry
    # storm, or repeated manual test triggers can never exhaust a metered
    # API key's budget in one runaway session — see DECISIONS.md ADR-024.
    # Raised from 30 -> 200 in Sprint 5 Phase 1.3 (ADR-040): Chief Ops now
    # attempts an LLM call for every shift with 1+ specialist findings
    # (previously gated at 2+ categories), and demo/rehearsal days trigger
    # many on-demand shifts via the Chaos Panel — 30 could be exhausted
    # well before a live demo finished.
    llm_max_calls_per_day: int = Field(default=200, validation_alias=AliasChoices("LLM_MAX_CALLS_PER_DAY"))

    # Caps how many inspection findings are sent to the LLM in a single
    # call, regardless of catalog size — bounds prompt/input-token growth
    # for very large or very messy catalogs. Findings are prioritized by
    # severity before truncation (CRITICAL/HIGH kept first).
    llm_max_findings_per_call: int = Field(
        default=50, validation_alias=AliasChoices("LLM_MAX_FINDINGS_PER_CALL")
    )

    # Observability
    otel_exporter_otlp_endpoint: str = ""

    # --- Sprint 4 Step 1: Demo Incident Generator -----------------------
    # Gates `POST /api/v1/demo/incidents/{scenario_id}` — a capability that
    # lets an authenticated store deliberately corrupt its own live data on
    # cue (e.g. create overlapping stackable discount codes) purely so a
    # live demo doesn't have to wait for an organic incident. Defaults to
    # False so this is never reachable in a real merchant's production
    # deployment unless a maintainer explicitly opts in; set
    # DEMO_MODE_ENABLED=true in dev/demo environments only.
    demo_mode_enabled: bool = Field(default=False, validation_alias=AliasChoices("DEMO_MODE_ENABLED"))

    # --- Nightly scheduler ------------------------------------------------
    # Closes a known gap flagged since Sprint 2's own completion report:
    # "'Morning Shift Report' implies nightly automation for real merchant
    # use... Open product decision, not yet made." Before this, every shift
    # (across all four specialists) only ever ran when someone manually
    # enqueued `tasks.inspect_catalog` — nothing in NightShift ran on its own.
    # `celery beat` (a separate process — see docker-compose.yml's `beat`
    # service) fires `tasks.dispatch_nightly_shifts` on this interval, which
    # fans out `tasks.inspect_catalog` to every currently-active store.
    # Defaults to true/1440 (genuinely nightly) — lower
    # SHIFT_SCHEDULE_INTERVAL_MINUTES for local testing (e.g. to 5) to watch
    # it fire without waiting a full day.
    shift_schedule_enabled: bool = Field(default=True, validation_alias=AliasChoices("SHIFT_SCHEDULE_ENABLED"))
    shift_schedule_interval_minutes: int = Field(
        default=1440, validation_alias=AliasChoices("SHIFT_SCHEDULE_INTERVAL_MINUTES")
    )

    # --- Billing: NightShift Free / Pro / Business monetization -----------
    # Ops-level kill switch for `POST /api/v1/billing/subscribe`, mirroring
    # `demo_mode_enabled`'s own pattern — but defaults to True (billing is
    # this app's real monetization path, not a demo-only capability), so it
    # never needs to be explicitly turned on for the feature to work.
    billing_enabled: bool = Field(default=True, validation_alias=AliasChoices("BILLING_ENABLED"))

    # Passed straight through as `appSubscriptionCreate`'s own `test: Boolean`
    # argument (see infrastructure/shopify_client.py's module comment for the
    # confirmed mutation shape) — a `test` charge is never actually billed to
    # the merchant, Shopify's own documented dev/test-store behavior.
    # Defaults to True so a hackathon/dev deployment can never accidentally
    # create a real, billed charge; set SHOPIFY_BILLING_TEST_MODE=false only
    # for a genuine production deployment.
    shopify_billing_test_mode: bool = Field(
        default=True, validation_alias=AliasChoices("SHOPIFY_BILLING_TEST_MODE")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
