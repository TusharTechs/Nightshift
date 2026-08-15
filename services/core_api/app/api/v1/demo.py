"""Demo Incident Generator API — Sprint 4 Step 1 shared infrastructure.

`POST /api/v1/demo/incidents/{scenario_id}` deliberately re-corrupts the
authenticated store's live data on cue (Scenario 1: creates two overlapping,
mutually-stackable 50%-off discount codes), so a live demo never has to wait
for something to organically go wrong (see
`SPRINT4_AI_WORKFORCE_VISION.md`).

Gated by `Settings.demo_mode_enabled` (default False): this is exactly the
kind of capability that must never be reachable in a real merchant's
production deployment by default — it lets an authenticated caller corrupt
their own store's pricing on purpose. Returns 404 (via
`DemoModeDisabledProblem`) rather than 403 when disabled, so the endpoint's
existence isn't even distinguishable from a route that doesn't exist.

Sprint 5 Phase 4 (Demo Incident Control Panel / "Chaos Panel"): after a
scenario actually triggers, this route also dispatches
`tasks.inspect_catalog` for the same store — one click both corrupts data
AND runs the background shift that detects/resolves it, per the roadmap's
own "must immediately trigger the incident, run a background shift, and
update the UI live without manual database manipulation." The dispatched
Celery task id is returned so the caller can see one was actually enqueued,
never silently skipped.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_current_store_id, get_task_dispatcher, get_trigger_demo_incident_use_case
from app.api.errors import DemoModeDisabledProblem
from app.application.dtos import DemoIncidentResponse
from app.application.ports import TaskDispatcher
from app.application.use_cases.trigger_demo_incident import TriggerDemoIncident
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


@router.post("/incidents/{scenario_id}", response_model=DemoIncidentResponse)
async def trigger_demo_incident(
    scenario_id: str,
    store_id: uuid.UUID = Depends(get_current_store_id),
    settings: Settings = Depends(get_settings),
    use_case: TriggerDemoIncident = Depends(get_trigger_demo_incident_use_case),
    task_dispatcher: TaskDispatcher = Depends(get_task_dispatcher),
) -> DemoIncidentResponse:
    if not settings.demo_mode_enabled:
        raise DemoModeDisabledProblem(
            "Demo mode is disabled on this deployment. Set DEMO_MODE_ENABLED=true to enable "
            "the Demo Incident Generator — dev/demo environments only, never production."
        )

    result = await use_case.execute(scenario_id=scenario_id, store_id=store_id)
    shift_dispatch_task_id = task_dispatcher.dispatch_inspect_catalog(store_id)

    return DemoIncidentResponse(
        scenario_id=result.scenario_id,
        created_discount_codes=result.created_discount_codes,
        timestamp=datetime.now(timezone.utc),
        notes=result.notes,
        shift_dispatch_task_id=shift_dispatch_task_id,
    )
