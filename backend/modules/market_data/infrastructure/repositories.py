"""market_data Repository（sync；不 commit）。"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.modules.market_data.infrastructure.models import (
    ConceptHotnessSnapshot,
    MarketAsset,
    MarketBarDaily,
)


class SqlAlchemyMarketUow:
    """market_data 事务边界（sync；Repository 不 commit）。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyMarketUow":
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


class MarketAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_enabled(self) -> list[MarketAsset]:
        stmt = (
            select(MarketAsset)
            .where(MarketAsset.enabled.is_(True))
            .order_by(MarketAsset.display_order.asc(), MarketAsset.id.asc())
        )
        return list(self._session.execute(stmt).scalars())

    def list_all(self) -> list[MarketAsset]:
        """全量目录（管理侧）：enabled=false 资产保留（availability_status=DISABLED 展示语义）。"""
        stmt = select(MarketAsset).order_by(MarketAsset.display_order.asc(), MarketAsset.id.asc())
        return list(self._session.execute(stmt).scalars())

    def get_by_symbol(self, market: str, symbol: str) -> MarketAsset | None:
        return self._session.execute(
            select(MarketAsset).where(MarketAsset.market == market, MarketAsset.symbol == symbol)
        ).scalar_one_or_none()


class MarketBarRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_range(self, asset_id: uuid.UUID, from_date: date, to_date: date) -> list[MarketBarDaily]:
        stmt = (
            select(MarketBarDaily)
            .where(
                MarketBarDaily.asset_id == asset_id,
                MarketBarDaily.trading_date >= from_date,
                MarketBarDaily.trading_date <= to_date,
            )
            .order_by(MarketBarDaily.trading_date.asc())
        )
        return list(self._session.execute(stmt).scalars())

    def latest_date(self, asset_id: uuid.UUID) -> date | None:
        return self._session.execute(
            select(func.max(MarketBarDaily.trading_date)).where(MarketBarDaily.asset_id == asset_id)
        ).scalar_one()

    def latest_source_updated(self, asset_id: uuid.UUID):
        """最近一次入库采集时间（datetime），与 as_of（数据时点）区分。"""
        return self._session.execute(
            select(func.max(MarketBarDaily.source_updated_at)).where(MarketBarDaily.asset_id == asset_id)
        ).scalar_one()

    def upsert(self, asset_id: uuid.UUID, rows: list[dict], source: str) -> int:
        """规范化日线 upsert（ON CONFLICT DO UPDATE：覆盖源端日终修正）。"""
        if not rows:
            return 0
        stmt = pg_insert(MarketBarDaily).values(
            [
                {
                    "asset_id": asset_id,
                    "trading_date": row["trading_date"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row.get("volume"),
                    "source": source,
                    "source_updated_at": row.get("source_updated_at"),
                }
                for row in rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketBarDaily.asset_id, MarketBarDaily.trading_date],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "source": stmt.excluded.source,
                "source_updated_at": stmt.excluded.source_updated_at,
            },
        )
        return self._session.execute(stmt).rowcount


class ConceptSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def latest_as_of(self, market: str) -> date | None:
        return self._session.execute(
            select(func.max(ConceptHotnessSnapshot.as_of_date)).where(ConceptHotnessSnapshot.market == market)
        ).scalar_one()

    def list_by_date(self, market: str, as_of: date, *, limit: int) -> list[ConceptHotnessSnapshot]:
        stmt = (
            select(ConceptHotnessSnapshot)
            .where(ConceptHotnessSnapshot.market == market, ConceptHotnessSnapshot.as_of_date == as_of)
            .order_by(ConceptHotnessSnapshot.rank.asc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())

    def upsert_snapshot(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        stmt = pg_insert(ConceptHotnessSnapshot).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["market", "concept_code", "as_of_date", "algorithm_version"],
            set_={
                "concept_name": stmt.excluded.concept_name,
                "rank": stmt.excluded.rank,
                "score": stmt.excluded.score,
                "hotness_reason": stmt.excluded.hotness_reason,
                "period_return": stmt.excluded.period_return,
                "daily_changes": stmt.excluded.daily_changes,
                "source": stmt.excluded.source,
                "source_updated_at": stmt.excluded.source_updated_at,
            },
        )
        return self._session.execute(stmt).rowcount
