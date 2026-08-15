"""Use case: Trigger Demo Incident — Sprint 4 Step 1, the Demo Incident
Generator's first wired scenario.

Deliberately re-corrupts a store's live data on cue so a live demo never has
to wait for something to organically go wrong (see
`SPRINT4_AI_WORKFORCE_VISION.md`, "Two pieces of shared infrastructure" /
"Three locked additions #3"). This is NOT part of the Observe -> Reason ->
... lifecycle — it runs in the opposite direction, creating the very
incidents the specialist agents are built to catch. Framework-agnostic in
the same sense the rest of this layer is: depends only on an
already-constructed `ShopifyGraphQLClient` and the `AuditLogRepository`
port, no FastAPI/Celery coupling.

Only Scenario 1 ("Midnight Pricing Disaster") is wired this step, per the
locked order of execution. `DEMO_SCENARIOS` (domain/demo_incidents.py) names
Scenarios 2 and 3 too, so `execute()` distinguishes three outcomes: unknown
id entirely (`UnknownDemoScenarioProblem`), a named-but-not-yet-wired
scenario (also `UnknownDemoScenarioProblem` — deliberately the same error
code, since from the caller's perspective both mean "you can't trigger this
yet"), and the one real, wired scenario.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from app.api.errors import DemoScenarioNoEligibleProductProblem, UnknownDemoScenarioProblem
from app.application.ports import AuditLogRepository, TrackingSnapshotRepository
from app.domain.demo_incidents import (
    CATALOG_SEO_COLLAPSE,
    DEMO_META_PIXEL_SCRIPT_TAG_SRC,
    DEMO_SCENARIOS,
    MIDNIGHT_PRICING_DISASTER,
    MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS,
    ROGUE_DEVELOPER_THEME_BREAK,
    WIRED_SCENARIO_IDS,
)
from app.domain.script_tag_inspection import identify_pattern
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="trigger_demo_incident")


@dataclass
class DemoIncidentResult:
    scenario_id: str
    created_discount_codes: list[str] = field(default_factory=list)
    notes: str | None = None
    """Scenario-specific caveats surfaced to the caller — e.g. Scenario 2's
    theme-file half not being auto-triggerable (see
    `domain/demo_incidents.py::ROGUE_DEVELOPER_THEME_BREAK`'s own
    description). None for scenarios with no caveats."""


class TriggerDemoIncident:
    def __init__(
        self,
        *,
        shopify_client: ShopifyGraphQLClient,
        audit_logs: AuditLogRepository,
        tracking_snapshots: TrackingSnapshotRepository | None = None,
    ) -> None:
        self._shopify_client = shopify_client
        self._audit_logs = audit_logs
        # Optional: only Scenario 2 needs this (to seed a baseline snapshot
        # so the very next inspection cycle can detect the removal it's
        # about to trigger, without waiting for a prior, separate shift to
        # have observed the tag first). None is safe for every other
        # scenario, mirroring `ProductQualityAgent`'s own optional
        # `budget_guard` pattern.
        self._tracking_snapshots = tracking_snapshots

    async def execute(self, *, scenario_id: str, store_id: uuid.UUID) -> DemoIncidentResult:
        if scenario_id not in DEMO_SCENARIOS:
            raise UnknownDemoScenarioProblem(f"No demo scenario registered for id {scenario_id!r}")
        if scenario_id not in WIRED_SCENARIO_IDS:
            raise UnknownDemoScenarioProblem(
                f"Demo scenario {scenario_id!r} is named in the roadmap but not wired up yet"
            )

        if scenario_id == MIDNIGHT_PRICING_DISASTER.id:
            return await self._trigger_midnight_pricing_disaster(store_id)
        if scenario_id == ROGUE_DEVELOPER_THEME_BREAK.id:
            return await self._trigger_rogue_developer_theme_break(store_id)
        return await self._trigger_catalog_seo_collapse(store_id)

    async def _trigger_midnight_pricing_disaster(self, store_id: uuid.UUID) -> DemoIncidentResult:
        starts_at = datetime.now(timezone.utc).isoformat()
        created_codes: list[str] = []

        for spec in MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS:
            # A short random suffix guarantees uniqueness across repeated
            # triggers within the same store — Shopify discount codes must
            # be unique, and a demo may be run more than once.
            unique_code = f"{spec.code_prefix}-{uuid.uuid4().hex[:6].upper()}"
            await self._shopify_client.create_basic_discount_code(
                title=spec.title,
                code=unique_code,
                percentage=spec.percentage,
                starts_at=starts_at,
                combines_with=spec.combines_with,
            )
            created_codes.append(unique_code)

        await self._audit_logs.append(
            store_id=store_id,
            actor_type="DEMO_GENERATOR",
            actor_id=MIDNIGHT_PRICING_DISASTER.id,
            action="DEMO_INCIDENT_TRIGGERED",
            rationale=(
                f"Demo Incident Generator ({MIDNIGHT_PRICING_DISASTER.name}) created "
                f"{len(created_codes)} overlapping, mutually-stackable 50%-off discount "
                "codes on cue: " + ", ".join(created_codes)
            ),
            after_state={"created_discount_codes": created_codes},
        )
        logger.info(
            "demo_incident_triggered",
            store_id=str(store_id),
            scenario_id=MIDNIGHT_PRICING_DISASTER.id,
            created_discount_codes=created_codes,
            status="success",
        )
        return DemoIncidentResult(
            scenario_id=MIDNIGHT_PRICING_DISASTER.id, created_discount_codes=created_codes
        )

    async def _trigger_rogue_developer_theme_break(self, store_id: uuid.UUID) -> DemoIncidentResult:
        """Only the tracking half of Scenario 2 actually executes — see
        `ROGUE_DEVELOPER_THEME_BREAK`'s own description for why the theme
        half cannot be auto-triggered at all (no Shopify write path to
        theme files exists in this codebase, for injection or repair
        alike).

        Sequence: create a Meta-Pixel-pattern script tag, immediately seed
        a `tracking_snapshots` baseline row for it (so the very next
        `tasks.inspect_tracking_scripts` run has something to diff
        against), then delete the live script tag — reproducing exactly
        the "a previously-known tracking script just disappeared" fact
        Tracking Specialist's detector is built to catch, without needing
        a separate shift to have observed the tag first.
        """
        created = await self._shopify_client.create_script_tag(
            src=DEMO_META_PIXEL_SCRIPT_TAG_SRC, display_scope="ONLINE_STORE"
        )
        pattern_name = identify_pattern(DEMO_META_PIXEL_SCRIPT_TAG_SRC)

        if self._tracking_snapshots is not None:
            await self._tracking_snapshots.create(
                store_id=store_id,
                src=DEMO_META_PIXEL_SCRIPT_TAG_SRC,
                display_scope="ONLINE_STORE",
                pattern_name=pattern_name,
            )

        await self._shopify_client.delete_script_tag(script_tag_id=created.get("id", ""))

        notes = (
            "Only the tracking half of this scenario was triggered (a Meta Pixel-pattern "
            "script tag was created, snapshotted, then deleted). The theme half (removing "
            "the Buy Button block) requires manually editing "
            "sections/main-product.liquid in the Shopify Theme Editor first — NightShift "
            "has no Shopify-granted write exemption for theme files."
        )
        await self._audit_logs.append(
            store_id=store_id,
            actor_type="DEMO_GENERATOR",
            actor_id=ROGUE_DEVELOPER_THEME_BREAK.id,
            action="DEMO_INCIDENT_TRIGGERED",
            rationale=(
                f"Demo Incident Generator ({ROGUE_DEVELOPER_THEME_BREAK.name}) deleted a "
                f"Meta Pixel-pattern script tag (src: {DEMO_META_PIXEL_SCRIPT_TAG_SRC}) on cue. {notes}"
            ),
            after_state={"deleted_script_tag_src": DEMO_META_PIXEL_SCRIPT_TAG_SRC},
        )
        logger.info(
            "demo_incident_triggered",
            store_id=str(store_id),
            scenario_id=ROGUE_DEVELOPER_THEME_BREAK.id,
            deleted_script_tag_src=DEMO_META_PIXEL_SCRIPT_TAG_SRC,
            status="success",
        )
        return DemoIncidentResult(scenario_id=ROGUE_DEVELOPER_THEME_BREAK.id, notes=notes)

    async def _trigger_catalog_seo_collapse(self, store_id: uuid.UUID) -> DemoIncidentResult:
        """Sprint 5 Phase 4: strips the description and (if present) the
        first image's ALT text from the catalog's first active product —
        reusing `update_product_description`/`update_product_image_alt_text`
        (Sprint 3's real auto-fix mutations, called here in reverse) so the
        very next `tasks.inspect_catalog` run genuinely re-detects both
        `missing_alt_text` and `thin_description` via its own unmodified
        `domain/inspection.py` checks — no new detection logic, no
        fabricated finding.

        `fetch_catalog_for_inspection(max_products=1)` still fetches one
        full page (up to 250 products) server-side and returns only the
        first — reusing the existing client method rather than adding a new
        one-product GraphQL query, since Step 1/2's own precedent is to
        reuse inspection-side fetches wherever the shape already fits.
        """
        products = await self._shopify_client.fetch_catalog_for_inspection(max_products=1)
        if not products:
            raise DemoScenarioNoEligibleProductProblem(
                "This store has no active products to corrupt for the Catalog SEO Collapse "
                "scenario — add at least one active product first."
            )

        product = products[0]
        product_gid = product.get("id", "")
        title = product.get("title") or "(untitled product)"

        await self._shopify_client.update_product_description(
            product_gid=product_gid, description_html=""
        )

        stripped_image_id: str | None = None
        for media_node in product.get("media", {}).get("nodes", []):
            if media_node.get("mediaContentType") == "IMAGE":
                stripped_image_id = media_node.get("id")
                break
        if stripped_image_id:
            await self._shopify_client.update_product_image_alt_text(
                product_gid=product_gid, image_gid=stripped_image_id, alt_text=""
            )

        notes = (
            f"Stripped the description{' and image ALT text' if stripped_image_id else ''} "
            f"from '{title}'."
            + ("" if stripped_image_id else " This product has no image, so only its description was stripped.")
        )
        await self._audit_logs.append(
            store_id=store_id,
            actor_type="DEMO_GENERATOR",
            actor_id=CATALOG_SEO_COLLAPSE.id,
            action="DEMO_INCIDENT_TRIGGERED",
            rationale=f"Demo Incident Generator ({CATALOG_SEO_COLLAPSE.name}): {notes}",
            after_state={"product_id": product_gid, "stripped_image_id": stripped_image_id},
        )
        logger.info(
            "demo_incident_triggered",
            store_id=str(store_id),
            scenario_id=CATALOG_SEO_COLLAPSE.id,
            product_id=product_gid,
            stripped_image_id=stripped_image_id,
            status="success",
        )
        return DemoIncidentResult(scenario_id=CATALOG_SEO_COLLAPSE.id, notes=notes)
