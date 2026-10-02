"""Stage the narrowly supported DB-owned unbound status command.

The caller owns commit/rollback. This is not wired into production order APIs;
0052 revokes PUBLIC function execution pending separate deployment/ACL checks.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_command_replay import (
    OrderStatusCommandReplay,
    _verify_unbound_replay,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
    QuantExecutionOperationChange,
    QuantExecutionOperationSource,
)


@dataclass(frozen=True)
class StagedUnboundOrderStatusCommand:
    command_id: uuid.UUID
    replayed: bool
    content: OrderStatusCommandReplay


def stage_unbound_order_status_command(session: Session, portfolio_id: uuid.UUID,
                                       request: OrderStatusCommandRequest, *, actor_ref: str) -> StagedUnboundOrderStatusCommand:
    if not isinstance(portfolio_id, uuid.UUID) or not isinstance(request, OrderStatusCommandRequest):
        raise TypeError("portfolio UUID and original typed status request are required")
    if not isinstance(actor_ref, str) or not actor_ref.strip() or len(actor_ref) > 128:
        raise ValueError("actor_ref must be a nonempty string up to 128 characters")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("命令stage须在业务修改前进入，不能自动flush已有变更")
    with session.no_autoflush:
        row = session.execute(text(
            "SELECT command_id,replayed FROM public.lc_record_unbound_order_status(:portfolio,:request,:actor)"),
            {"portfolio": portfolio_id, "request": request.canonical_request(), "actor": actor_ref}).one()
        commands = LifecycleBusinessCommand.__table__
        head = session.execute(select(commands).where(commands.c.id == row.command_id)).mappings().one()
        sources, changes = [], []
        for model, destination, ordering in (
            (QuantExecutionOperationSource, sources, "source_ordinal"),
            (QuantExecutionOperationChange, changes, "change_seq"),
        ):
            table = model.__table__
            destination.extend(dict(item) for item in session.execute(select(table).where(
                table.c.business_command_id == row.command_id).order_by(table.c[ordering])).mappings())
        content = _verify_unbound_replay(head, request, sources, changes)
        return StagedUnboundOrderStatusCommand(row.command_id, row.replayed, content)
