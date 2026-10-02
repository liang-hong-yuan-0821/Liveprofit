# test-catalog-begin
# {
#   "purpose": "量化策略 / joint_orders：Joint add/entry transactions use conftest's isolated PG database only.",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "并发",
#     "执行",
#     "策略族",
#     "事务回滚",
#     "交易信号",
#     "joint_orders",
#     "batch",
#     "concurrent",
#     "execution",
#     "family",
#     "rollback",
#     "signal"
#   ],
#   "covers": [
#     "backend/modules/analysis/application/contracts.py",
#     "backend/modules/analysis/application/reporting.py",
#     "backend/modules/analysis/application/task_lifecycle.py",
#     "backend/modules/analysis/domain/enums.py",
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/analysis/infrastructure/repositories.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/batch_completion.py",
#     "backend/modules/quant_strategy/application/execution.py",
#     "backend/modules/quant_strategy/application/family_batch.py",
#     "backend/modules/quant_strategy/application/lifecycle_batch.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/application/scan_manifest.py",
#     "backend/modules/quant_strategy/application/strategy_admission.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py",
#     "backend/modules/quant_strategy/infrastructure/admission_models.py",
#     "backend/modules/quant_strategy/infrastructure/allocation_models.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_certificates.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py",
#     "backend/workers/analysis_executor.py",
#     "db/instrument/dao/ingest_state.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Joint add/entry transactions use conftest's isolated PG database only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import base64
import json
from decimal import Decimal as D
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.backend.quant_strategy.support.lifecycle_service import _seed_buy_lifecycles
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.batch_completion import BatchCompletionService
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService, CN_TIME
from backend.modules.quant_strategy.application.lifecycle_batch import LifecycleBatchService
from backend.modules.quant_strategy.application.position_lifecycle_manager import PositionLifecycleManager
from backend.modules.quant_strategy.application.scan_manifest import ScanManifestService
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationExecution, AllocationOutcome
from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionDailyFact, PositionLifecycleState, SuggestedOrder
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal

DAY = datetime.now(CN_TIME).date()


@pytest.fixture(autouse=True)
def _signed_current_rule(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import InstrumentRuleCertificateRepository
    from backend.modules.quant_strategy.infrastructure.instrument_rule_models import InstrumentRuleCertificate
    from tests.backend.quant_strategy.support.instrument_rule_certificates import _signed, KEY

    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    with env["session_factory"]() as session:
        for symbol in ("000001.SZ", "000002.SZ"):
            if session.scalar(select(InstrumentRuleCertificate).where(
                    InstrumentRuleCertificate.symbol == symbol)) is not None:
                continue
            certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
                session, symbol=symbol, step=100)
            InstrumentRuleCertificateRepository(session).issue(
                certificate_id=certificate_id, observation_id=observation.id,
                rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
                reviewer_id="rule-reviewer", interpretation_note="verified fixture",
                identity_symbol=rule.symbol, identity_asset_type="stock",
                identity_locator="fixture identity row 1", review_signature=signature,
            )
        session.commit()


