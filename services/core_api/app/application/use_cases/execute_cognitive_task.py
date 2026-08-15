"""Use case: Execute Cognitive Task — Sprint 3 AI Trust & Execution, the
Execute step. Carries out a planned CognitiveTask's `execution_plan` against
the Shopify Admin API.

Framework-agnostic: depends only on Protocol ports and an already-constructed
`ShopifyGraphQLClient` (an infra collaborator, but one with zero DB/Celery
coupling itself — treated like `StructuredLlmClient` is treated by
`ProductQualityAgent`). Token decryption and client construction are the
caller's responsibility (the Celery task wrapper, or the API route granting
an approval) — this use case is focused purely on "execute the mutation."

Callable from two places: `workers/tasks/execution.py`'s
`tasks.execute_cognitive_task` Celery task (the true async path, dispatched
only on merchant APPROVE), and directly, in-process, from
`PlanCognitiveTasks` for the synchronous auto-execute path. Idempotency
(`Execution.get_by_task_id` returning a terminal row) protects against ever
double-mutating Shopify regardless of which path triggered a given call.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog

from app.api.errors import ShopifyApiProblem
from app.application.ports import AuditLogRepository, CognitiveTaskRepository, ExecutionRepository
from app.domain.models import Execution
from app.infrastructure.shopify_client import ShopifyGraphQLClient, ThemeWriteAccessDeniedError

logger = structlog.get_logger(component="execute_cognitive_task")

MAX_MUTATION_ATTEMPTS = 3
"""Bounded retry loop for `ShopifyApiProblem` raised by mutation-level
`userErrors` (a distinct failure class from transport-level failures, which
`ShopifyGraphQLClient.execute()` already retries internally)."""

_TERMINAL_EXECUTION_STATUSES = {"COMPLETED", "FAILED"}


class ExecuteCognitiveTask:
    def __init__(
        self,
        *,
        cognitive_tasks: CognitiveTaskRepository,
        executions: ExecutionRepository,
        audit_logs: AuditLogRepository,
        shopify_client: ShopifyGraphQLClient,
    ) -> None:
        self.cognitive_tasks = cognitive_tasks
        self.executions = executions
        self.audit_logs = audit_logs
        self._shopify_client = shopify_client

    async def execute(self, task_id: uuid.UUID) -> Execution:
        task = await self.cognitive_tasks.get_by_id(task_id)
        if task is None:
            raise ValueError(f"CognitiveTask {task_id} not found")

        existing = await self.executions.get_by_task_id(task_id)
        if existing is not None and existing.status in _TERMINAL_EXECUTION_STATUSES:
            # Idempotent re-dispatch (e.g. a retried Celery message): the
            # mutation already ran to a terminal state — never re-mutate
            # Shopify a second time for the same task.
            logger.info(
                "execute_cognitive_task_already_terminal",
                task_id=str(task_id),
                execution_status=existing.status,
                status="skipped",
            )
            return existing

        await self.cognitive_tasks.update_status(task_id, "EXECUTING")
        plan = task.execution_plan

        execution = existing or await self.executions.create(
            task_id=task_id, store_id=task.store_id, request_payload=plan
        )

        start = time.perf_counter()
        attempt = 0
        last_error: Exception | None = None

        while attempt < MAX_MUTATION_ATTEMPTS:
            attempt += 1
            try:
                responses = await self._dispatch_mutation(plan)

                if plan.get("mutation") == "scriptTagCreate":
                    # Sprint 4 Step 3: `scriptTagDelete` (the rollback
                    # mutation) needs a real, live script tag id — Shopify
                    # only assigns one once creation actually succeeds, so it
                    # can't have been known at Plan time. Patch each item's
                    # `rollback` dict with the id `create_script_tag` just
                    # returned, and persist the updated execution_plan via
                    # the already-existing `update_execution_plan` repository
                    # method (built for the merchant "Modify" override in
                    # Sprint 3 — reused here for a different purpose: keeping
                    # rollback data current, not merchant edits) so a later
                    # `RollbackCognitiveTask.execute()` call — which always
                    # re-fetches `task.execution_plan` fresh — sees the real
                    # id rather than the Plan-time placeholder-free dict.
                    patched_items = [
                        {
                            **item,
                            "rollback": {**item.get("rollback", {}), "script_tag_id": response.get("id")},
                        }
                        for item, response in zip(plan.get("items", []), responses, strict=True)
                    ]
                    plan = {**plan, "items": patched_items}
                    await self.cognitive_tasks.update_execution_plan(task_id, plan)

                if plan.get("mutation") == "theme_restore_guide":
                    # Productionization phase: a Theme Guardian item's
                    # plan-time rollback is always `{"mutation": "none"}`
                    # (see `theme_guardian.py`) because at Plan time it is
                    # not yet known whether Execute will perform a real,
                    # automated `themeFilesUpsert` write or fall back to a
                    # guided bundle — only Execute knows which happened,
                    # from each item's own response `status`. For any item
                    # that genuinely restored (`status == "restored"`),
                    # patch its rollback to a real, executable mutation that
                    # can put the pre-fix content back — the item's own
                    # `before_state["content"]` (the diverged content that
                    # was live immediately before this restore) is exactly
                    # what "undo this fix" means here, same as reactivating
                    # a deactivated discount undoes that fix. Items that fell
                    # back to a guided bundle keep `{"mutation": "none"}`
                    # unchanged — genuinely correct there, since no live
                    # mutation happened. Same persist-immediately pattern as
                    # `scriptTagCreate` above, for the same reason: this fact
                    # is only known post-execution, and `RollbackCognitiveTask`
                    # always re-fetches `task.execution_plan` fresh.
                    patched_items = [
                        {
                            **item,
                            "rollback": (
                                {
                                    "mutation": "theme_restore",
                                    "theme_id": item["theme_id"],
                                    "filename": item["filename"],
                                    "content": item.get("before_state", {}).get("content"),
                                }
                                if response.get("status") == "restored"
                                else item.get("rollback", {"mutation": "none"})
                            ),
                        }
                        for item, response in zip(plan.get("items", []), responses, strict=True)
                    ]
                    plan = {**plan, "items": patched_items}
                    await self.cognitive_tasks.update_execution_plan(task_id, plan)

                duration_ms = int((time.perf_counter() - start) * 1000)
                # `Execution.response_payload`/`AuditLogEntry.before_state`/
                # `after_state` are all typed `dict`, not `list` — wrap the
                # per-item lists so every item's before/after state is
                # captured, not just item 0's.
                items = plan.get("items", [])
                before_states = {"items": [item.get("before_state") for item in items]}
                after_states = {"items": responses}
                await self.executions.mark_completed(
                    execution.id, response_payload=after_states, execution_duration_ms=duration_ms
                )
                await self.cognitive_tasks.update_status(task_id, "VERIFYING")
                await self.audit_logs.append(
                    store_id=task.store_id,
                    shift_id=task.shift_id,
                    task_id=task_id,
                    execution_id=execution.id,
                    actor_type="AI_AGENT",
                    actor_id=task.action_type,
                    action="EXECUTION_COMPLETED",
                    rationale=f"Executed {task.action_type} mutation ({plan.get('mutation')}, {len(items)} item(s)).",
                    before_state=before_states,
                    after_state=after_states,
                )
                logger.info(
                    "execute_cognitive_task_succeeded",
                    task_id=str(task_id),
                    execution_id=str(execution.id),
                    attempt=attempt,
                    duration_ms=duration_ms,
                    status="success",
                )
                refreshed = await self.executions.get_by_task_id(task_id)
                return refreshed or execution
            except ShopifyApiProblem as exc:
                last_error = exc
                if attempt < MAX_MUTATION_ATTEMPTS:
                    await self.executions.increment_retry_count(execution.id)
                    logger.warning(
                        "execute_cognitive_task_retry",
                        task_id=str(task_id),
                        execution_id=str(execution.id),
                        attempt=attempt,
                        status="retrying",
                        error=str(exc),
                    )
                    await asyncio.sleep(1 * attempt)

        # Exhausted retries — mark failed, but never raise past this point:
        # callers (planning's synchronous auto-execute path in particular)
        # must be able to continue to the next issue instead of the whole
        # shift crashing.
        duration_ms = int((time.perf_counter() - start) * 1000)
        await self.executions.mark_failed(
            execution.id, error_log=str(last_error), execution_duration_ms=duration_ms
        )
        await self.cognitive_tasks.update_status(task_id, "FAILED")
        await self.audit_logs.append(
            store_id=task.store_id,
            shift_id=task.shift_id,
            task_id=task_id,
            execution_id=execution.id,
            actor_type="AI_AGENT",
            actor_id=task.action_type,
            action="EXECUTION_FAILED",
            rationale=f"Execution failed after {MAX_MUTATION_ATTEMPTS} attempts: {last_error}",
        )
        logger.error(
            "execute_cognitive_task_failed",
            task_id=str(task_id),
            execution_id=str(execution.id),
            attempts=MAX_MUTATION_ATTEMPTS,
            status="error",
            error=str(last_error),
        )
        refreshed = await self.executions.get_by_task_id(task_id)
        return refreshed or execution

    async def _dispatch_mutation(self, plan: dict) -> list[dict]:
        """Dispatches one mutation per `plan["items"]` entry (usually one,
        but a multi-product issue's task carries one item per affected
        product — see `product_quality.py`'s Plan-step docstrings). All
        items live under the outer `MAX_MUTATION_ATTEMPTS` retry loop as one
        unit: since every mutation here just SETs a value, re-applying an
        already-succeeded item on a retry is harmless, so there's no need
        for separate per-item retry bookkeeping."""
        mutation = plan.get("mutation")
        responses = []
        for item in plan.get("items", []):
            if mutation == "productImageUpdate":
                response = await self._shopify_client.update_product_image_alt_text(
                    product_gid=item["product_gid"],
                    image_gid=item["image_gid"],
                    alt_text=item["new_alt_text"],
                )
            elif mutation == "productUpdate":
                response = await self._shopify_client.update_product_description(
                    product_gid=item["product_gid"],
                    description_html=item["new_description_html"],
                )
            elif mutation == "discountCodeDeactivate":
                # Sprint 4 Step 2: Checkout Specialist — Duplicate Discount
                # lifecycle. One item per duplicate discount to deactivate.
                response = await self._shopify_client.deactivate_discount_code(
                    discount_id=item["discount_id"],
                )
            elif mutation == "theme_restore_guide":
                # Productionization phase: Theme Guardian first ATTEMPTS a
                # real, live `themeFilesUpsert` restore (approval-gated —
                # this branch only ever runs after a merchant has approved
                # the CognitiveTask). Most stores' app installations lack
                # Shopify's manually-granted theme-file-write exemption (see
                # `shopify_client.py`'s own comment), so
                # `ThemeWriteAccessDeniedError` is the expected, honest
                # outcome there — this falls back to the original Sprint 4
                # behavior (a guided-restore bundle handed to the merchant via
                # a Theme Editor deep link) rather than treating denial as a
                # hard execution failure. A store whose app instance DOES have
                # the exemption gets a genuinely autonomous restore, verified
                # by the normal read-after-write check in
                # `verify_execution.py` (which compares live content against
                # `expected_content` either way, so it needs no changes to
                # handle either outcome).
                try:
                    restore_result = await self._shopify_client.restore_theme_file(
                        theme_id=item["theme_id"],
                        filename=item["filename"],
                        content=item["expected_content"],
                    )
                    response = {**restore_result, "patch_content": item["expected_content"]}
                    logger.info(
                        "theme_restore_automated_write_succeeded",
                        filename=item["filename"],
                        theme_id=item["theme_id"],
                        status="restored",
                    )
                except ThemeWriteAccessDeniedError as exc:
                    logger.info(
                        "theme_restore_automated_write_denied_falling_back_to_guide",
                        filename=item["filename"],
                        theme_id=item["theme_id"],
                        status="fallback",
                        error=str(exc),
                    )
                    theme_numeric_id = item["theme_id"].rsplit("/", 1)[-1]
                    # `{shop}.myshopify.com/admin/themes/{id}/editor` opens
                    # the visual drag-and-drop Theme Customizer, not the
                    # Liquid code editor a merchant needs to paste this patch
                    # into — the correct destination is the unified admin's
                    # theme-code page,
                    # `admin.shopify.com/store/{handle}/themes/{id}` (no
                    # `/editor` suffix), confirmed directly against the live
                    # Shopify admin, not assumed from docs.
                    store_handle = self._shopify_client.shop_domain.removesuffix(".myshopify.com")
                    response = {
                        "filename": item["filename"],
                        "theme_editor_url": (
                            f"https://admin.shopify.com/store/{store_handle}/themes/{theme_numeric_id}"
                        ),
                        "patch_content": item["expected_content"],
                        "status": "guide_generated",
                        "automated_restore_denied_reason": str(exc),
                    }
            elif mutation == "scriptTagCreate":
                # Sprint 4 Step 3: Tracking Specialist — recreates a removed
                # script tag from its own snapshot.
                created = await self._shopify_client.create_script_tag(
                    src=item["src"], display_scope=item.get("display_scope", "ONLINE_STORE")
                )
                response = created
            else:
                raise ValueError(f"Unknown execution_plan mutation type: {mutation!r}")
            responses.append(response)
        return responses
