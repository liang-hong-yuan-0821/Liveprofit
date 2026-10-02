"""Recoverable completion of registered family scans; no new task scheduler."""
import logging
from decimal import Decimal

from sqlalchemy import Date, cast, exists, func, or_, select

from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.infrastructure.allocation_models import (
    AllocationBatch, AllocationExecution, AllocationMember, AllocationOutcome, AllocationScan,
)
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal
from .errors import FillValidationError
from .entry_batch import EntryBatchService
from .family_batch import CN_TIME
from .planning_account import lock_portfolio

logger = logging.getLogger(__name__)
FAILED = ("FAILED", "CANCELLED", "CANCEL_REQUESTED")


class BatchCompletionService:
    def __init__(self, session):
        self.session = session

    def for_task(self, task_id):
        batch_id = self.session.scalar(select(AllocationScan.batch_id).where(AllocationScan.task_id == task_id))
        return self.complete(batch_id) if batch_id else None

    def complete(self, batch_id):
        """Return None while waiting; caller commits outcome and orders together."""
        batch = self.session.get(AllocationBatch, batch_id)
        if batch is None:
            raise ValueError("allocation batch missing")
        lock_portfolio(self.session, batch.portfolio_id)
        previous = self.session.get(AllocationOutcome, batch_id)
        if previous:
            return previous
        scans = list(self.session.scalars(select(AllocationScan).where(AllocationScan.batch_id == batch_id)))
        if not scans:
            return None  # Unregistered projections are not executable work.
        members = list(self.session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)))
        tasks = list(self.session.scalars(select(AnalysisTask).where(
            AnalysisTask.id.in_([s.task_id for s in scans])).order_by(AnalysisTask.id)
            .with_for_update(read=True).execution_options(populate_existing=True)))
        now = self.session.scalar(select(func.clock_timestamp()))
        audit = {"tasks": [{"task_id": str(t.id), "attempt_no": t.attempt_no, "status": t.status} for t in tasks]}

        def finish(code, *, receipt=None, details=None):
            result = AllocationOutcome(batch_id=batch_id, status="COMPLETED" if receipt else "BLOCKED",
                reason_code=code, decided_at=now, entry_receipt_id=receipt.batch_id if receipt else None,
                details={**audit, **(details or {})})
            self.session.add(result)
            self.session.flush()
            return result

        receipt = self.session.get(AllocationExecution, batch_id)
        if receipt:
            return finish("ENTRY_ALREADY_COMPLETED", receipt=receipt)
        if len(tasks) != len(scans) or {m.strategy_version_id for m in members} != {s.strategy_version_id for s in scans}:
            return finish("INCOMPLETE_SCAN_MANIFEST")
        if any(t.status in FAILED for t in tasks):
            return finish("SCAN_FAILED_OR_CANCELLED")
        if batch.decision_at.astimezone(CN_TIME).date() != now.astimezone(CN_TIME).date():
            return finish("BATCH_DECISION_EXPIRED")
        if any(t.status != "SUCCEEDED" for t in tasks):
            return None
        by_task = {t.id: t for t in tasks}
        for scan in scans:
            task = by_task[scan.task_id]
            snapshot = task.request_params.get("execution_snapshot", {})
            if (snapshot.get("new_risk_mode") != "FAMILY_BATCH"
                    or snapshot.get("portfolio", {}).get("id") != str(batch.portfolio_id)
                    or snapshot.get("strategy", {}).get("version_id") != str(scan.strategy_version_id)
                    or task.effective_trade_date != batch.valuation_date):
                return finish("SCAN_PROVENANCE_MISMATCH")
        attempts = {s.strategy_version_id: (s.task_id, by_task[s.task_id].attempt_no) for s in scans}
        try:
            # Roll back any partial signal/projection mutations before recording
            # an input rejection. Infrastructure faults propagate for recovery.
            with self.session.begin_nested():
                industries, available = self._industry_context(batch, tasks)
                receipt = EntryBatchService(self.session).materialize(
                    batch_id=batch_id, scan_attempts=attempts,
                    closes={s: Decimal(v) for s, v in batch.result["closes"].items()},
                    industry_map=industries, industry_bucket_available=available,
                )
        except (ValueError, FillValidationError) as exc:
            return finish("BATCH_INPUT_REJECTED", details={"message": str(exc)[:512]})
        return finish("JOINT_COMPLETED" if receipt.result.get("lifecycle") else "ENTRY_COMPLETED", receipt=receipt)

    def _industry_context(self, batch, tasks):
        industries, contexts = {}, {}
        available = True
        for task in tasks:
            signals = self.session.scalars(select(QuantExecutionSignal).where(
                QuantExecutionSignal.task_id == task.id, QuantExecutionSignal.attempt_no == task.attempt_no,
                QuantExecutionSignal.signal_kind.in_(("BUY", "HOLDING")),
            ))
            for signal in signals:
                market = signal.execution_market or {}
                context = market.get("batch_context")
                if (not isinstance(context, dict)
                        or type(context.get("industry_bucket_available")) is not bool
                        or context.get("new_risk_allowed") is not True):
                    raise ValueError("complete frozen scan risk context required")
                industry = context.get("industry")
                if industry is not None and not isinstance(industry, dict):
                    raise ValueError("invalid frozen industry bucket")
                if signal.ts_code in contexts and contexts[signal.ts_code] != context:
                    raise ValueError("conflicting frozen industry contexts")
                contexts[signal.ts_code] = context
                if industry is not None:
                    industries[signal.ts_code] = industry
                available = available and context["industry_bucket_available"]
        return industries, available


def ready_batch_ids(session, *, limit=100):
    """Waiting scans cannot starve terminal/expired batches behind a LIMIT."""
    registered = exists(select(AllocationScan.batch_id).where(AllocationScan.batch_id == AllocationBatch.id))
    task_rows = select(AnalysisTask.id).join(AllocationScan, AllocationScan.task_id == AnalysisTask.id).where(
        AllocationScan.batch_id == AllocationBatch.id)
    failed = exists(task_rows.where(AnalysisTask.status.in_(FAILED)))
    unfinished = exists(task_rows.where(AnalysisTask.status != "SUCCEEDED"))
    expired = cast(func.timezone("Asia/Shanghai", AllocationBatch.decision_at), Date) < cast(
        func.timezone("Asia/Shanghai", func.clock_timestamp()), Date)
    return list(session.scalars(select(AllocationBatch.id).where(
        registered, ~exists(select(AllocationOutcome.batch_id).where(AllocationOutcome.batch_id == AllocationBatch.id)),
        or_(failed, ~unfinished, expired),
    ).order_by(AllocationBatch.decision_at, AllocationBatch.id).limit(limit)))


def recover_batches(session_factory, *, limit=100):
    with session_factory() as session:
        batch_ids = ready_batch_ids(session, limit=limit)
    completed = 0
    for batch_id in batch_ids:
        try:
            with session_factory() as session:
                outcome = BatchCompletionService(session).complete(batch_id)
                session.commit()
                completed += int(outcome is not None)
        except Exception:
            # Session context rolls back before the next independent batch.
            logger.exception("家族批次收敛失败，保留后续恢复：batch=%s", batch_id)
    return completed
