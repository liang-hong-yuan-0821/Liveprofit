"""Stage one DB-owned bound order-status command; caller owns its transaction."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_bound_replay import (
    BoundOrderStatusReplay,
    _read_bound,
)


@dataclass(frozen=True)
class StagedBoundOrderStatusCommand:
    command_id: uuid.UUID
    replayed: bool
    content: BoundOrderStatusReplay


def stage_bound_order_status_command(session: Session, portfolio_id: uuid.UUID,
                                     request: OrderStatusCommandRequest, *, actor_ref: str):
    if not isinstance(portfolio_id, uuid.UUID) or not isinstance(request, OrderStatusCommandRequest):
        raise TypeError("portfolio UUID and original typed request required")
    if not isinstance(actor_ref,str) or not actor_ref.strip() or len(actor_ref)>128:
        raise ValueError("actor_ref must be a nonempty string up to 128 characters")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("命令stage须在业务修改前进入，不能自动flush已有变更")
    with session.no_autoflush:
        row = session.execute(text("SELECT command_id,replayed FROM public.lc_record_bound_order_status(:portfolio,:request,:actor)"),
                              {"portfolio":portfolio_id,"request":request.canonical_request(),"actor":actor_ref}).one()
        content = _read_bound(session,portfolio_id,request)
        if content is None or content.command_id != row.command_id:
            raise LifecycleInvalidStateError("命令暂存结果与历史核验不一致")
        return StagedBoundOrderStatusCommand(row.command_id,row.replayed,content)
