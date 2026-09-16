"""quant_execution_signals ORM 与仓储（0009，plan 4.3.1）。

- id 表级自增主键；(task_id, attempt_no, id) 归属执行批次；
- signal_kind 判别行类型（BUY/持仓信号/错误样本），order 可空列组仅属 BUY/SELL 行；
- 复合索引 (task_id, attempt_no, score DESC, ts_code, id) 支撑 cursor；
- FK 随 task CASCADE。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend.shared.db import Base


class QuantExecutionSignal(Base):
    __tablename__ = "quant_execution_signals"

    id: Mapped[int] = mapped_column(BigInteger(), primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY / HOLDING / ERROR
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    # 脚本七键价格字段：仅 BUY 行有值
    entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    sell_ratio: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    # 系统回写：风控结论 + 建议订单列组（仅 ELIGIBLE 及卖出订单行有值）
    order_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shares: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    notional: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    order_cost_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    valuation_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    risk_bucket: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    # 错误样本行
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("signal_kind IN ('BUY', 'HOLDING', 'ERROR')", name="ck_quant_execution_signals_kind"),
        Index(
            "ix_quant_execution_signals_cursor",
            "task_id",
            "attempt_no",
            sa_text("score DESC"),
            "ts_code",
            "id",
        ),
        Index("ix_quant_execution_signals_task_attempt", "task_id", "attempt_no"),
    )


class QuantExecutionSignalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, signal: QuantExecutionSignal) -> None:
        self._session.add(signal)

    def add_all(self, signals: list[QuantExecutionSignal]) -> None:
        self._session.add_all(signals)

    def list_actionable(self, task_id: uuid.UUID, attempt_no: int):
        """PositionPlanner 用：当前 attempt 的可行动 signal 流式读取，
        全局排序 score DESC, ts_code ASC, id ASC。"""
        from sqlalchemy import select

        return self._session.scalars(
            select(QuantExecutionSignal)
            .where(
                QuantExecutionSignal.task_id == task_id,
                QuantExecutionSignal.attempt_no == attempt_no,
                QuantExecutionSignal.signal_kind.in_(["BUY", "HOLDING"]),
                QuantExecutionSignal.error_code.is_(None),
            )
            .order_by(
                QuantExecutionSignal.score.desc(),
                QuantExecutionSignal.ts_code.asc(),
                QuantExecutionSignal.id.asc(),
            )
        )

    def list_holding_signals(self, task_id: uuid.UUID, attempt_no: int):
        from sqlalchemy import select

        return self._session.scalars(
            select(QuantExecutionSignal)
            .where(
                QuantExecutionSignal.task_id == task_id,
                QuantExecutionSignal.attempt_no == attempt_no,
                QuantExecutionSignal.signal_kind == "HOLDING",
            )
            .order_by(QuantExecutionSignal.ts_code.asc(), QuantExecutionSignal.id.asc())
        )

    def update_order_fields(self, signal_id: int, values: dict) -> None:
        from sqlalchemy import update

        self._session.execute(
            update(QuantExecutionSignal).where(QuantExecutionSignal.id == signal_id).values(**values)
        )

    # ---- cursor 分页（plan 4.3.1 排序契约） ----

    def page_by_kind(
        self,
        task_id: uuid.UUID,
        attempt_no: int,
        kind: str,
        *,
        after: dict | None,
        limit: int,
    ) -> list[QuantExecutionSignal]:
        from sqlalchemy import and_, or_, select

        q = select(QuantExecutionSignal).where(
            QuantExecutionSignal.task_id == task_id,
            QuantExecutionSignal.attempt_no == attempt_no,
        )
        if kind == "buy":
            q = q.where(QuantExecutionSignal.signal_kind == "BUY").order_by(
                QuantExecutionSignal.score.desc(), QuantExecutionSignal.ts_code.asc(), QuantExecutionSignal.id.asc()
            )
            if after:
                score = after.get("score")
                ts = after.get("ts_code")
                after_id = after.get("id")
                if score is not None and ts is not None and after_id is not None:
                    q = q.where(
                        or_(
                            QuantExecutionSignal.score < float(score),
                            and_(
                                QuantExecutionSignal.score == float(score),
                                QuantExecutionSignal.ts_code > ts,
                            ),
                            and_(
                                QuantExecutionSignal.score == float(score),
                                QuantExecutionSignal.ts_code == ts,
                                QuantExecutionSignal.id > int(after_id),
                            ),
                        )
                    )
        elif kind == "holding":
            q = q.where(QuantExecutionSignal.signal_kind == "HOLDING").order_by(
                QuantExecutionSignal.ts_code.asc(), QuantExecutionSignal.id.asc()
            )
            if after and after.get("ts_code") is not None and after.get("id") is not None:
                q = q.where(
                    or_(
                        QuantExecutionSignal.ts_code > after["ts_code"],
                        and_(
                            QuantExecutionSignal.ts_code == after["ts_code"],
                            QuantExecutionSignal.id > int(after["id"]),
                        ),
                    )
                )
        elif kind == "orders":
            q = q.where(
                QuantExecutionSignal.signal_kind.in_(["BUY", "HOLDING"]),
                QuantExecutionSignal.order_status.isnot(None),
                QuantExecutionSignal.shares.isnot(None),
            ).order_by(QuantExecutionSignal.id.asc())
            if after and after.get("id") is not None:
                q = q.where(QuantExecutionSignal.id > int(after["id"]))
        elif kind == "errors":
            q = q.where(QuantExecutionSignal.signal_kind == "ERROR").order_by(
                QuantExecutionSignal.ts_code.asc(), QuantExecutionSignal.id.asc()
            )
            if after and after.get("ts_code") is not None and after.get("id") is not None:
                q = q.where(
                    or_(
                        QuantExecutionSignal.ts_code > after["ts_code"],
                        and_(
                            QuantExecutionSignal.ts_code == after["ts_code"],
                            QuantExecutionSignal.id > int(after["id"]),
                        ),
                    )
                )
        else:
            raise ValueError(f"非法 kind: {kind}")
        return list(self._session.scalars(q.limit(limit)))

    def top_previews(self, task_id: uuid.UUID, attempt_no: int, *, limit: int = 50) -> dict:
        """报告 DTO 预览（plan 4.4.1：首 50 条 BUY/持仓信号/建议订单，禁止无界数组）。"""
        from sqlalchemy import select

        buys = list(
            self._session.scalars(
                select(QuantExecutionSignal)
                .where(
                    QuantExecutionSignal.task_id == task_id,
                    QuantExecutionSignal.attempt_no == attempt_no,
                    QuantExecutionSignal.signal_kind == "BUY",
                )
                .order_by(QuantExecutionSignal.score.desc(), QuantExecutionSignal.ts_code.asc())
                .limit(limit)
            )
        )
        holdings = list(
            self._session.scalars(
                select(QuantExecutionSignal)
                .where(
                    QuantExecutionSignal.task_id == task_id,
                    QuantExecutionSignal.attempt_no == attempt_no,
                    QuantExecutionSignal.signal_kind == "HOLDING",
                )
                .order_by(QuantExecutionSignal.ts_code.asc())
                .limit(limit)
            )
        )
        orders = list(
            self._session.scalars(
                select(QuantExecutionSignal)
                .where(
                    QuantExecutionSignal.task_id == task_id,
                    QuantExecutionSignal.attempt_no == attempt_no,
                    QuantExecutionSignal.order_status.isnot(None),
                    QuantExecutionSignal.shares.isnot(None),
                )
                .order_by(QuantExecutionSignal.id.asc())
                .limit(limit)
            )
        )
        return {"buys": buys, "holdings": holdings, "orders": orders}