def setup(env, *, cash="20000", separate_family=False, entry_ranks_first=True, worker_scan=False):
    factory = env["session_factory"]
    pid, lids = _seed_buy_lifecycles(factory, cash=cash, symbols=("000001.SZ",), day=DAY)
    with factory() as session:
        life = session.get(PositionLifecycleState, lids[0])
        owner = life.strategy_version_id
        entry_version = owner
        today = datetime.now(CN_TIME).date()
        intents = []
        if separate_family:
            strategy = QuantStrategy(id=uuid4(), name=str(uuid4()), version=1)
            session.add(strategy)
            session.flush()
            version = QuantStrategyVersion(id=uuid4(), strategy_id=strategy.id, version_no=1,
                status="PUBLISHED", source_code="def strategy(context): return {}", source_hash="b"*64, version=1)
            session.add(version)
            session.flush()
            entry_version = version.id
            session.add(StrategyAdmissionEvent(strategy_version_id=entry_version, family_id="entry",
                asset_scope="CN_STOCK", risk_profile="BALANCED", revision=1, state="ADVISORY",
                reason="isolated fixture", request_key="fixture", recorded_at=datetime(2026,9,1,tzinfo=timezone.utc),
                evidence_completed_at=datetime(2026,8,31,tzinfo=timezone.utc), evidence_ref="test://fixture",
                evidence_sha256="a"*64, valid_until=datetime(2030,1,1,tzinfo=timezone.utc),
                net_expectancy_lower_bound=D(".002") if entry_ranks_first else D(".0005")))
            intents.append(PortfolioTargetIntent("entry", entry_version, DAY, today, today, "fixture", "fixture",
                                                (TargetLeg("000002.SZ", D(".5")),)))
        legs = (TargetLeg("000001.SZ", D(".5") if separate_family else D(".25")),)
        if not separate_family:
            legs += (TargetLeg("000002.SZ", D(".25")),)
        intents.append(PortfolioTargetIntent("trend", owner, DAY, today, today, "fixture", "fixture", legs))
        batch = FamilyBatchService(session).project(portfolio_id=pid, request_key="joint", asset_scope="CN_STOCK",
            expected_version_ids=tuple(i.strategy_version_id for i in intents), intents=tuple(intents),
            valuation_date=DAY, closes={"000001.SZ": D(10), "000002.SZ": D(10)})
        if worker_scan:
            import hashlib
            for version_id in {owner, entry_version}:
                version = session.get(QuantStrategyVersion, version_id)
                version.source_code = WORKER_SOURCE
                version.source_hash = hashlib.sha256(WORKER_SOURCE.encode()).hexdigest()
                version.template_id = None
                version.template_params = None
                version.template_renderer_version = None
            session.flush()
        scans = ScanManifestService(session).register(batch_id=batch.id,
            expected_portfolio_version=session.get(Portfolio, pid).version)
        if worker_scan:
            session.commit()
            return pid, batch.id, life.id, scans, owner
        policy = session.get(AnalysisTask, scans[owner]).request_params["execution_snapshot"]["execution_policy"]
        market = {"trade_date": DAY.isoformat(), "raw_close": "10", "qfq_close": "10", "raw_amount": "1000000",
                  "adv20_amount": "1000000",
                  "up_limit": "11", "is_st": False, "is_suspended": False}
        fact = {"close": "10", "high": "10", "ma5": "9.9", "ma20": "9.8", "ma60": "9.7",
                "execution_market": market, "execution_policy": policy, "source_task_id": str(scans[owner]),
                "new_risk_allowed": True, "asset_scope": "CN_STOCK"}
        daily, _, order, _ = PositionLifecycleManager(session).process_day(life.id, trade_date=DAY, fact=fact,
            defer_buy=True, commit=False, data_as_of=datetime.now(timezone.utc))
        assert order is None and daily.planning_result["stage"] == "AWAITING_BATCH"
        context = {"industry": {"industry_code": "801010"}, "industry_bucket_available": True, "new_risk_allowed": True}
        ids = []
        for version, symbol, kind in ((owner, "000001.SZ", "HOLDING"), (entry_version, "000002.SZ", "BUY")):
            signal = QuantExecutionSignal(task_id=scans[version], strategy_version_id=version, attempt_no=1,
                ts_code=symbol, signal_kind=kind, action="HOLD" if kind == "HOLDING" else "BUY",
                score=D(90), entry_price=D(10) if kind == "BUY" else None,
                stop_loss=D(9) if kind == "BUY" else None, take_profit=D(13) if kind == "BUY" else None,
                signal_trade_date=DAY, signal_price_basis="qfq", execution_price_basis="raw", valuation_price=D(10),
                execution_market={**market, "batch_context": context})
            session.add(signal)
            session.flush()
            ids.append(signal.id)
        for task_id in scans.values():
            session.get(AnalysisTask, task_id).status = "SUCCEEDED"
        session.commit()
        return pid, batch.id, daily.id, scans, ids


