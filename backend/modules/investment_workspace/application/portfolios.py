"""PortfolioService（§3.1.3：本地组合、持仓的增删改查与数值校验；只操作组合聚合）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from backend.modules.investment_workspace.application.contracts import (
    PortfolioDTO,
    PortfolioPositionDTO,
    PortfolioPositionMutationResult,
)
from backend.modules.investment_workspace.application.errors import (
    InvalidPositionError,
    PortfolioNameConflictError,
    PortfolioNotEmptyError,
    PortfolioNotFoundError,
    PortfolioPositionConflictError,
    RevisionConflictError,
)
from backend.modules.investment_workspace.domain.values import InstrumentRef, is_valid_instrument
from sqlalchemy.exc import IntegrityError

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.investment_workspace.infrastructure.repositories import (
    PortfolioPositionRepository,
    PortfolioRepository,
)
from backend.shared.clock import Clock, SystemClock
from backend.shared.ids import new_uuid


class PortfolioService:
    def __init__(self, uow, *, clock: Clock | None = None) -> None:
        self._uow = uow
        self._repo = PortfolioRepository(uow.session)
        self._positions = PortfolioPositionRepository(uow.session)
        self._clock = clock or SystemClock()

    # ---- 组合 ----

    def create(self, name: str) -> PortfolioDTO:
        if self._repo.get_by_name(name) is not None:
            raise PortfolioNameConflictError(f"组合名已存在：{name}")
        now = self._clock.now()
        portfolio = Portfolio(id=new_uuid(), name=name, version=1, created_at=now, updated_at=now)
        self._repo.add(portfolio)
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise PortfolioNameConflictError(f"组合名已存在：{name}") from None
        return self._to_portfolio_dto(portfolio, 0)

    def list(self, *, limit: int, before: tuple[datetime, uuid.UUID] | None = None) -> tuple[list[PortfolioDTO], tuple[datetime, uuid.UUID] | None]:
        rows = self._repo.list_ordered(limit=limit + 1, before=before)
        items = [self._to_portfolio_dto(p, self._repo.count_positions(p.id)) for p in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = (last.updated_at, last.id)
        return items, next_cursor

    def get(self, portfolio_id: uuid.UUID) -> PortfolioDTO:
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        return self._to_portfolio_dto(portfolio, self._repo.count_positions(portfolio_id))

    def rename(self, portfolio_id: uuid.UUID, name: str, expected_version: int) -> PortfolioDTO:
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        if self._repo.get_by_name(name) is not None and name != portfolio.name:
            raise PortfolioNameConflictError(f"组合名已存在：{name}")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            portfolio_id, expected_version, {"name": name, "version": expected_version + 1, "updated_at": now}
        ):
            raise RevisionConflictError("组合已变更，请重新拉取")
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise PortfolioNameConflictError(f"组合名已存在：{name}") from None
        return self.get(portfolio_id)

    def delete(self, portfolio_id: uuid.UUID, expected_version: int) -> None:
        if self._repo.get(portfolio_id) is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        if self._repo.count_positions(portfolio_id) > 0:
            raise PortfolioNotEmptyError("仅允许删除空组合")
        if not self._repo.conditional_delete(portfolio_id, expected_version):
            raise RevisionConflictError("组合已变更，请重新拉取")
        self._uow.commit()

    # ---- 持仓 ----

    def list_positions(self, portfolio_id: uuid.UUID) -> tuple[list[PortfolioPositionDTO], int]:
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        rows = self._positions.list_ordered(portfolio_id)
        return [self._to_position_dto(p) for p in rows], portfolio.version

    def upsert_position(
        self,
        portfolio_id: uuid.UUID,
        instrument: InstrumentRef,
        quantity: float,
        average_cost: float,
        expected_revision: int,
    ) -> PortfolioPositionMutationResult:
        self._validate_position(quantity, average_cost)
        self._validate_instrument(instrument)
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            portfolio_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise PortfolioPositionConflictError("持仓已变更，请重新读取")
        position = self._positions.get_by_instrument(portfolio_id, instrument.market, instrument.symbol)
        if position is None:
            position = PortfolioPosition(
                id=new_uuid(),
                portfolio_id=portfolio_id,
                market=instrument.market,
                symbol=instrument.symbol,
                quantity=quantity,
                average_cost=average_cost,
                created_at=now,
                updated_at=now,
            )
            self._positions.add(position)
        else:
            position.quantity = quantity
            position.average_cost = average_cost
            position.updated_at = now
        try:
            self._uow.commit()
        except IntegrityError:
            # 预检通过后并发撞 (portfolio_id, market, symbol) 唯一约束
            self._uow.rollback()
            raise PortfolioPositionConflictError("持仓已变更，请重新读取") from None
        return PortfolioPositionMutationResult(
            position=self._to_position_dto(position), portfolio_revision=expected_revision + 1
        )

    def remove_position(self, portfolio_id: uuid.UUID, instrument: InstrumentRef, expected_revision: int) -> None:
        self._validate_instrument(instrument)
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            portfolio_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise PortfolioPositionConflictError("持仓已变更，请重新读取")
        if not self._positions.delete_by_instrument(portfolio_id, instrument.market, instrument.symbol):
            raise PortfolioNotFoundError(f"持仓不存在：{instrument.market} {instrument.symbol}")
        self._uow.commit()

    @staticmethod
    def _validate_position(quantity: float, average_cost: float) -> None:
        if quantity <= 0:
            raise InvalidPositionError("quantity 必须大于 0")
        if average_cost < 0:
            raise InvalidPositionError("average_cost 不得小于 0")

    @staticmethod
    def _validate_instrument(instrument: InstrumentRef) -> None:
        """写库前校验（契约 422 INVALID_POSITION）：不得先落库再报错。"""
        if not is_valid_instrument(instrument):
            raise InvalidPositionError(f"非法 market/symbol：{instrument.market} {instrument.symbol}")

    # ---- 投影 ----

    @staticmethod
    def _to_portfolio_dto(portfolio: Portfolio, position_count: int) -> PortfolioDTO:
        return PortfolioDTO(
            id=portfolio.id,
            name=portfolio.name,
            version=portfolio.version,
            position_count=position_count,
            created_at=portfolio.created_at,
            updated_at=portfolio.updated_at,
        )

    @staticmethod
    def _to_position_dto(position: PortfolioPosition) -> PortfolioPositionDTO:
        return PortfolioPositionDTO(
            portfolio_id=position.portfolio_id,
            market=position.market,
            symbol=position.symbol,
            quantity=float(position.quantity),
            average_cost=float(position.average_cost),
            updated_at=position.updated_at,
        )
