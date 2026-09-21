"""量化任务提交服务（plan 4.2.1）。

跨聚合的唯一提交编排器：同一个 SQLAlchemy Session 锁定策略版本、组合与持仓，
复验 PUBLISHED / expected_portfolio_version / 层级组合，冻结策略源码、组合与持仓
进 canonical execution_snapshot（**不含行情**——不枚举 universe、不读取 OHLCV/因子，
行情在执行时实时获取，见 plan 4.3.1），预计算 input_hash 后经 TaskService.
stage_create_task 暂存 task/outbox；外层是唯一 commit/rollback 边界，任一失败
不留半成品。发生唯一幂等键 IntegrityError 时先 rollback，再以干净 Session 按键
读取既有 task：完整 hash 相同返回 replay，不同返回 IDEMPOTENCY_KEY_REUSED。
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.modules.analysis.application.contracts import CreateAnalysisTaskCommand, TaskCreatedResult
from backend.modules.analysis.application.errors import (
    IdempotencyKeyReusedError,
    PortfolioPositionLimitExceededError,
    PortfolioSnapshotConflictError,
    PortfolioUnsupportedHoldingError,
)
from backend.modules.analysis.application.task_lifecycle import (
    TaskService,
    canonical_task_input,
    hash_canonical_input,
)
from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.infrastructure.repositories import (
    SqlAlchemyTaskOutboxRepository,
    SqlAlchemyTaskRepository,
)
from backend.modules.investment_workspace.application.errors import PortfolioNotFoundError
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.errors import (
    StrategyNotFoundError,
    StrategyVersionNotPublishedError,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.domain.templates import get_template
from backend.shared.clock import Clock, SystemClock

MAX_POSITIONS = 500
SNAPSHOT_SCHEMA_VERSION = "quant_execution_snapshot_v1"


class _SessionBoundAnalysisUow:
    """绑定既有 Session 的 AnalysisUnitOfWork 协议实现（提交服务专用：跨聚合共享 Session）。"""

    def __init__(self, session) -> None:
        self.session = session
        self.tasks = SqlAlchemyTaskRepository(session)
        self.outbox = SqlAlchemyTaskOutboxRepository(session)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


def _replay_result(task) -> TaskCreatedResult:
    return TaskCreatedResult(
        task_id=task.id,
        status=TaskStatus(task.status),
        requested_trade_date=task.requested_trade_date,
        effective_trade_date=task.effective_trade_date,
        date_correction=task.date_correction,
        idempotent_replay=True,
    )


class QuantTaskSubmissionService:
    def __init__(self, session_factory, *, clock: Clock | None = None) -> None:
        self._session_factory = session_factory
        self._clock = clock or SystemClock()

    def submit(
        self,
        *,
        strategy_version_id: uuid.UUID,
        portfolio_id: uuid.UUID,
        expected_portfolio_version: int,
        command: CreateAnalysisTaskCommand,
        idempotency_key: str,
        trace_id: str | None,
    ) -> TaskCreatedResult:
        session = self._session_factory()
        try:
            uow = _SessionBoundAnalysisUow(session)

            # 0) 量化提交参数存在性校验（position 未带策略/组合 → 422 TASK_CREATE_INVALID）
            if (
                strategy_version_id is None
                or portfolio_id is None
                or expected_portfolio_version is None
            ):
                from backend.modules.analysis.application.errors import TaskCreateInvalidError

                raise TaskCreateInvalidError(
                    "position 层必须提供 strategy_version_id/portfolio_id/expected_portfolio_version"
                )

            # 1) SELECT ... FOR UPDATE 锁定 + 复验（PUBLISHED / 组合 version / 持仓约束）
            version = session.execute(
                select(QuantStrategyVersion)
                .where(QuantStrategyVersion.id == strategy_version_id)
                .with_for_update()
            ).scalar_one_or_none()
            if version is None:
                raise StrategyNotFoundError(f"策略版本不存在：{strategy_version_id}")
            if version.status != "PUBLISHED":
                raise StrategyVersionNotPublishedError(f"策略版本未发布：{strategy_version_id}")
            strategy = session.get(QuantStrategy, version.strategy_id)
            portfolio = session.execute(
                select(Portfolio).where(Portfolio.id == portfolio_id).with_for_update()
            ).scalar_one_or_none()
            if portfolio is None:
                raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
            positions = list(
                session.execute(
                    select(PortfolioPosition)
                    .where(PortfolioPosition.portfolio_id == portfolio_id)
                    .order_by(PortfolioPosition.market, PortfolioPosition.symbol)
                ).scalars()
            )

            # 2) 冻结策略/组合/持仓进 execution_snapshot（不含行情）
            snapshot = {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "strategy": {
                    "strategy_id": str(strategy.id),
                    "version_id": str(version.id),
                    "version_no": version.version_no,
                    "source_code": version.source_code,
                    "source_hash": version.source_hash,
                    "name": strategy.name,
                    "template_id": version.template_id,
                    "template_params": version.template_params,
                    "template_renderer_version": version.template_renderer_version,
                    "required_bars": get_template(version.template_id).required_bars if version.template_id else 250,
                },
                "portfolio": {
                    "id": str(portfolio.id),
                    "name": portfolio.name,
                    "version": portfolio.version,
                    "total_assets": format(portfolio.total_assets, "f"),
                    "available_cash": format(portfolio.available_cash, "f"),
                    "risk": {
                        "risk_per_trade_pct": format(portfolio.risk_per_trade_pct, "f"),
                        "min_risk_reward_ratio": format(portfolio.min_risk_reward_ratio, "f"),
                        "max_total_position_pct": format(portfolio.max_total_position_pct, "f"),
                        "max_single_stock_pct": format(portfolio.max_single_stock_pct, "f"),
                        "max_sector_pct": format(portfolio.max_sector_pct, "f"),
                    },
                },
                "positions": [
                    {
                        "market": p.market,
                        "symbol": p.symbol,
                        "quantity": format(p.quantity, "f"),
                        "average_cost": format(p.average_cost, "f"),
                    }
                    for p in positions
                ],
            }

            # 3) 完整 canonical envelope + 预计算 input_hash
            command = replace(
                command,
                strategy_version_id=strategy_version_id,
                portfolio_id=portfolio_id,
                expected_portfolio_version=expected_portfolio_version,
                execution_snapshot=snapshot,
            )
            TaskService.validate_layers(command)
            request_params = {"analysis_options": command.analysis_options or {}, "execution_snapshot": snapshot}
            input_hash = hash_canonical_input(canonical_task_input(command))

            # 4) 幂等检查先于版本/持仓数据校验（plan 4.2.3：同键任一快照字段变化 → REUSED）
            if idempotency_key is not None:
                existing = uow.tasks.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    if existing.input_hash != input_hash:
                        raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同输入")
                    session.commit()
                    return _replay_result(existing)

            # 5) 组合版本与持仓约束校验（数据级，晚于幂等）
            if portfolio.version != expected_portfolio_version:
                raise PortfolioSnapshotConflictError(
                    f"组合已变更（期望 version={expected_portfolio_version}，实际 {portfolio.version}），请重新拉取"
                )
            if len(positions) > MAX_POSITIONS:
                raise PortfolioPositionLimitExceededError(f"组合持仓超过 {MAX_POSITIONS} 条上限")
            non_cn = [p for p in positions if p.market != "CN"]
            if non_cn:
                raise PortfolioUnsupportedHoldingError(
                    f"V1 仅支持 CN 持仓：{non_cn[0].market}/{non_cn[0].symbol}"
                )

            # 6) 暂存 task/outbox
            task_service = TaskService(uow, clock=self._clock)
            result = task_service.stage_create_task(
                command, request_params, input_hash, idempotency_key=idempotency_key, trace_id=trace_id
            )

            # 4) 外层唯一 commit 边界
            session.commit()
            return result
        except IntegrityError:
            # 唯一幂等键竞争：先 rollback，再以干净 Session 按键读取既有 task
            session.rollback()
            with self._session_factory() as clean_session:
                existing = SqlAlchemyTaskRepository(clean_session).get_by_idempotency_key(idempotency_key)
            if existing is None:  # 非幂等键类 IntegrityError：原样抛出
                raise
            if existing.input_hash == input_hash:
                return _replay_result(existing)
            raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同输入") from None
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
