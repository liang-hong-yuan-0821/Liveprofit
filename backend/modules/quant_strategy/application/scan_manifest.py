"""Register all family scans atomically in the existing task/outbox system."""
from sqlalchemy import select

from backend.modules.analysis.application.contracts import CreateAnalysisTaskCommand
from backend.modules.analysis.application.quant_task_submission import QuantTaskSubmissionService
from backend.modules.analysis.domain.enums import TaskType
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember, AllocationScan
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionLifecycleState
from .planning_account import lock_portfolio


class ScanManifestService:
    def __init__(self, session):
        self.session = session

    def register(self, *, batch_id, expected_portfolio_version: int, trace_id=None):
        """Caller commits manifest, tasks and outbox together; errors require rollback.

        The batch already contains trusted family intents. This does not generate
        targets, grant admission or dispatch orders. Retries reuse the same tasks.
        """
        batch = self.session.get(AllocationBatch, batch_id)
        if batch is None or batch.asset_scope != "CN_STOCK" or batch.status != "PROJECTED":
            raise ValueError("scan registration requires a projected CN stock batch")
        account = lock_portfolio(self.session, batch.portfolio_id)
        members = list(self.session.scalars(select(AllocationMember).where(
            AllocationMember.batch_id == batch_id).order_by(AllocationMember.strategy_version_id)))
        if not members:
            raise ValueError("empty family manifest")
        previous = list(self.session.scalars(select(AllocationScan).where(AllocationScan.batch_id == batch_id)))
        if previous:
            if {s.strategy_version_id for s in previous} != {m.strategy_version_id for m in members}:
                raise ValueError("incomplete persisted scan manifest")
            for row in previous:
                task = self.session.get(AnalysisTask, row.task_id)
                if task.request_params["execution_snapshot"]["portfolio"]["version"] != expected_portfolio_version:
                    raise ValueError("scan registration portfolio version conflict")
            return {s.strategy_version_id: s.task_id for s in previous}
        if account.version != expected_portfolio_version:
            raise ValueError("scan registration portfolio version conflict")
        owners = set(self.session.scalars(select(PositionLifecycleState.strategy_version_id).where(
            PositionLifecycleState.portfolio_id == batch.portfolio_id,
            PositionLifecycleState.closed_at.is_(None))))
        if not owners.issubset({m.strategy_version_id for m in members}):
            raise ValueError("all active lifecycle owners must have a frozen scan member")
        # Pre-lock every strategy before any task staging: portfolio -> UUID order.
        for member in members:
            self.session.scalar(select(QuantStrategyVersion).where(
                QuantStrategyVersion.id == member.strategy_version_id).with_for_update()
                .execution_options(populate_existing=True))
        command = CreateAnalysisTaskCommand(
            task_type=TaskType.MARKET_WIDE, ticker=None,
            requested_trade_date=batch.valuation_date, selected_layers=("position",),
            analysis_options={}, trace_id=trace_id,
        )
        submitter = QuantTaskSubmissionService(None)
        result = {}
        for member in members:
            task = submitter.stage(
                self.session, strategy_version_id=member.strategy_version_id,
                portfolio_id=batch.portfolio_id, expected_portfolio_version=expected_portfolio_version,
                command=command, idempotency_key=f"family_{batch_id}_{member.strategy_version_id}",
                trace_id=trace_id, defer_new_risk=True,
            )
            self.session.add(AllocationScan(batch_id=batch_id,
                strategy_version_id=member.strategy_version_id, task_id=task.task_id))
            result[member.strategy_version_id] = task.task_id
        self.session.flush()
        return result
