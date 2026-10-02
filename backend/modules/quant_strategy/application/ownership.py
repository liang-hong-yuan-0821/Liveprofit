"""Read persisted ownership while the caller holds the portfolio lock.

None means unknown or conflicting provenance, never permission to adopt.
Existing lifecycle versions remain owners even after publication is archived.
"""
import uuid

from sqlalchemy import select

from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionLifecycleState, SuggestedOrder
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


def _version(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def collect_owner_versions(session, portfolio_id):
    claims: dict[str, set[str | None]] = {}
    lifecycles = list(session.scalars(select(PositionLifecycleState).where(
        PositionLifecycleState.portfolio_id == portfolio_id,
        PositionLifecycleState.market == "CN", PositionLifecycleState.closed_at.is_(None),
    ).execution_options(populate_existing=True)))
    lifecycle_versions = {row.id: (row.symbol, _version(row.strategy_version_id)) for row in lifecycles}
    for row in lifecycles:
        claims.setdefault(row.symbol, set()).add(_version(row.strategy_version_id))
    for position in session.scalars(select(PortfolioPosition).where(
        PortfolioPosition.portfolio_id == portfolio_id, PortfolioPosition.market == "CN",
        PortfolioPosition.quantity > 0,
    )):
        if position.symbol not in claims:
            claims[position.symbol] = {None}
    rows = session.execute(select(SuggestedOrder, QuantExecutionSignal, AnalysisTask).outerjoin(
        QuantExecutionSignal, SuggestedOrder.source_signal_id == QuantExecutionSignal.id,
    ).outerjoin(AnalysisTask, QuantExecutionSignal.task_id == AnalysisTask.id).where(
        SuggestedOrder.portfolio_id == portfolio_id, SuggestedOrder.market == "CN",
        SuggestedOrder.side == "BUY", SuggestedOrder.quantity > SuggestedOrder.filled_quantity,
        SuggestedOrder.status.in_(("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")),
    ).execution_options(populate_existing=True))
    for order, signal, task in rows:
        versions = set()
        if order.lifecycle_id is not None:
            bound = lifecycle_versions.get(order.lifecycle_id)
            versions.add(bound[1] if bound is not None and bound[0] == order.symbol else None)
        if signal is not None:
            if signal.ts_code != order.symbol:
                versions.add(None)
            source = _version(signal.strategy_version_id)
            params = _mapping(task.request_params) if task is not None else {}
            snapshot = _mapping(_mapping(params.get("execution_snapshot")).get("strategy"))
            frozen = _version(snapshot.get("version_id"))
            if source is not None:
                versions.add(source)
            if frozen is not None:
                versions.add(frozen)
            elif snapshot.get("version_id") is not None:
                versions.add(None)
        claims.setdefault(order.symbol, set()).update(versions or {None})
    return {symbol: next(iter(versions)) if len(versions) == 1 else None
            for symbol, versions in claims.items()}


def owned_script_output(output, *, owner_version_id, scanner_version_id):
    """A different scan's script cannot redefine the frozen owner's target."""
    owner = _version(owner_version_id)
    return output if owner is not None and owner == _version(scanner_version_id) else None
