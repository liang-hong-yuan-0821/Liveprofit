"""宏观信息路由（§2.6.1：仅 APPROVED，cursor 分页，空列表为正常空态）。"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.macro_information import MacroInformationData, MacroInformationItemDTO
from backend.api.schemas.problem import ProblemError
from backend.modules.event_study.infrastructure.readers import MacroInformationReader

router = APIRouter(prefix="/api/v1", tags=["macro-information"])


def _encode_cursor(occurred_at: datetime | None, item_id: uuid.UUID) -> str:
    payload = json.dumps({"o": occurred_at.isoformat() if occurred_at else None, "id": str(item_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(raw: str) -> tuple[datetime | None, uuid.UUID]:
    padded = raw + "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    occurred = datetime.fromisoformat(payload["o"]) if payload["o"] else None
    return occurred, uuid.UUID(payload["id"])


@router.get("/macro-information", response_model=Envelope[MacroInformationData])
async def list_macro_information(
    request: Request,
    limit: int = Query(ge=1, le=100),
    cursor: str | None = Query(default=None),
    market: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    trace_id: str = Depends(ensure_trace_context),
):
    try:
        decoded = _decode_cursor(cursor) if cursor else None
    except (ValueError, TypeError, KeyError):
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None

    container = request.app.state.container
    reader = MacroInformationReader(container.sync_session_factory)
    analysis_services = request.app.state.analysis_services

    def _do():
        return reader.list_approved(limit=limit + 1, before=decoded, market=market, topic=topic)

    rows = await analysis_services.run(_do)
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_cursor(last.occurred_at, last.id)

    data = MacroInformationData(
        items=[
            MacroInformationItemDTO(
                id=str(item.id),
                event_id=item.event_id,
                title=item.title,
                occurred_at=item.occurred_at,
                market_tags=item.market_tags or [],
                macro_topic=item.macro_topic,
                summary=item.summary,
                source=item.source,
                related_assets=item.related_assets,
                research_status=item.research_status,
            )
            for item in items
        ]
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=data, meta=meta).model_dump()
