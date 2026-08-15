"""FastAPI dependency wiring — the composition root for Sprint 1's endpoints.

This is the one place allowed to know about both the application layer
(use cases, ports) and the infrastructure layer (SQLAlchemy repositories,
Celery dispatcher, token cipher), per the Clean Architecture layering rule.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import StoreNotFoundProblem, UnauthorizedProblem
from app.application.ports import (
    ApprovalRepository,
    AuditLogRepository,
    CognitiveTaskRepository,
    ExecutionRepository,
    IssueRepository,
    RollbackRepository,
    ShiftReportRepository,
    ShiftRepository,
    StoreRepository,
    SubscriptionRepository,
    TaskDispatcher,
    VerificationRepository,
)
from app.application.use_cases.ask_nightshift import AskNightShift
from app.application.use_cases.complete_oauth_installation import CompleteOAuthInstallation
from app.application.use_cases.handle_approval_action import HandleApprovalAction
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.application.use_cases.trigger_demo_incident import TriggerDemoIncident
from app.config import Settings, get_settings
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlApprovalRepository,
    SqlAuditLogRepository,
    SqlCognitiveTaskRepository,
    SqlExecutionRepository,
    SqlIssueRepository,
    SqlOrganizationRepository,
    SqlRollbackRepository,
    SqlShiftReportRepository,
    SqlShiftRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
    SqlSubscriptionRepository,
    SqlTrackingSnapshotRepository,
    SqlVerificationRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.llm.budget_guard import LlmCallBudgetGuard
from app.infrastructure.llm.factory import build_llm_client
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient


class CeleryTaskDispatcher:
    def dispatch_store_discovery(self, store_id: uuid.UUID) -> str:
        result = celery_app.send_task("tasks.store_discovery", args=[str(store_id)])
        return result.id

    def dispatch_execute_cognitive_task(self, task_id: uuid.UUID) -> str:
        """Sprint 3 Part 2: the one truly-async execution path — dispatched
        only when `HandleApprovalAction` grants an APPROVE decision."""
        result = celery_app.send_task("tasks.execute_cognitive_task", args=[str(task_id)])
        return result.id

    def dispatch_inspect_catalog(self, store_id: uuid.UUID) -> str:
        """Sprint 5 Phase 4: same `tasks.inspect_catalog` entry point the
        nightly scheduler and `scripts/trigger_shift.py` already use — see
        `TaskDispatcher.dispatch_inspect_catalog`'s own docstring."""
        result = celery_app.send_task("tasks.inspect_catalog", args=[str(store_id)])
        return result.id

    def dispatch_nightly_shifts(self) -> str:
        """Cloud Run migration: see `TaskDispatcher.dispatch_nightly_shifts`'s
        own docstring."""
        result = celery_app.send_task("tasks.dispatch_nightly_shifts")
        return result.id


_session_factory_cache: dict[str, async_sessionmaker[AsyncSession]] = {}


def get_session_factory(settings: Settings = Depends(get_settings)) -> async_sessionmaker[AsyncSession]:
    if "default" not in _session_factory_cache:
        engine = create_engine(settings.database_url)
        _session_factory_cache["default"] = create_session_factory(engine)
    return _session_factory_cache["default"]


