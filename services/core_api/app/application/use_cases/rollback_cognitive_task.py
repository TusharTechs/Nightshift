"""Use case: Rollback Cognitive Task — Sprint 3 AI Trust & Execution.

Reverts a completed Execution's mutation using the compensating parameters
stored in the CognitiveTask's `execution_plan["rollback"]` sub-dict at Plan
time. Framework-agnostic: Protocol ports + `ShopifyGraphQLClient` only.

Called from two places: `VerifyExecution.execute()` when a read-after-write
check fails (automatic rollback), and `POST /api/v1/tasks/{task_id}/rollback`
(manual, merchant-triggered rollback of a COMPLETED execution).

Hard requirement: never raise past a rollback failure — this is already deep
in a failure path (verification failed, or a merchant explicitly asked to
undo something). A failed rollback attempt must still be recorded (a
`rollbacks` row with status=FAILED, plus an audit log entry) so the failure
is never silently invisible, but the caller must be able to keep going and
still mark the task/issue FAILED and surface it.
"""

from __future__ import annotations

import uuid

import structlog

from app.api.errors import ShopifyApiProblem
from app.application.ports import (
    AuditLogRepository,
    CognitiveTaskRepository,
    ExecutionRepository,
    RollbackRepository,
)
from app.domain.models import Rollback
from app.infrastructure.shopify_client import ShopifyGraphQLClient, ThemeWriteAccessDeniedError

logger = structlog.get_logger(component="rollback_cognitive_task")

DEFAULT_ROLLBACK_REASON = "Verification failed"


