"""investment_workspace Repository（sync；不 commit，事务由 Application Service 控制）。

两个聚合各自独立 Repository，禁止跨聚合访问。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from backend.modules.investment_workspace.infrastructure.models import (
    Portfolio,
    PortfolioPosition,
    Watchlist,
    WatchlistItem,
)


class WatchlistRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, watchlist: Watchlist) -> None:
        self._session.add(watchlist)

    def get(self, watchlist_id: uuid.UUID) -> Watchlist | None:
        return self._session.get(Watchlist, watchlist_id)

    def get_by_name(self, name: str) -> Watchlist | None:
        return self._session.execute(select(Watchlist).where(Watchlist.name == name)).scalar_one_or_none()

    def list_ordered(self, *, limit: int, before: tuple[datetime, uuid.UUID] | None = None) -> list[Watchlist]:
        stmt = select(Watchlist)
        if before is not None:
            cu, cid = before
            stmt = stmt.where(
                or_(
                    Watchlist.updated_at < cu,
                    and_(Watchlist.updated_at == cu, Watchlist.id < cid),
                )
            )
        stmt = stmt.order_by(Watchlist.updated_at.desc(), Watchlist.id.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars())

    def conditional_update_version(self, watchlist_id: uuid.UUID, expected_version: int, changes: dict) -> bool:
        stmt = (
            update(Watchlist)
            .where(Watchlist.id == watchlist_id, Watchlist.version == expected_version)
            .values(**changes)
        )
        return self._session.execute(stmt).rowcount > 0

    def conditional_delete(self, watchlist_id: uuid.UUID, expected_version: int) -> bool:
        stmt = delete(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.version == expected_version)
        return self._session.execute(stmt).rowcount > 0

    def count_items(self, watchlist_id: uuid.UUID) -> int:
        return self._session.execute(
            select(func.count()).select_from(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist_id)
        ).scalar_one()


class WatchlistItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, item: WatchlistItem) -> None:
        self._session.add(item)

    def get(self, item_id: uuid.UUID) -> WatchlistItem | None:
        return self._session.get(WatchlistItem, item_id)

    def get_by_instrument(self, watchlist_id: uuid.UUID, market: str, symbol: str) -> WatchlistItem | None:
        return self._session.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.market == market,
                WatchlistItem.symbol == symbol,
            )
        ).scalar_one_or_none()

    def list_ordered(self, watchlist_id: uuid.UUID) -> list[WatchlistItem]:
        stmt = (
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.display_order.asc(), WatchlistItem.id.asc())
        )
        return list(self._session.execute(stmt).scalars())

    def delete_by_id(self, item_id: uuid.UUID) -> bool:
        stmt = delete(WatchlistItem).where(WatchlistItem.id == item_id)
        return self._session.execute(stmt).rowcount > 0


class PortfolioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, portfolio: Portfolio) -> None:
        self._session.add(portfolio)

    def get(self, portfolio_id: uuid.UUID) -> Portfolio | None:
        return self._session.get(Portfolio, portfolio_id)

    def get_by_name(self, name: str) -> Portfolio | None:
        return self._session.execute(select(Portfolio).where(Portfolio.name == name)).scalar_one_or_none()

    def list_ordered(self, *, limit: int, before: tuple[datetime, uuid.UUID] | None = None) -> list[Portfolio]:
        stmt = select(Portfolio)
        if before is not None:
            cu, cid = before
            stmt = stmt.where(
                or_(
                    Portfolio.updated_at < cu,
                    and_(Portfolio.updated_at == cu, Portfolio.id < cid),
                )
            )
        stmt = stmt.order_by(Portfolio.updated_at.desc(), Portfolio.id.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars())

    def conditional_update_version(self, portfolio_id: uuid.UUID, expected_version: int, changes: dict) -> bool:
        stmt = (
            update(Portfolio)
            .where(Portfolio.id == portfolio_id, Portfolio.version == expected_version)
            .values(**changes)
        )
        return self._session.execute(stmt).rowcount > 0

    def conditional_delete(self, portfolio_id: uuid.UUID, expected_version: int) -> bool:
        stmt = delete(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.version == expected_version)
        return self._session.execute(stmt).rowcount > 0

    def count_positions(self, portfolio_id: uuid.UUID) -> int:
        return self._session.execute(
            select(func.count()).select_from(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio_id)
        ).scalar_one()


class SqlAlchemyWorkspaceUnitOfWork:
    """investment_workspace 事务边界（sync；Repository 不 commit）。"""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyWorkspaceUnitOfWork":
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


class PortfolioPositionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, position: PortfolioPosition) -> None:
        self._session.add(position)

    def get_by_instrument(self, portfolio_id: uuid.UUID, market: str, symbol: str) -> PortfolioPosition | None:
        return self._session.execute(
            select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == portfolio_id,
                PortfolioPosition.market == market,
                PortfolioPosition.symbol == symbol,
            )
        ).scalar_one_or_none()

    def list_ordered(self, portfolio_id: uuid.UUID) -> list[PortfolioPosition]:
        stmt = (
            select(PortfolioPosition)
            .where(PortfolioPosition.portfolio_id == portfolio_id)
            .order_by(PortfolioPosition.market.asc(), PortfolioPosition.symbol.asc())
        )
        return list(self._session.execute(stmt).scalars())

    def delete_by_instrument(self, portfolio_id: uuid.UUID, market: str, symbol: str) -> bool:
        stmt = delete(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.market == market,
            PortfolioPosition.symbol == symbol,
        )
        return self._session.execute(stmt).rowcount > 0
