# test-catalog-begin
# {
#   "purpose": "量化任务提交服务集成测试（plan 4.2.1/4.2.3）：原子快照、幂等竞争/冲突、校验失败无残留。",
#   "keywords": [
#     "量化策略",
#     "绑定",
#     "执行",
#     "幂等",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "仓位管理",
#     "重放",
#     "来源证据",
#     "任务提交",
#     "任务",
#     "task_submission",
#     "bound",
#     "execution",
#     "idempotent",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "position",
#     "replay",
#     "source",
#     "submission",
#     "task"
#   ],
#   "covers": [
#     "backend/modules/analysis/application/contracts.py",
#     "backend/modules/analysis/application/errors.py",
#     "backend/modules/analysis/application/quant_task_submission.py",
#     "backend/modules/analysis/application/task_lifecycle.py",
#     "backend/modules/analysis/domain/enums.py",
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/analysis/infrastructure/repositories.py",
#     "backend/modules/investment_workspace/application/portfolios.py",
#     "backend/modules/investment_workspace/domain/values.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/repositories.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/service.py",
#     "backend/modules/quant_strategy/domain/templates.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/repositories.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""量化任务提交服务集成测试（plan 4.2.1/4.2.3）：原子快照、幂等竞争/冲突、校验失败无残留。"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from backend.modules.analysis.application.contracts import CreateAnalysisTaskCommand
from backend.modules.analysis.application.errors import (
    IdempotencyKeyReusedError,
    PortfolioPositionLimitExceededError,
    PortfolioSnapshotConflictError,
    PortfolioUnsupportedHoldingError,
    TaskCreateInvalidError,
)
from backend.modules.analysis.application.quant_task_submission import QuantTaskSubmissionService
from backend.modules.analysis.application.task_lifecycle import TaskService
from backend.modules.analysis.domain.enums import TaskType
from backend.modules.investment_workspace.application.portfolios import PortfolioService
from backend.modules.investment_workspace.domain.values import InstrumentRef
from backend.modules.investment_workspace.infrastructure.repositories import SqlAlchemyWorkspaceUnitOfWork
from backend.modules.quant_strategy.application.errors import (
    StrategyNotFoundError,
    StrategyVersionNotPublishedError,
)
from backend.modules.quant_strategy.application.service import QuantStrategyService
from backend.modules.quant_strategy.application.lifecycle_service import LifecyclePolicyService
from backend.modules.quant_strategy.infrastructure.repositories import SqlAlchemyQuantStrategyUnitOfWork
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder

LEGAL = '''
def strategy(context):
    close = context["ohlcv"]["close"]
    price = close[-1]
    if price > 1:
        return {"action": "BUY", "score": 90, "entry_price": price,
                "stop_loss": round(price * 0.965, 2), "take_profit": round(price * 1.10, 2),
                "sell_ratio": None, "reason": "x"}
    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "y"}
'''


def _command(**overrides) -> CreateAnalysisTaskCommand:
    base = dict(
        task_type=TaskType.MARKET_WIDE,
        ticker=None,
        requested_trade_date=date(2026, 9, 15),
        selected_layers=("position",),
        analysis_options={},
        trace_id=None,
    )
    base.update(overrides)
    return CreateAnalysisTaskCommand(**base)


def _published_strategy(env, name=None, template_id=None) -> tuple[uuid.UUID, uuid.UUID]:
    """返回 (strategy_id, published_version_id)。"""
    if name is None:
        name = f"策略-{uuid.uuid4().hex[:8]}"
    with SqlAlchemyQuantStrategyUnitOfWork(env["session_factory"]) as uow:
        service = QuantStrategyService(uow)
        if template_id:
            from backend.modules.quant_strategy.domain.templates import TEMPLATES

            source, params = TEMPLATES[template_id].render()
            created = service.create(
                name, source_code=source, template_id=template_id, template_params=params,
            )
        else:
            created = service.create(name, source_code=LEGAL)
        published = service.publish(created.id, created.versions[0].id, expected_version=1).published
        return created.id, published.id


