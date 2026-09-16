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
    PortfolioAccountInvalidError,
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

    def create(
        self,
        name: str,
        *,
        total_assets: float | None = None,
        available_cash: float | None = None,
        risk_per_trade_pct: float | None = None,
        min_risk_reward_ratio: float | None = None,
        max_total_position_pct: float | None = None,
        max_single_stock_pct: float | None = None,
        max_sector_pct: float | None = None,
    ) -> PortfolioDTO:
        if self._repo.get_by_name(name) is not None:
            raise PortfolioNameConflictError(f"组合名已存在：{name}")
        # 接受完整账户配置（也接受默认参数）；None 字段落 DB 默认值
        account = self._validate_account(
            total_assets=total_assets,
            available_cash=available_cash,
            risk_per_trade_pct=risk_per_trade_pct,
            min_risk_reward_ratio=min_risk_reward_ratio,
            max_total_position_pct=max_total_position_pct,
            max_single_stock_pct=max_single_stock_pct,
            max_sector_pct=max_sector_pct,
        )
        now = self._clock.now()
        portfolio = Portfolio(id=new_uuid(), name=name, version=1, created_at=now, updated_at=now, **account)
        self._repo.add(portfolio)
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise PortfolioNameConflictError(f"组合名已存在：{name}") from None
        return self._to_portfolio_dto(portfolio, 0)

    def update_account(
        self,
        portfolio_id: uuid.UUID,
        *,
        name: str,
        total_assets: float,
        available_cash: float,
        risk_per_trade_pct: float,
        min_risk_reward_ratio: float,
        max_total_position_pct: float,
        max_single_stock_pct: float,
        max_sector_pct: float,
        expected_version: int,
    ) -> PortfolioDTO:
        """PATCH 原子更新名称与全部账户字段（plan 4.2.1：一次条件更新、成功仅 version+1）。"""
        portfolio = self._repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        if self._repo.get_by_name(name) is not None and name != portfolio.name:
            raise PortfolioNameConflictError(f"组合名已存在：{name}")
        account = self._validate_account(
            total_assets=total_assets,
            available_cash=available_cash,
            risk_per_trade_pct=risk_per_trade_pct,
            min_risk_reward_ratio=min_risk_reward_ratio,
            max_total_position_pct=max_total_position_pct,
            max_single_stock_pct=max_single_stock_pct,
            max_sector_pct=max_sector_pct,
        )
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            portfolio_id,
            expected_version,
            {"name": name, "version": expected_version + 1, "updated_at": now, **account},
        ):
            raise RevisionConflictError("组合已变更，请重新拉取")
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise PortfolioNameConflictError(f"组合名已存在：{name}") from None
        return self.get(portfolio_id)

    @staticmethod
    def _validate_account(
        *,
        total_assets: float | None,
        available_cash: float | None,
        risk_per_trade_pct: float | None,
        min_risk_reward_ratio: float | None,
        max_total_position_pct: float | None,
        max_single_stock_pct: float | None,
        max_sector_pct: float | None,
    ) -> dict:
        """账户字段校验（Decimal 精度）：0<=cash<=assets、各比例 (0,1]、single<=total、sector<=total、rr>0。

        返回 dict[str, Decimal]（仅非 None 字段；None 字段由 DB 默认值兜底）。
        """
        from decimal import Decimal, InvalidOperation

        def to_decimal(value: float | None) -> Decimal | None:
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except InvalidOperation:
                raise PortfolioAccountInvalidError(f"账户字段不是有效数值：{value}") from None

        assets = to_decimal(total_assets)
        cash = to_decimal(available_cash)
        risk = to_decimal(risk_per_trade_pct)
        rr = to_decimal(min_risk_reward_ratio)
        total_pct = to_decimal(max_total_position_pct)
        single_pct = to_decimal(max_single_stock_pct)
        sector_pct = to_decimal(max_sector_pct)

        def check(condition: bool, message: str) -> None:
            if not condition:
                raise PortfolioAccountInvalidError(message)

        # 单字段区间（跨字段关系由服务复验，与 DB CHECK 分工一致）
        if assets is not None:
            check(assets >= 0, "total_assets 必须非负")
        if cash is not None:
            check(cash >= 0, "available_cash 必须非负")
        if assets is not None and cash is not None:
            check(cash <= assets, "available_cash 必须 <= total_assets")
        for label, v in (
            ("risk_per_trade_pct", risk),
            ("max_total_position_pct", total_pct),
            ("max_single_stock_pct", single_pct),
            ("max_sector_pct", sector_pct),
        ):
            if v is not None:
                check(0 < v <= 1, f"{label} 必须在 (0,1] 区间")
        if rr is not None:
            check(rr > 0, "min_risk_reward_ratio 必须 > 0")
        if single_pct is not None and total_pct is not None:
            check(single_pct <= total_pct, "max_single_stock_pct 必须 <= max_total_position_pct")
        if sector_pct is not None and total_pct is not None:
            check(sector_pct <= total_pct, "max_sector_pct 必须 <= max_total_position_pct")
        return {
            "total_assets": assets,
            "available_cash": cash,
            "risk_per_trade_pct": risk,
            "min_risk_reward_ratio": rr,
            "max_total_position_pct": total_pct,
            "max_single_stock_pct": single_pct,
            "max_sector_pct": sector_pct,
        }

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
            total_assets=float(portfolio.total_assets),
            available_cash=float(portfolio.available_cash),
            risk_per_trade_pct=float(portfolio.risk_per_trade_pct),
            min_risk_reward_ratio=float(portfolio.min_risk_reward_ratio),
            max_total_position_pct=float(portfolio.max_total_position_pct),
            max_single_stock_pct=float(portfolio.max_single_stock_pct),
            max_sector_pct=float(portfolio.max_sector_pct),
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
