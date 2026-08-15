"""Ask NightShift API — Sprint 4 Step 4 conversational surface.

`POST /api/v1/ask` — merchant asks a plain-English question ("why did
revenue increase yesterday?"), gets back an answer grounded in this store's
own recently-persisted shift reports (see
`application/use_cases/ask_nightshift.py`). Never mutates anything — a pure
read/reason endpoint, so it carries no risk-level/approval gating at all,
unlike every Sprint 3 action-taking endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_ask_nightshift_use_case, get_current_store_id
from app.application.dtos import AskNightShiftRequest, AskNightShiftResponse
from app.application.use_cases.ask_nightshift import AskNightShift

router = APIRouter(prefix="/api/v1", tags=["ask-nightshift"])


@router.post("/ask", response_model=AskNightShiftResponse)
async def ask_nightshift(
    body: AskNightShiftRequest,
    store_id: uuid.UUID = Depends(get_current_store_id),
    use_case: AskNightShift = Depends(get_ask_nightshift_use_case),
) -> AskNightShiftResponse:
    result = await use_case.ask(store_id, body.question)
    return AskNightShiftResponse(
        answer=result.answer,
        grounded_in_shift_ids=[uuid.UUID(sid) for sid in result.grounded_in_shift_ids],
        used_llm=result.used_llm,
        timestamp=datetime.now(timezone.utc),
    )
