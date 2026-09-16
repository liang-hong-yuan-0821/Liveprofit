"""quant_strategy 仓储与事务边界（plan 4.1.1）：策略聚合与版本子资源。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


class SqlAlchemyQuantStrategyUnitOfWork:
    """quant_strategy 事务边界（sync；Repository 不 commit）。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyQuantStrategyUnitOfWork":
        self.session = self._session_factory()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None

    def commit(self) -> None:
        assert self.session is not None, "UoW 未进入上下文"
        self.session.commit()

    def rollback(self) -> None:
        assert self.session is not None, "UoW 未进入上下文"
        self.session.rollback()


class QuantStrategyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, strategy: QuantStrategy) -> None:
        self._session.add(strategy)

    def get(self, strategy_id: uuid.UUID) -> QuantStrategy | None:
        return self._session.get(QuantStrategy, strategy_id)

    def get_by_name(self, name: str) -> QuantStrategy | None:
        return self._session.scalar(select(QuantStrategy).where(QuantStrategy.name == name))

    def list_all(self) -> list[QuantStrategy]:
        return list(
            self._session.scalars(select(QuantStrategy).order_by(QuantStrategy.created_at, QuantStrategy.id))
        )

    def conditional_update_version(
        self, strategy_id: uuid.UUID, expected_version: int, values: dict
    ) -> bool:
        """元数据乐观锁：仅当 version 匹配才更新并 +1。"""
        from sqlalchemy import update

        result = self._session.execute(
            update(QuantStrategy)
            .where(QuantStrategy.id == strategy_id, QuantStrategy.version == expected_version)
            .values(**values)
        )
        return result.rowcount == 1


class QuantStrategyVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, version: QuantStrategyVersion) -> None:
        self._session.add(version)

    def get(self, version_id: uuid.UUID) -> QuantStrategyVersion | None:
        return self._session.get(QuantStrategyVersion, version_id)

    def list_by_strategy(self, strategy_id: uuid.UUID) -> list[QuantStrategyVersion]:
        return list(
            self._session.scalars(
                select(QuantStrategyVersion)
                .where(QuantStrategyVersion.strategy_id == strategy_id)
                .order_by(QuantStrategyVersion.version_no.desc())
            )
        )

    def get_draft(self, strategy_id: uuid.UUID) -> QuantStrategyVersion | None:
        """当前草稿：每策略至多一个 DRAFT（partial unique index 保证）。"""
        return self._session.scalar(
            select(QuantStrategyVersion).where(
                QuantStrategyVersion.strategy_id == strategy_id,
                QuantStrategyVersion.status == "DRAFT",
            )
        )

    def count_published(self, strategy_id: uuid.UUID) -> int:
        from sqlalchemy import func

        return self._session.scalar(
            select(func.count())
            .select_from(QuantStrategyVersion)
            .where(
                QuantStrategyVersion.strategy_id == strategy_id,
                QuantStrategyVersion.status == "PUBLISHED",
            )
        ) or 0

    def next_version_no(self, strategy_id: uuid.UUID) -> int:
        from sqlalchemy import func

        return (
            self._session.scalar(
                select(func.max(QuantStrategyVersion.version_no)).where(
                    QuantStrategyVersion.strategy_id == strategy_id
                )
            )
            or 0
        ) + 1

    def conditional_update_version(
        self, version_id: uuid.UUID, expected_version: int, values: dict
    ) -> bool:
        """草稿源码乐观锁：仅当 version 匹配才更新并 +1。"""
        from sqlalchemy import update

        result = self._session.execute(
            update(QuantStrategyVersion)
            .where(QuantStrategyVersion.id == version_id, QuantStrategyVersion.version == expected_version)
            .values(**values)
        )
        return result.rowcount == 1