def _portfolio(env, name=None, positions=None):
    if name is None:
        name = f"组合-{uuid.uuid4().hex[:8]}"
    with SqlAlchemyWorkspaceUnitOfWork(env["session_factory"]) as uow:
        service = PortfolioService(uow)
        dto = service.create(name, total_assets=100000, available_cash=35000, risk_profile="BALANCED")
        for market, symbol, qty, cost in positions or []:
            service.upsert_position(dto.id, InstrumentRef(market=market, symbol=symbol), qty, cost, expected_revision=1)
        return service.get(dto.id)


def _count_tasks(env) -> int:
    with env["session_factory"]() as session:
        return session.execute(text("SELECT count(*) FROM analysis_tasks")).scalar_one()


def _count_outbox(env) -> int:
    with env["session_factory"]() as session:
        return session.execute(text("SELECT count(*) FROM task_outbox")).scalar_one()


def test_happy_path_freezes_snapshot_with_source(env):
    sid, vid = _published_strategy(env, template_id="ma_trend_cross_v1")
    portfolio = _portfolio(env, positions=[("CN", "600519.SH", 100, 1500.0)])
    service = QuantTaskSubmissionService(env["session_factory"])
    result = service.submit(
        strategy_version_id=vid,
        portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version,
        command=_command(),
        idempotency_key=f"happy-{uuid.uuid4().hex[:12]}",
        trace_id="t",
    )
    assert result.idempotent_replay is False
    with env["session_factory"]() as session:
        row = session.execute(
            text("SELECT request_params, selected_layers, input_hash FROM analysis_tasks WHERE id = :id"),
            {"id": result.task_id},
        ).one()
        snapshot = row.request_params["execution_snapshot"]
        assert snapshot["schema_version"] == "quant_execution_snapshot_v2"
        assert snapshot["execution_policy"]["version"] == "cn_execution_v1"
        assert snapshot["strategy"]["template_contract"]["template_id"] == "ma_trend_cross_v1"
        assert snapshot["strategy"]["template_contract"]["required_fields"]
        assert snapshot["strategy"]["version_id"] == str(vid)
        assert "def strategy(context)" in snapshot["strategy"]["source_code"]
        assert snapshot["portfolio"]["risk_profile"] == "BALANCED"
        assert snapshot["portfolio"]["name"] == portfolio.name
        assert snapshot["portfolio"]["total_assets"] == "100000.0000"
        assert snapshot["portfolio"]["risk"]["max_portfolio_open_risk_pct"] == "0.040000"
        assert snapshot["portfolio"]["risk"]["net_asset_value"] is None
        assert snapshot["pending_orders"] == []
        assert snapshot["positions"] == [
            {
                "market": "CN", "symbol": "600519.SH", "quantity": "100.0000",
                "average_cost": "1500.0000", "active_stop_price": None,
            }
        ]
        assert row.selected_layers == ["position"]
        assert len(row.input_hash) == 64
        outbox = session.execute(
            text("SELECT status FROM task_outbox WHERE task_id = :id"), {"id": result.task_id}
        ).scalar_one()
        assert outbox == "PENDING"


