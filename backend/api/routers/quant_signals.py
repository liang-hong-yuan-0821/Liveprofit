"""量化信号 cursor endpoint（plan 4.3.1/4.4.1）。

- 结果按 attempt 隔离：未指定 attempt_no 时解析任务最新成功 attempt
  （= analysis_reports 存在该 attempt_no 的报告行）；指定时必须属于该 task。
- BUY cursor 固定 (score DESC, ts_code ASC, id ASC) 编码三个值；持仓审计/订单/
  错误样本以各自唯一稳定排序键编码——禁止复用仅支持 (datetime, UUID) 的任务列表 cursor。
- cursor payload 含 kind/attempt_no，与请求不一致即拒绝。
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.problem import ProblemError
from backend.api.schemas.quant_execution import QuantSignalPageData, QuantSignalRowDTO

router = APIRouter(prefix="/api/v1", tags=["quant-signals"])

KIND_KEYS = {
    "buy": ("score", "ts_code", "id"),
    "holding": ("ts_code", "id"),
    "orders": ("id",),
    "errors": ("ts_code", "id"),
}

PREVIEW_LIMIT = 50


def encode_quant_cursor(kind: str, attempt_no: int, keys: dict) -> str:
    payload = json.dumps({"k": kind, "a": attempt_no, "v": keys}, sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_quant_cursor(raw: str, *, expected_kind: str, expected_attempt: int) -> dict:
    """解码失败/kind 或 attempt_no 与请求不一致 → ValueError（Router 映射 422）。"""
    padded = raw + "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if payload.get("k") != expected_kind:
        raise ValueError("cursor kind 与请求不一致")
    if payload.get("a") != expected_attempt:
        raise ValueError("cursor attempt_no 与请求不一致")
    keys = payload.get("v") or {}
    allowed = set(KIND_KEYS[expected_kind])
    if not set(keys) <= allowed:
        raise ValueError("cursor 键不合法")
    return keys


@router.get("/analysis-tasks/{task_id}/quant-signals", response_model=Envelope[QuantSignalPageData])
async def list_quant_signals(
    task_id: uuid.UUID,
    request: Request,
    kind: Literal["buy", "holding", "orders", "errors"] = Query(default="buy"),
    attempt_no: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    if cursor is not None and attempt_no is None:
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 请求必须同时指定 attempt_no")

    def _do():
        with services.open() as bundle:
            from backend.modules.analysis.application.errors import ReportNotFoundError
            from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignalRepository

            report = bundle.reports.get_latest_report(bundle.uow, task_id)
            resolved_attempt = attempt_no if attempt_no is not None else report.attempt_no
            after = None
            if cursor is not None:
                try:
                    after = decode_quant_cursor(cursor, expected_kind=kind, expected_attempt=resolved_attempt)
                except (ValueError, TypeError, KeyError):
                    raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None
            repo = QuantExecutionSignalRepository(bundle.uow.session)
            try:
                rows = repo.page_by_kind(task_id, resolved_attempt, kind, after=after, limit=limit + 1)
            except (ValueError, TypeError):
                # cursor 值类型非法（如伪造 score="abc"）：拒绝而非 500
                raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None
            has_more = len(rows) > limit
            rows = rows[:limit]
            next_keys = None
            if has_more:
                last = rows[-1]
                next_keys = {k: getattr(last, k) for k in KIND_KEYS[kind]}
                next_keys = {k: _jsonable(v) for k, v in next_keys.items()}
            return rows, resolved_attempt, next_keys

    rows, resolved_attempt, next_keys = await services.run(_do)
    items = [
        QuantSignalRowDTO(
            id=r.id, ts_code=r.ts_code, signal_kind=r.signal_kind, attempt_no=resolved_attempt,
            action=r.action, score=float(r.score) if r.score is not None else None,
            reason=r.reason,
            entry_price=float(r.entry_price) if r.entry_price is not None else None,
            stop_loss=float(r.stop_loss) if r.stop_loss is not None else None,
            take_profit=float(r.take_profit) if r.take_profit is not None else None,
            sell_ratio=float(r.sell_ratio) if r.sell_ratio is not None else None,
            order_status=r.order_status,
            shares=float(r.shares) if r.shares is not None else None,
            notional=float(r.notional) if r.notional is not None else None,
            order_cost_price=float(r.order_cost_price) if r.order_cost_price is not None else None,
            valuation_price=float(r.valuation_price) if r.valuation_price is not None else None,
            signal_trade_date=r.signal_trade_date,
            signal_price_basis=r.signal_price_basis,
            execution_price_basis=r.execution_price_basis,
            adj_factor_version=r.adj_factor_version,
            execution_market=r.execution_market,
            order_entry_price=float(r.order_entry_price) if r.order_entry_price is not None else None,
            order_stop_price=float(r.order_stop_price) if r.order_stop_price is not None else None,
            order_take_price=float(r.order_take_price) if r.order_take_price is not None else None,
            earliest_execution_trade_date=r.earliest_execution_trade_date,
            available_sell_quantity=float(r.available_sell_quantity) if r.available_sell_quantity is not None else None,
            estimated_fees=float(r.estimated_fees) if r.estimated_fees is not None else None,
            estimated_slippage=float(r.estimated_slippage) if r.estimated_slippage is not None else None,
            execution_policy_version=r.execution_policy_version,
            risk_bucket=r.risk_bucket, error_code=r.error_code,
        )
        for r in rows
    ]
    next_cursor = (
        encode_quant_cursor(kind, resolved_attempt, next_keys) if next_keys is not None else None
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(
        data=QuantSignalPageData(items=items, next_cursor=next_cursor, attempt_no=resolved_attempt, kind=kind),
        meta=meta,
    ).model_dump()


def _jsonable(value):
    from decimal import Decimal

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
