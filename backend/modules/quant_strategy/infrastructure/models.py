"""quant_strategy ORM（0008/0009，plan 4.1.1）。

- QuantStrategy：名称唯一，version 为元数据乐观锁（只保护名称/描述）。
- QuantStrategyVersion：(strategy_id, version_no) 唯一；状态 CHECK 仅
  DRAFT/PUBLISHED/ARCHIVED；partial unique index 保证每策略至多一个 DRAFT
  （不声明"当前草稿指针"，当前草稿由唯一 DRAFT 查询）；
  version 为草稿源码乐观锁（与语义版本号 version_no 区分）。
- published_at 不可变（发布时刻，禁止用会被归档更新的 updated_at 代替）；
  archived_at 可空（归档时刻）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.shared.db import Base, TimestampMixin


class QuantStrategy(Base, TimestampMixin):
    __tablename__ = "quant_strategies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    versions: Mapped[list["QuantStrategyVersion"]] = relationship(
        back_populates="strategy", order_by="QuantStrategyVersion.version_no"
    )

    __table_args__ = (UniqueConstraint("name", name="uq_quant_strategies_name"),)


class QuantStrategyVersion(Base, TimestampMixin):
    __tablename__ = "quant_strategy_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quant_strategies.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_code: Mapped[str] = mapped_column(Text(), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_params: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    template_renderer_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lifecycle_policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lifecycle_policy_versions.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    strategy: Mapped[QuantStrategy] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("strategy_id", "version_no", name="uq_quant_strategy_versions_strategy_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_quant_strategy_versions_status",
        ),
        # 每策略至多一个 DRAFT（当前草稿由此唯一 DRAFT 查询，不设"当前草稿指针"列）
        Index(
            "uq_quant_strategy_versions_one_draft",
            "strategy_id",
            unique=True,
            postgresql_where=sa_text("status = 'DRAFT'"),
        ),
    )