def test_active_suggested_order_is_frozen_into_next_task_snapshot(env):
    _sid, version_id = _published_strategy(env)
    portfolio = _portfolio(env)
    order_id = uuid.uuid4()
    with env["session_factory"]() as session:
        session.add(SuggestedOrder(
            id=order_id, portfolio_id=portfolio.id,
            market="CN", symbol="000001.SZ", industry_code="801080", side="BUY",
            quantity=Decimal("100"), filled_quantity=Decimal("20"),
            limit_price=Decimal("10"), stop_price=Decimal("9"),
            reserved_cash=Decimal("1000"), reserved_risk=Decimal("100"),
            reason_code="TEST_PENDING", status="PARTIALLY_FILLED", revision=2,
        ))
        session.commit()

    result = QuantTaskSubmissionService(env["session_factory"]).submit(
        strategy_version_id=version_id, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=f"pending-{uuid.uuid4().hex[:12]}", trace_id="t",
    )
    with env["session_factory"]() as session:
        params = session.execute(
            text("SELECT request_params FROM analysis_tasks WHERE id = :id"),
            {"id": result.task_id},
        ).scalar_one()
    assert params["execution_snapshot"]["pending_orders"] == [{
        "id": str(order_id), "side": "BUY", "symbol": "000001.SZ",
        "industry_code": "801080", "remaining_quantity": "80.0000",
        "order_entry_price": "10.0000", "order_stop_price": "9.0000",
        "reserved_cash": "800.0000", "status": "PARTIALLY_FILLED", "revision": 2,
    }]


def test_bound_lifecycle_policy_is_frozen_into_execution_snapshot(env):
    with env["session_factory"]() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"snapshot-{uuid.uuid4().hex[:8]}", required_fields=["ma5", "ma20"],
            config={
                "template_id": "ma_trend_cross_v1", "reward_multiple": "2.5",
                "initial_exposure_pct": "0.50",
            },
        )
    with SqlAlchemyQuantStrategyUnitOfWork(env["session_factory"]) as uow:
        service = QuantStrategyService(uow)
        from backend.modules.quant_strategy.domain.templates import TEMPLATES
        source, template_params = TEMPLATES["ma_trend_cross_v1"].render()
        created = service.create(
            f"绑定-{uuid.uuid4().hex[:8]}", source_code=source,
            template_id="ma_trend_cross_v1", template_params=template_params,
        )
        draft = created.versions[0]
        bound = service.bind_lifecycle_policy(
            created.id, draft.id, policy.id, expected_version=draft.version,
        )
        published = service.publish(
            created.id, draft.id, expected_version=bound.version,
        ).published
    portfolio = _portfolio(env)
    result = QuantTaskSubmissionService(env["session_factory"]).submit(
        strategy_version_id=published.id, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=f"life-snapshot-{uuid.uuid4().hex[:8]}", trace_id="t",
    )
    with env["session_factory"]() as session:
        params = session.execute(
            text("SELECT request_params FROM analysis_tasks WHERE id = :id"),
            {"id": result.task_id},
        ).scalar_one()
    frozen = params["execution_snapshot"]["strategy"]["lifecycle_policy"]
    assert frozen["id"] == str(policy.id)
    assert frozen["content_hash"] == policy.content_hash
    assert frozen["config"]["template_id"] == "ma_trend_cross_v1"


def test_draft_strategy_rejected_no_residue(env):
    with SqlAlchemyQuantStrategyUnitOfWork(env["session_factory"]) as uow:
        created = QuantStrategyService(uow).create(f"草稿-{uuid.uuid4().hex[:8]}", source_code=LEGAL)
        draft_id = created.versions[0].id
    portfolio = _portfolio(env)
    before_tasks, before_outbox = _count_tasks(env), _count_outbox(env)
    with pytest.raises(StrategyVersionNotPublishedError):
        QuantTaskSubmissionService(env["session_factory"]).submit(
            strategy_version_id=draft_id,
            portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version,
            command=_command(),
            idempotency_key=f"draft-{uuid.uuid4().hex[:12]}",
            trace_id="t",
        )
    assert _count_tasks(env) == before_tasks
    assert _count_outbox(env) == before_outbox


def test_portfolio_version_conflict(env):
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    with pytest.raises(PortfolioSnapshotConflictError):
        QuantTaskSubmissionService(env["session_factory"]).submit(
            strategy_version_id=vid,
            portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version + 1,
            command=_command(),
            idempotency_key=f"conflict-{uuid.uuid4().hex[:12]}",
            trace_id="t",
        )


