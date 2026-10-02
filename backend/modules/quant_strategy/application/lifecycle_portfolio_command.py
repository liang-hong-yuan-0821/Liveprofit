"""Stage DB-owned protective quarantine; no commit or production API wiring."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_portfolio_selection import (
    PortfolioDrawdownCommandRequest,
)


@dataclass(frozen=True)
class StagedPortfolioDrawdownCommand:
    command_id: UUID
    replayed: bool
    results: tuple[dict, ...]
    execution_authorized: bool = False
    continuous_history_known: bool = False


def stage_portfolio_unbound_drawdown_command(session: Session, request: PortfolioDrawdownCommandRequest,
                                            *, actor_ref: str) -> StagedPortfolioDrawdownCommand:
    if not isinstance(request, PortfolioDrawdownCommandRequest):
        raise TypeError("original typed portfolio request required")
    if not isinstance(actor_ref, str) or not actor_ref.strip() or len(actor_ref) > 128:
        raise ValueError("actor_ref must be nonempty and up to 128 characters")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("组合命令必须在业务修改前进入，不能自动flush已有变化")
    with session.no_autoflush:
        if session.connection().connection.driver_connection.autocommit:
            raise LifecycleInvalidStateError("组合stage不能使用AUTOCOMMIT，调用者须保留提交/回滚权")
        row = session.execute(text("SELECT command_id,replayed FROM public.lc_record_portfolio_unbound_drawdown(:request,:actor)"),
                              {"request": request.canonical_request(), "actor": actor_ref}).one()
        results = tuple(session.scalars(text("SELECT source_snapshot FROM quant_execution_operation_sources "
                                            "WHERE business_command_id=:id AND source_type='UNBOUND_ORDER_RESULT' ORDER BY source_ordinal"),
                                       {"id": row.command_id}))
        return StagedPortfolioDrawdownCommand(row.command_id, row.replayed, results)