def test_joint_same_family_add_precedes_entry_and_receipt_replays(env):
    pid, batch_id, daily_id, _scans, _ids = setup(env)
    with env["session_factory"]() as session:
        result = BatchCompletionService(session).complete(batch_id)
        assert result.status == "COMPLETED", result.details
        session.commit()
        receipt = session.get(AllocationExecution, batch_id)
        assert [i["kind"] for i in receipt.result["processing_order"]] == ["LIFECYCLE", "ENTRY"]
        orders = list(session.scalars(select(SuggestedOrder).where(SuggestedOrder.portfolio_id == pid)))
        assert {o.symbol for o in orders} == {"000001.SZ", "000002.SZ"}
        assert sum((o.reserved_cash for o in orders), D(0)) <= D(20000)
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "BATCH_COMPLETED"
        for order in orders:
            order.status = "CANCELLED"
        session.commit()
        assert BatchCompletionService(session).complete(batch_id).entry_receipt_id == batch_id
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 2


@pytest.mark.parametrize("entry_first", [False, True])
def test_family_evidence_rank_precedes_cross_family_add_priority(env, entry_first):
    pid, batch_id, _daily, _scans, _ids = setup(env, cash="2500", separate_family=True, entry_ranks_first=entry_first)
    with env["session_factory"]() as session:
        result = BatchCompletionService(session).complete(batch_id)
        assert result.status == "COMPLETED", result.details
        session.commit()
        orders = list(session.scalars(select(SuggestedOrder).where(SuggestedOrder.portfolio_id == pid)))
        assert len(orders) == 1
        assert orders[0].symbol == ("000002.SZ" if entry_first else "000001.SZ")
        receipt = session.get(AllocationExecution, batch_id)
        assert receipt.result["processing_order"][0]["kind"] == ("ENTRY" if entry_first else "LIFECYCLE")
        assert sum(o.reserved_cash for o in orders) <= D(2500)


def test_joint_rollback_and_concurrent_completion(env):
    pid, batch_id, daily_id, _scans, _ids = setup(env)
    factory = env["session_factory"]
    with factory() as session:
        assert BatchCompletionService(session).complete(batch_id).status == "COMPLETED"
        session.rollback()
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "AWAITING_BATCH"
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
    barrier = Barrier(2)
    def run(_):
        with factory() as session:
            barrier.wait(timeout=10)
            outcome = BatchCompletionService(session).complete(batch_id)
            session.commit()
            return outcome.status
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, range(2))) == ["COMPLETED", "COMPLETED"]
    with factory() as session:
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 2


@pytest.mark.parametrize("case", ["old_attempt", "foreign_source", "market", "policy", "missing_daily", "unknown_scope"])
def test_joint_owner_provenance_failure_blocks_both_orders(env, case):
    pid, batch_id, daily_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        daily = session.get(PositionDailyFact, daily_id)
        if case == "old_attempt":
            session.get(QuantExecutionSignal, ids[0]).attempt_no = 2
        elif case == "missing_daily":
            daily.trade_date = datetime(2026,9,21).date()
        elif case == "unknown_scope":
            daily.input_payload = {**daily.input_payload, "asset_scope": None}
        elif case == "foreign_source":
            daily.input_payload = {**daily.input_payload, "source_task_id": str(uuid4())}
        elif case == "market":
            signal = session.get(QuantExecutionSignal, ids[0])
            signal.execution_market = {**signal.execution_market, "raw_close": "11"}
        else:
            daily.rule_version = "0"*64
        session.commit()
        assert BatchCompletionService(session).complete(batch_id).status == "BLOCKED"
        session.commit()
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_joint_late_failure_rolls_back_entry_and_add_mutations(env, monkeypatch):
    pid, batch_id, daily_id, _scans, _ids = setup(env, separate_family=True)
    real = LifecycleBatchService.materialize
    def fail(service, **kwargs):
        real(service, **kwargs)
        raise ValueError("late fixture failure after both orders")
    monkeypatch.setattr(LifecycleBatchService, "materialize", fail)
    with env["session_factory"]() as session:
        assert BatchCompletionService(session).complete(batch_id).status == "BLOCKED"
        session.commit()
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "AWAITING_BATCH"