def test_idempotent_replay_and_reused(env):
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    key = f"idem-{uuid.uuid4().hex[:12]}"
    service = QuantTaskSubmissionService(env["session_factory"])
    first = service.submit(
        strategy_version_id=vid, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=key, trace_id="t",
    )
    replay = service.submit(
        strategy_version_id=vid, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=key, trace_id="t",
    )
    assert replay.idempotent_replay is True
    assert replay.task_id == first.task_id
    # 同 key 改组合 version（快照变化）→ REUSED
    with pytest.raises(IdempotencyKeyReusedError):
        service.submit(
            strategy_version_id=vid, portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version + 1, command=_command(),
            idempotency_key=key, trace_id="t",
        )


def test_non_cn_holding_rejected(env):
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env, positions=[("US", "AAPL", 10, 200.0)])
    with pytest.raises(PortfolioUnsupportedHoldingError):
        QuantTaskSubmissionService(env["session_factory"]).submit(
            strategy_version_id=vid, portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version, command=_command(),
            idempotency_key=f"us-{uuid.uuid4().hex[:12]}", trace_id="t",
        )


def test_position_limit_exceeded(env):
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    with env["session_factory"]() as session:
        for i in range(501):
            session.execute(
                text(
                    "INSERT INTO portfolio_positions (id, portfolio_id, market, symbol, quantity, average_cost, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :pid, 'CN', :sym, 100, 10, now(), now())"
                ),
                {"pid": portfolio.id, "sym": f"{i:06d}.SZ"},
            )
        session.commit()
    with pytest.raises(PortfolioPositionLimitExceededError):
        QuantTaskSubmissionService(env["session_factory"]).submit(
            strategy_version_id=vid, portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version, command=_command(),
            idempotency_key=f"limit-{uuid.uuid4().hex[:12]}", trace_id="t",
        )


def test_validate_layers_quant_params(env):
    # position 未带三参数 → 拒绝
    with pytest.raises(TaskCreateInvalidError):
        TaskService.validate_layers(_command())
    # 未选 position 却带参数 → 拒绝
    with pytest.raises(TaskCreateInvalidError):
        TaskService.validate_layers(
            _command(selected_layers=("market",), strategy_version_id=uuid.uuid4())
        )
    # position 独立成任务合法（不再要求 screening，决策 11）
    TaskService.validate_layers(
        _command(strategy_version_id=uuid.uuid4(), portfolio_id=uuid.uuid4(), expected_portfolio_version=1)
    )


def test_idempotency_race_integrity_error_branch_replays(env, monkeypatch):
    """双 Session 幂等竞争：预检双空（模拟竞争）→ 唯一键 IntegrityError → 干净 Session 重读 → replay。"""
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    key = f"race-{uuid.uuid4().hex[:12]}"
    service = QuantTaskSubmissionService(env["session_factory"])
    first = service.submit(
        strategy_version_id=vid, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=key, trace_id="t",
    )
    assert first.idempotent_replay is False

    # 让两次预检（服务层 + stage 内部）都读不到既有行（模拟并发双方都未提交），
    # 提交时撞唯一键 → 走 IntegrityError 分支；第三次调用（干净 Session 重读）恢复真实实现
    from backend.modules.analysis.infrastructure import repositories as analysis_repos

    real_get = analysis_repos.SqlAlchemyTaskRepository.get_by_idempotency_key
    calls = {"n": 0}

    def flaky(self, key):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None
        return real_get(self, key)

    monkeypatch.setattr(analysis_repos.SqlAlchemyTaskRepository, "get_by_idempotency_key", flaky)
    raced = service.submit(
        strategy_version_id=vid, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version, command=_command(),
        idempotency_key=key, trace_id="t",
    )
    assert raced.idempotent_replay is True
    assert raced.task_id == first.task_id