class RollbackCognitiveTask:
    def __init__(
        self,
        *,
        executions: ExecutionRepository,
        rollbacks: RollbackRepository,
        cognitive_tasks: CognitiveTaskRepository,
        audit_logs: AuditLogRepository,
        shopify_client: ShopifyGraphQLClient,
    ) -> None:
        self._executions = executions
        self._rollbacks = rollbacks
        self._cognitive_tasks = cognitive_tasks
        self._audit_logs = audit_logs
        self._shopify_client = shopify_client

    async def execute(self, execution_id: uuid.UUID, *, reason: str = DEFAULT_ROLLBACK_REASON) -> Rollback:
        execution = await self._executions.get_by_id(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        task = await self._cognitive_tasks.get_by_id(execution.task_id)
        if task is None:
            raise ValueError(f"CognitiveTask {execution.task_id} not found")

        plan = task.execution_plan
        items = plan.get("items", [])
        reverted_state = {"items": [item.get("rollback", {}) for item in items]}
        rollback = await self._rollbacks.create(
            execution_id=execution_id,
            task_id=task.id,
            store_id=task.store_id,
            reverted_state=reverted_state,
            rollback_reason=reason,
        )

        # Attempt every item's rollback regardless of earlier items' outcome
        # — "never raise past a rollback failure" applies per item too, not
        # just to the batch as a whole; a failure on item 2 must not leave
        # items 3..N un-reverted.
        responses: list[dict] = []
        errors: list[str] = []
        for item in items:
            try:
                responses.append(await self._dispatch_rollback_mutation(item))
            except (ShopifyApiProblem, ThemeWriteAccessDeniedError) as exc:
                # ThemeWriteAccessDeniedError (productionization phase): a
                # `theme_restore` rollback item can hit this if Shopify's
                # write exemption was somehow revoked between Execute and
                # this rollback — a distinct exception type from
                # ShopifyApiProblem by design (see its own docstring), so it
                # must be caught explicitly here too or it would propagate
                # past this "never raise past a rollback failure" method
                # entirely, unlike every other rollback failure mode.
                errors.append(str(exc))

        if errors:
            await self._rollbacks.mark_failed(rollback.id, error_log="; ".join(errors))
            await self._audit_logs.append(
                store_id=task.store_id,
                shift_id=task.shift_id,
                task_id=task.id,
                execution_id=execution_id,
                actor_type="AI_AGENT",
                actor_id=task.action_type,
                action="ROLLBACK_FAILED",
                rationale=f"Rollback attempt failed: {'; '.join(errors)}",
            )
            logger.error(
                "rollback_cognitive_task_failed",
                task_id=str(task.id),
                execution_id=str(execution_id),
                status="error",
                error="; ".join(errors),
            )
            refreshed = await self._rollbacks.get_by_id(rollback.id)
            return refreshed or rollback

        await self._rollbacks.mark_completed(rollback.id)
        await self._executions.mark_rolled_back(execution_id)
        await self._audit_logs.append(
            store_id=task.store_id,
            shift_id=task.shift_id,
            task_id=task.id,
            execution_id=execution_id,
            actor_type="AI_AGENT",
            actor_id=task.action_type,
            action="ROLLBACK_COMPLETED",
            rationale=f"Reverted {task.action_type} ({len(items)} item(s)) via {plan.get('mutation')}.",
            after_state={"items": responses},
        )
        logger.info(
            "rollback_cognitive_task_succeeded",
            task_id=str(task.id),
            execution_id=str(execution_id),
            status="success",
        )
        refreshed = await self._rollbacks.get_by_id(rollback.id)
        return refreshed or rollback

    async def _dispatch_rollback_mutation(self, item: dict) -> dict:
        rollback_plan = item.get("rollback", {})
        mutation = rollback_plan.get("mutation")
        if mutation == "productImageUpdate":
            return await self._shopify_client.update_product_image_alt_text(
                product_gid=item.get("product_gid", ""),
                image_gid=item.get("image_gid", ""),
                # LIMITATION (ADR-030): the true prior ALT text is not
                # captured this sprint; `rollback_plan["alt_text"]` is
                # always None, so rollback restores an empty string rather
                # than the merchant's real original value.
                alt_text=rollback_plan.get("alt_text") or "",
            )
        if mutation == "productUpdate":
            return await self._shopify_client.update_product_description(
                product_gid=item.get("product_gid", ""),
                # LIMITATION (ADR-030): the true prior descriptionHtml is
                # not captured this sprint; restores to an empty string.
                description_html=rollback_plan.get("description_html") or "",
            )
        if mutation == "discountCodeActivate":
            # Sprint 4 Step 2: unlike the two LIMITATIONs above, this
            # rollback is exact — "prior state" is simply "active," which
            # discountCodeActivate reproduces precisely, no placeholder gap.
            return await self._shopify_client.activate_discount_code(
                discount_id=rollback_plan.get("discount_id", ""),
            )
        if mutation == "scriptTagDelete":
            # Sprint 4 Step 3: `script_tag_id` is patched into this dict
            # post-execution by `execute_cognitive_task.py` (never known at
            # Plan time — see its own comment); `delete_script_tag` raises
            # `ShopifyApiProblem` itself if it's somehow still missing,
            # which this method's caller already treats as a rollback
            # failure, not a silent no-op.
            return await self._shopify_client.delete_script_tag(
                script_tag_id=rollback_plan.get("script_tag_id", ""),
            )
        if mutation == "theme_restore":
            # Productionization phase: patched into this dict post-execution
            # by `execute_cognitive_task.py`'s "theme_restore_guide" branch,
            # ONLY for an item whose automated `themeFilesUpsert` restore
            # genuinely succeeded (never known at Plan time — same reasoning
            # as `scriptTagCreate`/`scriptTagDelete` above). "Undo this fix"
            # means writing back `content` — the diverged content that was
            # live immediately before this restore, captured in the item's
            # own `before_state` at Plan time, never fabricated here.
            return await self._shopify_client.restore_theme_file(
                theme_id=rollback_plan.get("theme_id", ""),
                filename=rollback_plan.get("filename", ""),
                content=rollback_plan.get("content") or "",
            )
        if mutation in (None, "none"):
            # Sprint 4 Step 3: Theme Guardian's guided restore did not
            # execute a live Shopify mutation for this item — either the
            # store lacks the write exemption and Execute fell back to a
            # guided bundle for the merchant to apply themselves (the common
            # case — see `execute_cognitive_task.py`'s own comment), or this
            # is some other action type that genuinely never mutates
            # Shopify. "none" is a real, documented rollback mutation type
            # meaning "nothing to revert," not an unhandled/placeholder gap
            # — but as of the productionization phase it is no longer
            # universally true for `theme_restore_guide` specifically (see
            # the `"theme_restore"` branch above for the case where it did).
            return {"skipped": True, "reason": "no live Shopify mutation was ever executed for this action"}
        raise ValueError(f"Unknown rollback mutation type: {mutation!r}")