def test_owner_buy_signal_is_holding_evidence_not_an_extra_entry(env):
    pid, batch_id, daily_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        signal = session.get(QuantExecutionSignal, ids[0])
        signal.signal_kind, signal.action = "BUY", "BUY"
        signal.entry_price, signal.stop_loss, signal.take_profit = D(10), D(9), D(13)
        for task_id in scans.values():
            session.get(AnalysisTask, task_id).attempt_no = 2
        for signal_id in ids:
            session.get(QuantExecutionSignal, signal_id).attempt_no = 2
        session.commit()
        # Retry retains the original immutable day; current-attempt evidence
        # proves the owner ran again, without putting attempt into the day hash.
        assert BatchCompletionService(session).complete(batch_id).status == "COMPLETED"
        session.commit()
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 2
        assert session.query(SuggestedOrder).filter_by(source_signal_id=ids[0]).count() == 0
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "BATCH_COMPLETED"


def test_pause_after_scan_rejects_entry_and_deferred_add(env):
    from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
    pid, batch_id, daily_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        owner = session.get(QuantExecutionSignal, ids[0]).strategy_version_id
        StrategyAdmissionService(session).record_restriction(owner, family_id="trend", asset_scope="CN_STOCK",
            risk_profile="BALANCED", state="SUSPENDED", reason="fixture", request_key="pause", expected_revision=1)
        session.commit()
        assert BatchCompletionService(session).complete(batch_id).status == "COMPLETED"
        session.commit()
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
        assert session.get(PositionDailyFact, daily_id).planning_result["projection"]["order_status"] == "BUY_REJECTED_BATCH_ADMISSION"


def test_scan_registration_requires_all_open_owners(env):
    from backend.modules.quant_strategy.application.lifecycle_batch import _intent
    from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationMember
    pid, batch_id, _daily, scans, ids = setup(env, separate_family=True)
    with env["session_factory"]() as session:
        entry_version = session.get(QuantExecutionSignal, ids[1]).strategy_version_id
        member = session.get(AllocationMember, (batch_id, entry_version))
        batch = FamilyBatchService(session).project(portfolio_id=pid, request_key="missing-owner",
            asset_scope="CN_STOCK", expected_version_ids=(entry_version,), intents=(_intent(member.intent),),
            valuation_date=DAY, closes={"000001.SZ": D(10), "000002.SZ": D(10)})
        before = session.query(AnalysisTask).count()
        with pytest.raises(ValueError, match="all active lifecycle owners"):
            ScanManifestService(session).register(batch_id=batch.id,
                expected_portfolio_version=session.get(Portfolio, pid).version)
        assert session.query(AnalysisTask).count() == before
        session.rollback()


def test_joint_requires_one_execution_policy_across_add_and_entry(env):
    pid, batch_id, daily_id, _scans, ids = setup(env, separate_family=True)
    with env["session_factory"]() as session:
        daily = session.get(PositionDailyFact, daily_id)
        owner_signal = session.get(QuantExecutionSignal, ids[0])
        task = session.get(AnalysisTask, owner_signal.task_id)
        snapshot = task.request_params["execution_snapshot"]
        changed = {**snapshot["execution_policy"], "slippage_bps": "999"}
        daily.input_payload = {**daily.input_payload, "execution_policy": changed}
        task.request_params = {**task.request_params, "execution_snapshot": {**snapshot, "execution_policy": changed}}
        session.commit()
        result = BatchCompletionService(session).complete(batch_id)
        assert result.status == "BLOCKED"
        assert result.details["message"] == "one common execution policy required"
        session.commit()
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


WORKER_SOURCE = """def strategy(context):
    return {"action": "BUY", "score": 90, "entry_price": 10,
            "stop_loss": 9, "take_profit": 13, "sell_ratio": None, "reason": "fixture"}
"""