def test_same_key_different_task_fields_reused_matrix(env):
    """同幂等键下只改任一任务字段 → IDEMPOTENCY_KEY_REUSED（仅完整 envelope 相同才重放）。"""
    sid, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    key = f"matrix-{uuid.uuid4().hex[:12]}"
    service = QuantTaskSubmissionService(env["session_factory"])
    base = dict(
        strategy_version_id=vid, portfolio_id=portfolio.id,
        expected_portfolio_version=portfolio.version,
        idempotency_key=key, trace_id="t",
    )
    service.submit(command=_command(), **base)
    for variant in [
        _command(requested_trade_date=date(2026, 9, 14)),
        _command(selected_layers=("market", "position")),
        _command(analysis_options={"foo": "bar"}),
    ]:
        with pytest.raises(IdempotencyKeyReusedError):
            service.submit(command=variant, **base)


def test_submission_uses_account_before_strategy_lock_like_order_planning(env):
    from sqlalchemy import event
    _sid, version_id = _published_strategy(env)
    portfolio = _portfolio(env)
    statements = []
    engine = env["session_factory"].kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        QuantTaskSubmissionService(env["session_factory"]).submit(
            strategy_version_id=version_id, portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version, command=_command(),
            idempotency_key=f"lock-order-{uuid.uuid4().hex}", trace_id="test")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert "FROM portfolios" in statements[0]
    assert "FROM quant_strategy_versions" in statements[1]


def test_deferred_mode_is_frozen_and_part_of_idempotency(env):
    _, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    service = QuantTaskSubmissionService(env["session_factory"])
    kwargs = dict(strategy_version_id=vid, portfolio_id=portfolio.id,
                  expected_portfolio_version=portfolio.version, command=_command(),
                  idempotency_key=f"deferred-{uuid.uuid4().hex}", trace_id=None)
    first = service.submit(**kwargs, defer_new_risk=True)
    assert service.submit(**kwargs, defer_new_risk=True).task_id == first.task_id
    with pytest.raises(IdempotencyKeyReusedError):
        service.submit(**kwargs)
    with env["session_factory"]() as session:
        params = session.execute(text("SELECT request_params FROM analysis_tasks WHERE id=:id"),
                                 {"id": first.task_id}).scalar_one()
        assert params["execution_snapshot"]["new_risk_mode"] == "FAMILY_BATCH"


def test_staging_members_and_replay_never_commits_caller_transaction(env):
    versions = sorted([_published_strategy(env)[1] for _ in range(2)])
    portfolio = _portfolio(env)
    service = QuantTaskSubmissionService(env["session_factory"])
    before = (_count_tasks(env), _count_outbox(env))
    with env["session_factory"]() as session:
        created = []
        for vid in versions:
            kwargs = dict(strategy_version_id=vid, portfolio_id=portfolio.id,
                          expected_portfolio_version=portfolio.version, command=_command(),
                          idempotency_key=f"stage-{uuid.uuid4().hex}", trace_id=None,
                          defer_new_risk=True)
            result = service.stage(session, **kwargs)
            replay = service.stage(session, **kwargs)
            assert replay.idempotent_replay and replay.task_id == result.task_id
            created.append(result)
        session.flush()
        # Another connection sees no partially published task or dispatch event.
        assert (_count_tasks(env), _count_outbox(env)) == before
        session.rollback()
    assert (_count_tasks(env), _count_outbox(env)) == before


def test_failed_second_staged_member_rolls_back_whole_group(env):
    _, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    service = QuantTaskSubmissionService(env["session_factory"])
    before = (_count_tasks(env), _count_outbox(env))
    with env["session_factory"]() as session:
        kwargs = dict(portfolio_id=portfolio.id, expected_portfolio_version=portfolio.version,
                      command=_command(), trace_id=None, defer_new_risk=True)
        service.stage(session, strategy_version_id=vid,
                      idempotency_key=f"stage-ok-{uuid.uuid4().hex}", **kwargs)
        with pytest.raises(StrategyNotFoundError):
            service.stage(session, strategy_version_id=uuid.uuid4(),
                          idempotency_key=f"stage-fail-{uuid.uuid4().hex}", **kwargs)
        session.rollback()
    assert (_count_tasks(env), _count_outbox(env)) == before


