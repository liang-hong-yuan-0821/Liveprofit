"""报告路由（§3.2.1：只读最新成功版本，无版本 Query 参数）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.reports import ReportDTO, ReportSectionDTO, ReportTaskInfo
from backend.modules.analysis.application.errors import ReportNotFoundError
from backend.modules.analysis.domain.enums import TaskType

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/analysis-tasks/{task_id}/report", response_model=Envelope[ReportDTO])
async def get_report(task_id: uuid.UUID, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            report = bundle.reports.get_latest_report(bundle.uow, task_id)  # 不存在 → ReportNotFoundError(404)
            task = bundle.uow.tasks.get(task_id)
            if task is None:
                raise ReportNotFoundError(f"任务 {task_id} 不存在")
            duration_ms = None
            if task.started_at is not None and task.finished_at is not None:
                duration_ms = int((task.finished_at - task.started_at).total_seconds() * 1000)
            previews = None
            decision_orm = bundle.uow.reports.get_latest(task_id)
            decision = (decision_orm.decision if decision_orm is not None else None) or {}
            if isinstance(decision, dict) and isinstance(decision.get("quant_execution"), dict):
                from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignalRepository

                previews = QuantExecutionSignalRepository(bundle.uow.session).top_previews(
                    task_id, report.attempt_no
                )
            return report, task, duration_ms, previews, decision

    report, task, duration_ms, previews, decision = await services.run(_do)
    report_json = report.report_json or {}

    def _quant_execution():
        # decision["quant_execution"] 的无源码投影（旧/损坏防御解析为 None）；
        # 预览由 signals 表按 report.attempt_no 读取（首 50，禁止无界数组）
        payload = decision.get("quant_execution") if isinstance(decision, dict) else None
        if not isinstance(payload, dict):
            return None
        try:
            from backend.api.schemas.quant_execution import (
                MatchingBuyPreviewDTO,
                PortfolioSnapshotDTO,
                QuantExecutionDTO,
                QuantExecutionSummaryDTO,
                StrategyAuditDTO,
                HoldingSignalPreviewDTO,
                SuggestedOrderPreviewDTO,
            )
            strategy = payload.get("strategy") or {}
            portfolio = payload.get("portfolio_snapshot") or {}
            summary = payload.get("summary") or {}
            return QuantExecutionDTO(
                strategy=StrategyAuditDTO(
                    name=str(strategy.get("name") or ""),
                    version_no=int(strategy.get("version_no") or 0),
                    source_hash_prefix=str(strategy.get("source_hash_prefix") or ""),
                    published_at=strategy.get("published_at"),
                ),
                portfolio_snapshot=PortfolioSnapshotDTO(
                    name=str(portfolio.get("name") or ""),
                    version=int(portfolio.get("version") or 0),
                    total_assets=str(portfolio.get("total_assets") or "0"),
                    available_cash=str(portfolio.get("available_cash") or "0"),
                    risk=portfolio.get("risk") or {},
                    snapshot_at=portfolio.get("snapshot_at"),
                ),
                matching_buy_preview=[] if previews is None else [
                    MatchingBuyPreviewDTO(
                        symbol=r.ts_code, score=float(r.score) if r.score is not None else None,
                        reason=r.reason,
                        entry_price=float(r.entry_price) if r.entry_price is not None else None,
                        stop_loss=float(r.stop_loss) if r.stop_loss is not None else None,
                        take_profit=float(r.take_profit) if r.take_profit is not None else None,
                        order_status=r.order_status,
                    )
                    for r in previews["buys"]
                ],
                holding_signal_preview=[
                    HoldingSignalPreviewDTO(symbol=r.ts_code, action=r.action, reason=r.reason, order_status=r.order_status)
                    for r in previews["holdings"]
                ],
                suggested_order_preview=[
                    SuggestedOrderPreviewDTO(
                        symbol=r.ts_code, action=r.action,
                        shares=float(r.shares) if r.shares is not None else None,
                        notional=float(r.notional) if r.notional is not None else None,
                        order_cost_price=float(r.order_cost_price) if r.order_cost_price is not None else None,
                        valuation_price=float(r.valuation_price) if r.valuation_price is not None else None,
                        stop_loss=float(r.stop_loss) if r.stop_loss is not None else None,
                        take_profit=float(r.take_profit) if r.take_profit is not None else None,
                        risk_bucket=r.risk_bucket,
                    )
                    for r in previews["orders"]
                ],
                summary=QuantExecutionSummaryDTO(
                    universe_total=int(summary.get("universe_total") or 0),
                    data_complete=int(summary.get("data_complete") or 0),
                    scanned=int(summary.get("scanned") or 0),
                    buy_matches=int(summary.get("buy_matches") or 0),
                    suggested_buy_orders=int(summary.get("suggested_buy_orders") or 0),
                    suggested_sell_orders=int(summary.get("suggested_sell_orders") or 0),
                    failed_count=int(summary.get("failed_count") or 0),
                ),
                warnings=[str(w) for w in (payload.get("warnings") or [])],
                valued_at=payload.get("valued_at"),
                requested_trade_date=payload.get("requested_trade_date"),
                market_as_of_trade_date=payload.get("market_as_of_trade_date"),
            )
        except (TypeError, ValueError, KeyError):
            return None

    quant_execution = _quant_execution()
    sections = [
        ReportSectionDTO(
            block=section.get("block", "decision"),
            status=section.get("status", "UNAVAILABLE"),
            title=section.get("title"),
            summary=section.get("summary"),
            content=section.get("content"),
            charts=section.get("charts"),
            unavailable_reason=section.get("unavailable_reason"),
            retryable=section.get("retryable"),
        )
        for section in report_json.get("sections", [])
    ]
    data = ReportDTO(
        schema_version=report.schema_version,
        report_version=report.report_version,
        generated_at=report.generated_at,
        task=ReportTaskInfo(
            task_id=task_id,
            task_type=TaskType(task.task_type).value,
            ticker=task.ticker,
            effective_trade_date=task.effective_trade_date,
            duration_ms=duration_ms,
        ),
        sections=sections,
        data_sources=report_json.get("data_sources"),
        risk_note=report_json.get("risk_note"),
        quant_execution=quant_execution,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