async def get_db_session(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    """Unit-of-work boundary for the whole request.

    Repository methods only ever `flush()` (visible within this transaction,
    not durable) — by Clean Architecture design, the application-layer use
    cases (e.g. CompleteOAuthInstallation) hold only repository Protocol
    ports, never a raw session, so they cannot commit themselves. This is
    therefore the one place with a real AsyncSession, and the transaction
    must be committed here on success (or rolled back on failure) or every
    write this API makes is silently discarded when the session closes —
    exactly what was happening before this fix: OAuth installation appeared
    to succeed, but Organization/Store/StoreToken rows were never durably
    written, so the dispatched discovery task could never find the store.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_token_cipher(settings: Settings = Depends(get_settings)) -> TokenCipher:
    return TokenCipher.from_base64_key(settings.nightshift_local_data_key)


def get_task_dispatcher() -> TaskDispatcher:
    return CeleryTaskDispatcher()


def get_store_repository(
    session: AsyncSession = Depends(get_db_session),
) -> StoreRepository:
    return SqlStoreRepository(session)


def get_shift_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ShiftRepository:
    return SqlShiftRepository(session)


def get_shift_report_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ShiftReportRepository:
    return SqlShiftReportRepository(session)


def get_complete_oauth_installation_use_case(
    session: AsyncSession = Depends(get_db_session),
    token_cipher: TokenCipher = Depends(get_token_cipher),
    task_dispatcher: TaskDispatcher = Depends(get_task_dispatcher),
    settings: Settings = Depends(get_settings),
) -> CompleteOAuthInstallation:
    return CompleteOAuthInstallation(
        organizations=SqlOrganizationRepository(session),
        stores=SqlStoreRepository(session),
        store_tokens=SqlStoreTokenRepository(session),
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=settings.shopify_app_secret,
        timestamp_drift_seconds=settings.oauth_timestamp_drift_seconds,
        # Billing: every newly installed store gets an auto-provisioned FREE
        # subscription row (see CompleteOAuthInstallation's own comment).
        subscriptions=SqlSubscriptionRepository(session),
    )


def get_subscription_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SubscriptionRepository:
    return SqlSubscriptionRepository(session)


async def get_current_store_id(
    authorization: str = Header(default=""),
    x_shopify_shop_domain: str = Header(default="", alias="X-Shopify-Shop-Domain"),
    settings: Settings = Depends(get_settings),
    stores: StoreRepository = Depends(get_store_repository),
) -> uuid.UUID:
    """Validate the Shopify session JWT (Section 8.4: Bearer JWT, JWKS-backed,
    15-minute lifetime) and resolve it to our internal store id.

    Real Shopify session tokens (App Bridge's getSessionToken()) only ever
    carry Shopify's own standard claims — iss/dest/aud/sub/exp/nbf/iat/jti/sid
    — never a custom "store_id" claim, so the store must be resolved via the
    `dest` claim (the shop's myshopify.com domain) against
    StoreRepository.get_by_shopify_domain, falling back to the
    X-Shopify-Shop-Domain header the frontend already sends (lib/api.ts) if
    `dest` is absent. JWKS verification itself is a production integration
    point (fetching Shopify's public keys) — Sprint 1 validates structure and
    expiry so the endpoint contract and 401 behavior are correct end-to-end;
    full JWKS signature verification against Shopify's live keys should be
    enabled with real key material before production traffic.
    """
    if not authorization.startswith("Bearer "):
        raise UnauthorizedProblem("Missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ")
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": True})
    except jwt.PyJWTError as exc:
        raise UnauthorizedProblem(f"Invalid session token: {exc}") from exc

    dest = claims.get("dest", "") or ""
    shop_domain = dest.removeprefix("https://").removeprefix("http://").rstrip("/") or x_shopify_shop_domain
    if not shop_domain:
        raise UnauthorizedProblem("Session token missing dest claim and no shop domain header provided")

    store = await stores.get_by_shopify_domain(shop_domain)
    if store is None:
        raise UnauthorizedProblem(f"No store found for shop domain {shop_domain}")

    return store.id


# --- Sprint 3: AI Trust & Execution -----------------------------------------


def get_issue_repository(session: AsyncSession = Depends(get_db_session)) -> IssueRepository:
    return SqlIssueRepository(session)


def get_cognitive_task_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CognitiveTaskRepository:
    return SqlCognitiveTaskRepository(session)


def get_approval_repository(session: AsyncSession = Depends(get_db_session)) -> ApprovalRepository:
    return SqlApprovalRepository(session)


def get_execution_repository(session: AsyncSession = Depends(get_db_session)) -> ExecutionRepository:
    return SqlExecutionRepository(session)


def get_verification_repository(
    session: AsyncSession = Depends(get_db_session),
) -> VerificationRepository:
    return SqlVerificationRepository(session)


def get_rollback_repository(session: AsyncSession = Depends(get_db_session)) -> RollbackRepository:
    return SqlRollbackRepository(session)


def get_audit_log_repository(session: AsyncSession = Depends(get_db_session)) -> AuditLogRepository:
    return SqlAuditLogRepository(session)


def get_handle_approval_action_use_case(
    session: AsyncSession = Depends(get_db_session),
    task_dispatcher: TaskDispatcher = Depends(get_task_dispatcher),
) -> HandleApprovalAction:
    return HandleApprovalAction(
        approvals=SqlApprovalRepository(session),
        cognitive_tasks=SqlCognitiveTaskRepository(session),
        issues=SqlIssueRepository(session),
        shifts=SqlShiftRepository(session),
        audit_logs=SqlAuditLogRepository(session),
        dispatch_execution=task_dispatcher.dispatch_execute_cognitive_task,
    )


async def _shopify_client_for_store_id(
    store_id: uuid.UUID,
    stores: StoreRepository,
    session: AsyncSession,
    token_cipher: TokenCipher,
    settings: Settings,
) -> AsyncGenerator[ShopifyGraphQLClient, None]:
    """Shared builder behind both `get_shopify_client_for_store` (resolves
    `store_id` from the Bearer session token, per Sprint 1's auth
    dependency) and `get_shopify_client_for_store_id` (Billing's
    `GET /api/v1/billing/confirm` — the Shopify `returnUrl` redirect target,
    which carries no Bearer session token at all, only `store_id` as its own
    query param — see `api/v1/billing.py`'s own module docstring). Decrypts
    the stored token here, at the API composition root, never inside a use
    case."""
    store = await stores.get_by_id(store_id)
    if store is None:
        raise StoreNotFoundProblem(f"No store found for id {store_id}")

    token_repo = SqlStoreTokenRepository(session)
    token_row = await token_repo.get_by_store_id(store_id)
    if token_row is None:
        raise StoreNotFoundProblem(f"No Shopify access token on file for store {store_id}")

    access_token = token_cipher.decrypt(EncryptedPayload.deserialize(token_row.access_token_encrypted))
    client = ShopifyGraphQLClient(
        shop_domain=store.shopify_domain,
        access_token=access_token,
        api_version=settings.shopify_api_version,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def get_shopify_client_for_store(
    store_id: uuid.UUID = Depends(get_current_store_id),
    stores: StoreRepository = Depends(get_store_repository),
    session: AsyncSession = Depends(get_db_session),
    token_cipher: TokenCipher = Depends(get_token_cipher),
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[ShopifyGraphQLClient, None]:
    """Builds a real `ShopifyGraphQLClient` for the Bearer-JWT-authenticated
    store — decrypts the stored token here, at the API composition root,
    never inside a use case (`RollbackCognitiveTask`/`ExecuteCognitiveTask`
    are handed an already-constructed client, per this sprint's layering
    rule).
    """
    async for client in _shopify_client_for_store_id(store_id, stores, session, token_cipher, settings):
        yield client


async def get_shopify_client_for_store_id(
    store_id: uuid.UUID,
    stores: StoreRepository = Depends(get_store_repository),
    session: AsyncSession = Depends(get_db_session),
    token_cipher: TokenCipher = Depends(get_token_cipher),
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[ShopifyGraphQLClient, None]:
    """Billing-only variant of `get_shopify_client_for_store`: resolves
    `store_id` directly from the request's own `store_id` query parameter
    (shared with the route's own declared parameter of the same name)
    rather than from a Bearer session token — `GET /api/v1/billing/confirm`
    is a plain browser redirect target Shopify itself navigates to after the
    merchant approves/declines a charge, and carries no App Bridge session
    token at all."""
    async for client in _shopify_client_for_store_id(store_id, stores, session, token_cipher, settings):
        yield client


def get_rollback_cognitive_task_use_case(
    session: AsyncSession = Depends(get_db_session),
    shopify_client: ShopifyGraphQLClient = Depends(get_shopify_client_for_store),
) -> RollbackCognitiveTask:
    return RollbackCognitiveTask(
        executions=SqlExecutionRepository(session),
        rollbacks=SqlRollbackRepository(session),
        cognitive_tasks=SqlCognitiveTaskRepository(session),
        audit_logs=SqlAuditLogRepository(session),
        shopify_client=shopify_client,
    )


# --- Sprint 4 Step 1: Demo Incident Generator --------------------------------


def get_trigger_demo_incident_use_case(
    session: AsyncSession = Depends(get_db_session),
    shopify_client: ShopifyGraphQLClient = Depends(get_shopify_client_for_store),
) -> TriggerDemoIncident:
    return TriggerDemoIncident(
        shopify_client=shopify_client,
        audit_logs=SqlAuditLogRepository(session),
        # Sprint 4 Step 3: lets Scenario 2's trigger seed a tracking_snapshots
        # baseline row directly (see TriggerDemoIncident's own docstring).
        tracking_snapshots=SqlTrackingSnapshotRepository(session),
    )


# --- Sprint 4 Step 4: Ask NightShift -----------------------------------------


async def get_ask_nightshift_use_case(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AsyncGenerator[AskNightShift, None]:
    """Same cost-guardrail pattern as `inspection.py`'s Product Quality LLM
    call (ADR-024): a Redis-backed daily budget guard shared across every
    LLM-calling surface in this deployment via the same `llm:call_budget:*`
    key, so Ask NightShift can never itself blow past the ceiling a merchant
    (or developer) has configured."""
    llm_client = build_llm_client(settings)
    redis_client = aioredis.from_url(settings.redis_url)
    try:
        budget_guard = LlmCallBudgetGuard(
            backend=redis_client, max_calls_per_day=settings.llm_max_calls_per_day
        )
        yield AskNightShift(
            shift_reports=SqlShiftReportRepository(session),
            client=llm_client,
            budget_guard=budget_guard,
        )
    finally:
        await redis_client.aclose()
