"""Atomic joint consumer for ordinary entries and frozen-owner additions."""
from decimal import Decimal
import hashlib
import json
from uuid import UUID
from sqlalchemy import func, select

from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember, AllocationExecution, AllocationOutcome
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal, QuantExecutionSignalRepository
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from .family_batch import FamilyBatchService, _json
from .lifecycle_batch import _intent, LifecycleBatchService
from .joint_lifecycle import joint_lifecycle_inputs
from .planning_account import lock_portfolio, planning_account
from .position_planner import PositionPlanner
from .order_materialization import materialize_signal_orders
from .target_trial_binding import TargetTrialBindingService
from .certified_instrument_rules import live_authorizations


class EntryBatchService:
    def __init__(self, session):
        self.session = session

    def materialize(self, *, batch_id: UUID, scan_attempts: dict[UUID, tuple[UUID, int]], closes: dict,
                    industry_map: dict, industry_bucket_available: bool):
        """Consume entries and owned deferred days once, under one transaction.

        Only completed source task attempts are accepted. Source provenance and
        complete family target coverage are checked before any order mutation.
        Historical receipt replay never creates another order after cancellation.
        """
        batch = self.session.get(AllocationBatch, batch_id)
        if batch is None:
            raise ValueError("allocation batch missing")
        if batch.asset_scope != "CN_STOCK":
            raise ValueError("entry batch currently requires verified CN stock execution rules")
        account = lock_portfolio(self.session, batch.portfolio_id)
        members = list(self.session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)))
        if set(scan_attempts) != {m.strategy_version_id for m in members}:
            raise ValueError("complete frozen scan manifest required, including cash-only families")
        request = _json(dict(scan_attempts=[(str(v), str(t), n) for v, (t, n) in sorted(scan_attempts.items(), key=lambda item: str(item[0]))],
                             closes=closes, industry_map=industry_map,
                             industry_bucket_available=industry_bucket_available))
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        previous = self.session.get(AllocationExecution, batch_id)
        if previous:
            if previous.input_hash != digest:
                raise ValueError("entry receipt input conflict")
            return previous
        from .portfolio_drawdown_actions import PortfolioDrawdownActions
        PortfolioDrawdownActions(self.session).pause_for_planning(
            batch.portfolio_id, valuation_date=batch.valuation_date)
        outcome = self.session.get(AllocationOutcome, batch_id)
        if outcome is not None and outcome.status == "BLOCKED":
            raise ValueError("allocation batch is terminally blocked")
        binding_service = TargetTrialBindingService(self.session)
        for member in members:
            if binding_service.audit_target_intent(
                intent=_intent(member.intent), asset_scope=batch.asset_scope,
            ) is not None:
                raise ValueError("portfolio trial requires dedicated target order runtime")
        tasks = {}
        for version_id, (task_id, attempt_no) in sorted(scan_attempts.items(), key=lambda item: str(item[1][0])):
            task = self.session.scalar(select(AnalysisTask).where(AnalysisTask.id == task_id)
                .with_for_update(read=True).execution_options(populate_existing=True))
            snapshot = (task.request_params or {}).get("execution_snapshot", {}) if task else {}
            if (type(attempt_no) is not int or attempt_no <= 0 or task is None
                    or task.status != "SUCCEEDED" or task.attempt_no != attempt_no
                    or task.effective_trade_date != batch.valuation_date
                    or str(snapshot.get("portfolio", {}).get("id")) != str(batch.portfolio_id)
                    or str(snapshot.get("strategy", {}).get("version_id")) != str(version_id)):
                raise ValueError("scan manifest requires completed matching current attempts")
            tasks[version_id] = task
        deferred = joint_lifecycle_inputs(self.session, batch, tasks)
        intents = tuple(_intent(m.intent) for m in members)
        legs = {(i.strategy_version_id, leg.ts_code): leg for i in intents for leg in i.legs}
        occupied = {e["symbol"] for e in batch.result["exposures"]}
        required = {key for key in legs if key[1] not in occupied}
        signals = []
        for version_id, task in tasks.items():
            symbols = [symbol for version, symbol in required if version == version_id]
            if not symbols:
                continue
            signals.extend(self.session.scalars(select(QuantExecutionSignal).where(
                QuantExecutionSignal.task_id == task.id,
                QuantExecutionSignal.attempt_no == task.attempt_no,
                QuantExecutionSignal.strategy_version_id == version_id,
                QuantExecutionSignal.signal_kind == "BUY",
                QuantExecutionSignal.ts_code.in_(symbols),
            ).execution_options(populate_existing=True)))
        signals.sort(key=lambda s: s.id)
        signal_ids = tuple(s.id for s in signals)
        actual = {(s.strategy_version_id, s.ts_code) for s in signals}
        if len(actual) != len(signals) or actual != required:
            raise ValueError("complete unique entry signal coverage required")
        snapshots, sources = {}, {}
        policies = {str(version): task.request_params["execution_snapshot"].get("execution_policy")
                    for version, task in tasks.items()}
        for signal in signals:
            task = tasks[signal.strategy_version_id]
            snapshot = (task.request_params or {}).get("execution_snapshot", {}) if task else {}
            strategy = snapshot.get("strategy", {})
            if (task is None or task.status != "SUCCEEDED" or task.attempt_no != signal.attempt_no
                    or str(snapshot.get("portfolio", {}).get("id")) != str(batch.portfolio_id)
                    or str(strategy.get("version_id")) != str(signal.strategy_version_id)
                    or signal.signal_kind != "BUY" or signal.action != "BUY" or signal.error_code
                    or signal.signal_trade_date != batch.valuation_date
                    or signal.signal_price_basis != "qfq" or signal.execution_price_basis != "raw"):
                raise ValueError("entry signal provenance or completed attempt mismatch")
            market = signal.execution_market or {}
            if (str(market.get("trade_date"))[:10] != batch.valuation_date.isoformat()
                    or signal.ts_code not in closes
                    or Decimal(str(market.get("raw_close"))) != Decimal(str(closes[signal.ts_code]))):
                raise ValueError("entry signal valuation mismatch")
            key = str(signal.strategy_version_id)
            if key in snapshots and snapshots[key] != strategy:
                raise ValueError("one frozen strategy snapshot per family required")
            if key in sources and sources[key] != (signal.task_id, signal.attempt_no):
                raise ValueError("one completed task attempt per family required")
            sources[key] = (signal.task_id, signal.attempt_no)
            snapshots[key] = strategy
            policies[key] = snapshot.get("execution_policy")
        if len({json.dumps(p, sort_keys=True) for p in policies.values()}) > 1:
            raise ValueError("one common execution policy required")
        if signals and self.session.scalar(select(SuggestedOrder.id).where(
                SuggestedOrder.source_signal_id.in_(signal_ids)).limit(1)):
            raise ValueError("source signal already materialized outside this batch")
        fresh = FamilyBatchService(self.session).project(
            portfolio_id=batch.portfolio_id, request_key=f"entries:{batch_id}", asset_scope=batch.asset_scope,
            expected_version_ids=tuple(m.strategy_version_id for m in members), intents=intents,
            valuation_date=batch.valuation_date, closes=closes, allow_replay=False,
            protective_targets={s: Decimal(v) for s, v in batch.result["protective_targets"].items()},
        )
        repo = QuantExecutionSignalRepository(self.session)
        accepted = []
        targets = {t["symbol"]: t for t in (fresh.result["allocation"] or {}).get("targets", [])}
        for signal in signals:
            target = targets.get(signal.ts_code)
            code = None
            if batch.status != "PROJECTED" or fresh.status != "PROJECTED":
                code = "BUY_REJECTED_ADMISSION"
            elif not target or target["strategy_version_id"] != str(signal.strategy_version_id):
                code = "BUY_REJECTED_FAMILY_OWNER"
            elif Decimal(target["max_add_notional"]) <= 0:
                code = "BUY_REJECTED_FAMILY_BUDGET"
            if code:
                repo.update_order_fields(signal.id, {"order_status": code})
                continue
            leg = legs[(signal.strategy_version_id, signal.ts_code)]
            signal.max_notional = Decimal(target["max_add_notional"])
            signal.entry_lower, signal.entry_upper = leg.entry_lower, leg.entry_upper
            accepted.append(signal)
        evidence = {m.strategy_version_id: self.session.get(StrategyAdmissionEvent, m.admission_event_id)
                    for m in self.session.scalars(select(AllocationMember).where(AllocationMember.batch_id == fresh.id))
                    if m.admission_event_id}
        from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
        strategy_ids = {v: str(self.session.get(QuantStrategyVersion, v).strategy_id) for v in tasks}
        def family_rank(version):
            event = evidence.get(version)
            return (-(event.net_expectancy_lower_bound or Decimal(0)) if event else Decimal(0), strategy_ids[version])
        family_order = sorted(tasks, key=family_rank)
        rule_authorizations = live_authorizations(
            self.session, symbols={signal.ts_code for signal in accepted},
            decision_date=batch.valuation_date)
        orders, lifecycle_results, processing_order = [], [], []
        for version in family_order:
            # Across families use shared evidence rank. Within each family the
            # frozen owner's add target precedes new positions, then score order.
            for life, daily in deferred.get(version, []):
                order = LifecycleBatchService(self.session).materialize(
                    daily_id=daily.id, batch_id=batch_id,
                    buy_context={"valuation_date": batch.valuation_date, "asset_scope": batch.asset_scope,
                                 "closes": closes, "industry_map": industry_map,
                                 "industry_bucket_available": industry_bucket_available},
                )
                lifecycle_results.append({"daily_id": str(daily.id), "strategy_version_id": str(version),
                    "symbol": life.symbol, "order_id": str(order.id) if order else None,
                    "planning_result": daily.planning_result})
                processing_order.append({"kind": "LIFECYCLE", "id": str(daily.id), "version_id": str(version)})
            selected = sorted((s for s in accepted if s.strategy_version_id == version),
                              key=lambda s: (-(s.score or Decimal(0)), s.ts_code, s.id))
            if not selected:
                continue
            current = planning_account(self.session, account)

            class SelectedSignals:
                def list_actionable(self, task_id, attempt_no):
                    return selected
                def update_order_fields(self, signal_id, values):
                    repo.update_order_fields(signal_id, values)

            PositionPlanner(SelectedSignals()).plan(
                task_id=selected[0].task_id, attempt_no=selected[0].attempt_no,
                **current, closes=closes, industry_map=industry_map,
                industry_bucket_available=industry_bucket_available, valuation_date=batch.valuation_date,
                execution_policy_snapshot=policies[str(version)],
                lifecycle_managed_symbols={p["symbol"] for p in current["positions"]},
                instrument_rules={symbol: authorization.rule
                                  for symbol, authorization in rule_authorizations.items()},
            )
            orders.extend(materialize_signal_orders(self.session, portfolio_id=batch.portfolio_id,
                rows=[s for s in selected if s.order_status == "ELIGIBLE"],
                strategy_snapshots=snapshots, industry_map=industry_map,
                instrument_rules={symbol: authorization.rule
                                  for symbol, authorization in rule_authorizations.items()},
                rule_authorizations=rule_authorizations))
            self.session.flush()
            processing_order.extend({"kind": "ENTRY", "id": str(s.id), "version_id": str(version)} for s in selected)
        order_ids = {o.source_signal_id: str(o.id) for o in orders}
        receipt = AllocationExecution(batch_id=batch_id, refreshed_batch_id=fresh.id, input_hash=digest,
            executed_at=self.session.scalar(select(func.clock_timestamp())), result=_json({
                "signals": [{"signal_id": s.id, "strategy_version_id": s.strategy_version_id,
                             "symbol": s.ts_code, "order_status": s.order_status,
                             "order_id": order_ids.get(s.id), "shares": s.shares,
                             "order_cost_price": s.order_cost_price} for s in signals],
                "blocks": fresh.result["blocks"], "scan_attempts": request["scan_attempts"],
                "lifecycle": lifecycle_results, "processing_order": processing_order,
            }))
        self.session.add(receipt)
        self.session.flush()
        return receipt
