"""Validate owner-only lifecycle inputs before joint entry/add consumption."""
from sqlalchemy import or_, select

from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionDailyFact, PositionLifecycleState
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


def joint_lifecycle_inputs(session, batch, tasks):
    """Caller holds the portfolio lock; return validated deferred days by version.

    A retried owner scan validates the same daily input hash in process_day;
    its current successful attempt must also contain matching holding evidence.
    """
    rows = session.execute(select(PositionLifecycleState, PositionDailyFact).outerjoin(
        PositionDailyFact, (PositionDailyFact.lifecycle_id == PositionLifecycleState.id)
        & (PositionDailyFact.trade_date == batch.valuation_date),
    ).where(PositionLifecycleState.portfolio_id == batch.portfolio_id, or_(
        PositionLifecycleState.closed_at.is_(None),
        PositionDailyFact.planning_result["stage"].astext == "AWAITING_BATCH",
    )).order_by(PositionLifecycleState.symbol, PositionLifecycleState.id)
        .execution_options(populate_existing=True)).all()
    deferred = {}
    for life, daily in rows:
        task = tasks.get(life.strategy_version_id)
        if task is None or daily is None:
            raise ValueError("complete owner lifecycle day required")
        snapshot = task.request_params["execution_snapshot"]
        policy = snapshot["strategy"].get("lifecycle_policy") or {}
        fact = daily.input_payload
        if (life.market != "CN" or daily.price_basis != "raw"
                or fact.get("asset_scope") != batch.asset_scope
                or snapshot.get("new_risk_mode") != "FAMILY_BATCH"
                or fact.get("source_task_id") != str(task.id)
                or str(policy.get("id")) != str(life.lifecycle_policy_version_id)
                or policy.get("content_hash") != daily.rule_version
                or fact.get("execution_policy") != snapshot.get("execution_policy")):
            raise ValueError("owner lifecycle provenance mismatch")
        signals = list(session.scalars(select(QuantExecutionSignal).where(
            QuantExecutionSignal.task_id == task.id, QuantExecutionSignal.attempt_no == task.attempt_no,
            QuantExecutionSignal.strategy_version_id == life.strategy_version_id,
            QuantExecutionSignal.ts_code == life.symbol, QuantExecutionSignal.signal_kind.in_(("HOLDING", "BUY")),
        )))
        if (len(signals) != 1 or signals[0].error_code
                or signals[0].signal_trade_date != batch.valuation_date
                or signals[0].signal_price_basis != "qfq"
                or signals[0].execution_price_basis != "raw"):
            raise ValueError("current owner attempt holding evidence required")
        market = {k: v for k, v in (signals[0].execution_market or {}).items()
                  if k not in ("batch_context", "lifecycle_seed")}
        if market != fact.get("execution_market"):
            raise ValueError("owner lifecycle market evidence mismatch")
        saved = daily.planning_result or {}
        if saved.get("stage") == "BATCH_COMPLETED":
            raise ValueError("owner lifecycle day already consumed")
        if saved.get("stage") == "AWAITING_BATCH":
            deferred.setdefault(life.strategy_version_id, []).append((life, daily))
    return deferred
