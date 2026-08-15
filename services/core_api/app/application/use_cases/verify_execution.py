"""Use case: Verify Execution — Sprint 3 AI Trust & Execution, the Verify /
Explain steps.

Hard axiom (never violated): ALWAYS re-query Shopify after a mutation —
never trust the mutation response alone. `ShopifyGraphQLClient.fetch_product_state`
is the one and only source of truth this use case compares against.

If verification fails: roll back when possible, mark the execution/task
as failed, surface the failure (issue reopens to OPEN so it resurfaces next
shift) — never silently continue.
"""

from __future__ import annotations

import uuid

import structlog

from app.application.ports import (
    AuditLogRepository,
    CognitiveTaskRepository,
    ExecutionRepository,
    IssueRepository,
    ShiftRepository,
    VerificationRepository,
)
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.domain.explanation import build_explanation
from app.domain.models import Verification
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="verify_execution")

VERIFICATION_METHOD = "READ_AFTER_WRITE"


class VerifyExecution:
    def __init__(
        self,
        *,
        cognitive_tasks: CognitiveTaskRepository,
        executions: ExecutionRepository,
        verifications: VerificationRepository,
        issues: IssueRepository,
        shifts: ShiftRepository,
        audit_logs: AuditLogRepository,
        rollback_cognitive_task: RollbackCognitiveTask,
        shopify_client: ShopifyGraphQLClient,
    ) -> None:
        self._cognitive_tasks = cognitive_tasks
        self._executions = executions
        self._verifications = verifications
        self._issues = issues
        self._shifts = shifts
        self._audit_logs = audit_logs
        self._rollback_cognitive_task = rollback_cognitive_task
        self._shopify_client = shopify_client

    async def execute(self, execution_id: uuid.UUID) -> Verification:
        execution = await self._executions.get_by_id(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        task = await self._cognitive_tasks.get_by_id(execution.task_id)
        if task is None:
            raise ValueError(f"CognitiveTask {execution.task_id} not found")

        plan = task.execution_plan
        issue = await self._issues.get_by_id(task.issue_id)

        # One `items` entry per affected product (see `product_quality.py`'s
        # Plan-step docstrings) — every item must independently verify via
        # read-after-write for the task (and its issue) to count as
        # resolved. A multi-product issue is never marked RESOLVED on the
        # strength of just one of its N products actually changing.
        items = plan.get("items", [])
        states = []
        item_results = []
        for item in items:
            if task.action_type == "DEACTIVATE_DUPLICATE_DISCOUNT":
                # Sprint 4 Step 2: discount lifecycle items are keyed by
                # discount_id, not product_gid — a distinct read-after-write
                # fetch, same "always re-query, never trust the mutation
                # response alone" axiom.
                state = await self._shopify_client.fetch_discount_state(discount_id=item["discount_id"])
            elif task.action_type == "GENERATE_THEME_RESTORE_GUIDE":
                # Productionization phase: this re-fetch is identical whether
                # `execute_cognitive_task.py` performed a real automated
                # `themeFilesUpsert` write OR fell back to a guided bundle the
                # MERCHANT then applied themselves via the Theme Editor — in
                # both cases the only thing that matters is whether the
                # theme's LIVE content now matches the baseline, per the same
                # "always re-query, never trust the mutation response alone"
                # axiom used everywhere else in this use case.
                files = await self._shopify_client.fetch_theme_files(
                    theme_id=item["theme_id"], filenames=[item["filename"]]
                )
                state = {"filename": item["filename"], "content": files.get(item["filename"])}
            elif task.action_type == "RECREATE_TRACKING_SCRIPT_TAG":
                # Sprint 4 Step 3: looked up by `src`, not by the id
                # `create_script_tag` returned — an independent
                # re-confirmation that the tag is actually live, not a reuse
                # of the mutation's own response.
                state = await self._shopify_client.fetch_script_tag_state(src=item["src"])
            else:
                state = await self._shopify_client.fetch_product_state(product_gid=item["product_gid"])
            states.append(state)
            item_results.append(self._compare_item(task.action_type, item, state))

        passed = bool(item_results) and all(result[0] for result in item_results)
        before_value = item_results[0][1] if item_results else None
        after_value = item_results[0][2] if item_results else None
        # `result_data`/`after_state` below intentionally carry every
        # product's fetched state (not just item 0), wrapped in a dict since
        # `Verification.result_data`/`AuditLogEntry.after_state` are typed
        # `dict`, not `list` — so the stored verification/audit record
        # reflects every product actually checked, not only the first.
        state = {"items": states}

        verification = await self._verifications.create(
            execution_id=execution_id,
            task_id=task.id,
            store_id=task.store_id,
            status="PASSED" if passed else "FAILED",
            method=VERIFICATION_METHOD,
            result_data=state,
        )

        explanation = build_explanation(
            issue_title=issue.title if issue else task.action_type,
            issue_description=issue.description if issue else "",
            action_type=task.action_type,
            revenue_impact_estimate=float(issue.revenue_impact_estimate) if issue else 0.0,
            verification_passed=passed,
            verification_method=VERIFICATION_METHOD,
            before_value=before_value,
            after_value=after_value,
        )

        if passed:
            await self._cognitive_tasks.update_status(task.id, "SUCCESS")
            await self._cognitive_tasks.update_confidence_and_explanation(
                task.id,
                confidence_assessment=task.confidence_assessment,
                explanation=explanation.to_dict(),
            )
            if issue is not None:
                await self._issues.update_status(issue.id, "RESOLVED")
            await self._shifts.increment_resolved_count(task.shift_id, 1)
            await self._audit_logs.append(
                store_id=task.store_id,
                shift_id=task.shift_id,
                task_id=task.id,
                execution_id=execution_id,
                actor_type="AI_AGENT",
                actor_id=task.action_type,
                action="VERIFICATION_PASSED",
                rationale=explanation.narrative(),
                after_state=state,
            )
            logger.info(
                "verify_execution_passed",
                task_id=str(task.id),
                execution_id=str(execution_id),
                status="success",
            )
            return verification

        # --- Verification failed: roll back, mark failed, surface it -------
        await self._rollback_cognitive_task.execute(execution_id, reason="Verification failed")
        await self._cognitive_tasks.update_status(task.id, "FAILED")
        await self._cognitive_tasks.update_confidence_and_explanation(
            task.id,
            confidence_assessment=task.confidence_assessment,
            explanation=explanation.to_dict(),
        )
        if issue is not None:
            # Never silently drop a real problem — it resurfaces next shift.
            await self._issues.update_status(issue.id, "OPEN")
        await self._audit_logs.append(
            store_id=task.store_id,
            shift_id=task.shift_id,
            task_id=task.id,
            execution_id=execution_id,
            actor_type="AI_AGENT",
            actor_id=task.action_type,
            action="VERIFICATION_FAILED",
            rationale=explanation.narrative(),
            after_state=state,
        )
        logger.warning(
            "verify_execution_failed",
            task_id=str(task.id),
            execution_id=str(execution_id),
            status="error",
        )
        return verification

    @staticmethod
    def _compare_item(action_type: str, item: dict, state: dict) -> tuple[bool, str | None, str | None]:
        """Returns (passed, before_value, after_value) for one execution_plan
        item, for the explanation bundle. Comparison is deterministic and
        field-specific per action type — never a generic/fuzzy diff."""
        if action_type == "GENERATE_ALT_TEXT":
            expected_alt = item.get("new_alt_text")
            image_gid = item.get("image_gid")
            nodes = state.get("images", {}).get("nodes", [])
            match = next((n for n in nodes if n.get("id") == image_gid), None)
            actual_alt = match.get("altText") if match else None
            before_value = (item.get("before_state") or {}).get("alt_text")
            return actual_alt == expected_alt, before_value, expected_alt

        if action_type == "REWRITE_PRODUCT_DESCRIPTION":
            expected_html = item.get("new_description_html")
            actual_html = state.get("descriptionHtml")
            before_value = (item.get("before_state") or {}).get("description_html")
            return actual_html == expected_html, before_value, expected_html

        if action_type == "DEACTIVATE_DUPLICATE_DISCOUNT":
            # Shopify's own documented deactivation behavior sets `endsAt`
            # to now, so a successfully deactivated discount reads back as
            # status EXPIRED — there is no separate INACTIVE value in
            # DiscountStatus (ACTIVE/EXPIRED/SCHEDULED only, confirmed via
            # live schema docs).
            actual_status = state.get("status")
            before_value = (item.get("before_state") or {}).get("status")
            return actual_status == "EXPIRED", before_value, "EXPIRED"

        if action_type == "GENERATE_THEME_RESTORE_GUIDE":
            # Passes once the live file's content matches the baseline the
            # guide handed the merchant — i.e. once THEY have applied the
            # patch via the Theme Editor. Note: on the very first check
            # (immediately after approval, before the merchant has had a
            # chance to act), this will legitimately read as not-yet-passed
            # — see `execute_cognitive_task.py`'s Step 3 module comment and
            # CONFLICTS.md for how that's handled by the surrounding
            # lifecycle (rollback becomes a documented no-op, task ends in
            # FAILED meaning "not yet applied," and the issue reopens to
            # OPEN so the next Theme Guardian scan can re-offer the guide).
            expected_content = item.get("expected_content")
            actual_content = state.get("content")
            before_value = (item.get("before_state") or {}).get("content")
            return actual_content == expected_content, before_value, expected_content

        if action_type == "RECREATE_TRACKING_SCRIPT_TAG":
            expected_src = item.get("src")
            actual_src = state.get("src")
            before_value = "removed" if (item.get("before_state") or {}).get("existed") is False else None
            return actual_src == expected_src, before_value, expected_src

        # Unknown action type: conservatively treat as unverifiable/failed
        # rather than assuming success.
        return False, None, None
