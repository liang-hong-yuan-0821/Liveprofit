"""量化任务提交服务集成测试（plan 4.2.1/4.2.3）：原子快照、幂等竞争/冲突、校验失败无残留。"""

from __future__ import annotations

import uuid
from datetime import date

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
from backend.modules.quant_strategy.infrastructure.repositories import SqlAlchemyQuantStrategyUnitOfWork

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


def _published_strategy(env, name=None) -> tuple[uuid.UUID, uuid.UUID]:
    """返回 (strategy_id, published_version_id)。"""
    if name is None:
        name = f"策略-{uuid.uuid4().hex[:8]}"
    with SqlAlchemyQuantStrategyUnitOfWork(env["session_factory"]) as uow:
        service = QuantStrategyService(uow)
        created = service.create(name, source_code=LEGAL)
        published = service.publish(created.id, created.versions[0].id, expected_version=1).published
        return created.id, published.id


def _portfolio(env, name=None, positions=None):
    if name is None:
        name = f"组合-{uuid.uuid4().hex[:8]}"
    with SqlAlchemyWorkspaceUnitOfWork(env["session_factory"]) as uow:
        service = PortfolioService(uow)
        dto = service.create(name, total_assets=100000, available_cash=35000)
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
    sid, vid = _published_strategy(env)
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
        assert snapshot["schema_version"] == "quant_execution_snapshot_v1"
        assert snapshot["strategy"]["version_id"] == str(vid)
        assert "def strategy(context)" in snapshot["strategy"]["source_code"]
        assert snapshot["portfolio"]["name"] == portfolio.name
        assert snapshot["portfolio"]["total_assets"] == "100000.0000"
        assert snapshot["positions"] == [
            {"market": "CN", "symbol": "600519.SH", "quantity": "100.0000", "average_cost": "1500.0000"}
        ]
        assert row.selected_layers == ["position"]
        assert len(row.input_hash) == 64
        outbox = session.execute(
            text("SELECT status FROM task_outbox WHERE task_id = :id"), {"id": result.task_id}
        ).scalar_one()
        assert outbox == "PENDING"


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