def test_registered_workers_produce_owner_day_and_automatically_consume_joint_batch(env, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.modules.analysis.application.contracts import AnalysisArtifact, ClaimedTask
    from backend.modules.analysis.application.reporting import ReportService
    from backend.modules.analysis.application.task_lifecycle import TaskService
    from backend.modules.analysis.domain.enums import TaskType
    from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
    from backend.modules.quant_strategy.application import execution
    from backend.workers.analysis_executor import AnalysisExecutor
    from db.instrument import db as market_db
    from db.instrument.dao import ingest_state

    pid, batch_id, life_id, scans, owner = setup(env, separate_family=True, worker_scan=True)
    factory = env["session_factory"]
    market = Mock()
    def query(sql, *args):
        rows = []
        if "FROM market.instrument WHERE" in sql:
            rows = [("000001.SZ",), ("000002.SZ",)]
        elif "market.industry_member" in sql:
            rows = [(s, "801010", "fixture") for s in ("000001.SZ", "000002.SZ")]
        return SimpleNamespace(fetchall=lambda: rows)
    market.execute.side_effect = query
    @contextmanager
    def connection(_dsn=None):
        yield market
    monkeypatch.setattr(market_db, "get_connection", connection)
    monkeypatch.setattr(ingest_state, "is_industry_bucket_available", lambda conn: (True, None))
    monkeypatch.setattr(execution.AllMarketUniverseBuilder, "list_active_cn_stocks", lambda conn: ["000001.SZ", "000002.SZ"])
    monkeypatch.setattr(execution.DataReadinessGate, "resolve", lambda conn, day: SimpleNamespace(
        requested_trade_date=day, market_as_of_trade_date=day))
    def load(self, _conn, symbols, *args, **kwargs):
        return [{"ts_code": symbol, "status": "OK", "context": {
                    "position": {"shares": 100 if symbol == "000001.SZ" else 0},
                    "ohlcv": {"close": [10], "high": [10], "volume": [100]},
                    "indicators": {"ma_qfq_5": [9.9], "ma_qfq_20": [9.8], "ma_qfq_60": [9.7]}},
                 "execution_market": {"trade_date": DAY.isoformat(), "raw_close": "10", "qfq_close": "10",
                    "raw_amount": "1000000", "adv20_amount": "1000000",
                    "up_limit": "11", "is_st": False, "is_suspended": False}}
                for symbol in symbols]
    monkeypatch.setattr(execution.MarketContextBatchLoader, "load_batch", load)
    monkeypatch.setattr(execution.QuantExecutionService, "_load_holding_closes",
                        lambda *a, **k: {"000001.SZ": D(10), "000002.SZ": D(10)})
    monkeypatch.setattr(execution, "run_strategy", lambda *a, **k: SimpleNamespace(ok=True, output={
        "action": "BUY", "score": 90, "entry_price": 10, "stop_loss": 9,
        "take_profit": 13, "sell_ratio": None, "reason": "fixture"}))

    class Bundles:
        @contextmanager
        def open(self):
            with SqlAlchemyAnalysisUnitOfWork(factory) as uow:
                yield SimpleNamespace(uow=uow, tasks=TaskService(uow), reports=ReportService(),
                    events=Mock(), build_artifact=lambda state: AnalysisArtifact(report_json=state, conclusion_summary=None,
                        risk_flag=False, risk_hint=None, decision=None, artifact_uri=None, checksum=None, duration_ms=0))

    executor = AnalysisExecutor(graph_adapter=None, bundle_factory=Bundles(), worker_id="joint-test")
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))
    for version_id in sorted(scans, key=lambda v: v == owner):
        with factory() as session:
            task = session.get(AnalysisTask, scans[version_id])
            task.status, task.lease_token = "RUNNING", "joint-lease"
            session.commit()
            claimed = ClaimedTask(task_id=task.id, attempt_no=task.attempt_no, lease_token="joint-lease",
                task_type=TaskType.MARKET_WIDE, ticker=None, selected_layers=("position",),
                effective_trade_date=DAY, request_params=task.request_params)
        executor._claimed = claimed
        executor._run_quant(claimed, heartbeat)
        with factory() as session:
            task = session.get(AnalysisTask, claimed.task_id)
            assert task.status == "SUCCEEDED", (task.status, task.error_code, task.error_summary)
            if version_id != owner:
                assert session.scalar(select(PositionDailyFact).where(PositionDailyFact.lifecycle_id == life_id)) is None
                assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
    with factory() as session:
        outcome = session.get(AllocationOutcome, batch_id)
        assert outcome is not None and outcome.status == "COMPLETED", outcome.details if outcome else None
        assert outcome.reason_code == "JOINT_COMPLETED"
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 2
        daily = session.scalar(select(PositionDailyFact).where(PositionDailyFact.lifecycle_id == life_id))
        assert daily.input_payload["source_task_id"] == str(scans[owner])
        assert daily.planning_result["stage"] == "BATCH_COMPLETED"
