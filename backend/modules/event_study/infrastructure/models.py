"""event_study ORM：macro_information（已审核事件研究宏观信息投影，§2.5）。

仅返回 review_status=APPROVED 的投影；不从预测响应或报告文本拼装。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base, TimestampMixin


class MacroInformation(Base, TimestampMixin):
    __tablename__ = "macro_information"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 既有事件研究 events.id（有则关联）
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    macro_topic: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    related_assets: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")  # PENDING/APPROVED/REJECTED
    research_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 展示投影字段

    __table_args__ = (
        Index("ix_macro_information_review_occurred", "review_status", "occurred_at"),
    )