def test_staged_members_publish_together_on_caller_commit(env):
    versions = sorted([_published_strategy(env)[1] for _ in range(2)])
    portfolio = _portfolio(env)
    service = QuantTaskSubmissionService(env["session_factory"])
    before = (_count_tasks(env), _count_outbox(env))
    with env["session_factory"]() as session:
        for vid in versions:
            service.stage(session, strategy_version_id=vid, portfolio_id=portfolio.id,
                          expected_portfolio_version=portfolio.version, command=_command(),
                          idempotency_key=f"stage-commit-{uuid.uuid4().hex}", trace_id=None,
                          defer_new_risk=True)
        session.commit()
    assert (_count_tasks(env), _count_outbox(env)) == (before[0] + 2, before[1] + 2)


def test_stage_refreshes_preloaded_positions_and_pending_orders_after_lock(env):
    from sqlalchemy import select
    from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
    from backend.modules.analysis.infrastructure.models import AnalysisTask

    _, vid = _published_strategy(env)
    portfolio = _portfolio(env, positions=[("CN", "000001.SZ", 100, 10)])
    order_id = uuid.uuid4()
    factory = env["session_factory"]
    with factory() as session:
        session.add(SuggestedOrder(
            id=order_id, portfolio_id=portfolio.id, market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(100), filled_quantity=Decimal(0), limit_price=Decimal(10),
            stop_price=Decimal(9), reserved_cash=Decimal(1000), reserved_risk=Decimal(100),
            reason_code="TEST_PENDING", status="PROPOSED", revision=1,
        ))
        session.commit()
    with factory() as stale:
        position = stale.scalar(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio.id))
        order = stale.get(SuggestedOrder, order_id)
        with factory() as writer:
            current = writer.get(PortfolioPosition, position.id)
            current.quantity = Decimal(150)
            pending = writer.get(SuggestedOrder, order_id)
            pending.filled_quantity, pending.status = Decimal(50), "PARTIALLY_FILLED"
            pending.revision = 2
            writer.commit()
        assert position.quantity == 100 and order.filled_quantity == 0
        result = QuantTaskSubmissionService(factory).stage(
            stale, strategy_version_id=vid, portfolio_id=portfolio.id,
            expected_portfolio_version=portfolio.version, command=_command(),
            idempotency_key=f"refresh-{uuid.uuid4().hex}", trace_id=None, defer_new_risk=True,
        )
        snapshot = stale.get(AnalysisTask, result.task_id).request_params["execution_snapshot"]
        assert Decimal(snapshot["positions"][0]["quantity"]) == 150
        assert Decimal(snapshot["pending_orders"][0]["remaining_quantity"]) == 50
        assert snapshot["pending_orders"][0]["revision"] == 2
        stale.rollback()


def test_stage_rechecks_preloaded_strategy_publication(env):
    from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
    _, vid = _published_strategy(env)
    portfolio = _portfolio(env)
    factory = env["session_factory"]
    with factory() as stale:
        version = stale.get(QuantStrategyVersion, vid)
        with factory() as writer:
            writer.get(QuantStrategyVersion, vid).status = "ARCHIVED"
            writer.commit()
        assert version.status == "PUBLISHED"
        with pytest.raises(StrategyVersionNotPublishedError):
            QuantTaskSubmissionService(factory).stage(
                stale, strategy_version_id=vid, portfolio_id=portfolio.id,
                expected_portfolio_version=portfolio.version, command=_command(),
                idempotency_key=f"refresh-version-{uuid.uuid4().hex}", trace_id=None,
            )
        stale.rollback()
