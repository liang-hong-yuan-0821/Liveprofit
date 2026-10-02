"""PortfolioService（§3.1.3：本地组合、持仓的增删改查与数值校验；只操作组合聚合）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from backend.modules.investment_workspace.application.contracts import (
    PortfolioDTO,
    PortfolioPositionDTO,
    PortfolioPositionMutationResult,
)
from backend.modules.investment_workspace.application.errors import (
    InvalidPositionError,
    PortfolioAccountInvalidError,
    PortfolioLedgerRequiredError,
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


_UNSET = object()


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
        max_portfolio_open_risk_pct: float | None = None,
        max_sector_open_risk_pct: float | None = None,
        max_daily_new_risk_pct: float | None = None,
        max_drawdown_pct: float | None = None,
        max_daily_loss_pct: float | None = None,
        net_asset_value: float | None = None,
        peak_net_asset_value: float | None = None,
        day_start_net_asset_value: float | None = None,
        risk_facts_as_of: date | None = None,
        risk_profile: str | None = None,
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
            max_portfolio_open_risk_pct=max_portfolio_open_risk_pct,
            max_sector_open_risk_pct=max_sector_open_risk_pct,
            max_daily_new_risk_pct=max_daily_new_risk_pct,
            max_drawdown_pct=max_drawdown_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            net_asset_value=net_asset_value,
            peak_net_asset_value=peak_net_asset_value,
            day_start_net_asset_value=day_start_net_asset_value,
            risk_facts_as_of=risk_facts_as_of,
            risk_profile=risk_profile,
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
        max_portfolio_open_risk_pct: float,
        max_sector_open_risk_pct: float,
        max_daily_new_risk_pct: float,
        max_drawdown_pct: float,
        max_daily_loss_pct: float,
        net_asset_value: float | None,
        peak_net_asset_value: float | None,
        day_start_net_asset_value: float | None,
        risk_facts_as_of: date | None,
        risk_profile=_UNSET,
        expected_version: int,
    ) -> PortfolioDTO:
        """PATCH 原子更新名称与全部账户字段（plan 4.2.1：一次条件更新、成功仅 version+1）。"""
        portfolio = self._repo.get_locked(portfolio_id)
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
            max_portfolio_open_risk_pct=max_portfolio_open_risk_pct,
            max_sector_open_risk_pct=max_sector_open_risk_pct,
            max_daily_new_risk_pct=max_daily_new_risk_pct,
            max_drawdown_pct=max_drawdown_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            net_asset_value=net_asset_value,
            peak_net_asset_value=peak_net_asset_value,
            day_start_net_asset_value=day_start_net_asset_value,
            risk_facts_as_of=risk_facts_as_of,
            risk_profile=portfolio.risk_profile if risk_profile is _UNSET else risk_profile,
        )
        if self._repo.has_ledger_baseline(portfolio_id) and (
            account["total_assets"] != portfolio.total_assets
            or account["available_cash"] != portfolio.available_cash
        ):
            raise PortfolioLedgerRequiredError("账本基线已建立，现金与总资产须经账户流水或对账调整")
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
        max_portfolio_open_risk_pct: float | None,
        max_sector_open_risk_pct: float | None,
        max_daily_new_risk_pct: float | None,
        max_drawdown_pct: float | None,
        max_daily_loss_pct: float | None,
        net_asset_value: float | None,
        peak_net_asset_value: float | None,
        day_start_net_asset_value: float | None,
        risk_facts_as_of: date | None,
        risk_profile=_UNSET,
    ) -> dict:
        """账户字段校验（Decimal 精度）：0<=cash<=assets、各比例 (0,1]、single<=total、sector<=total、rr>0。

        返回 dict[str, Decimal]（仅非 None 字段；None 字段由 DB 默认值兜底）。
        """
        from decimal import Decimal, InvalidOperation

        def to_decimal(value: float | None) -> Decimal | None:
            if value is None:
                return None
            try:
                result = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise PortfolioAccountInvalidError(f"账户字段不是有效数值：{value}") from None
            if not result.is_finite():
                raise PortfolioAccountInvalidError(f"账户字段不是有限数值：{value}")
            return result

        if risk_profile is not _UNSET and risk_profile not in (None, "CONSERVATIVE", "BALANCED", "AGGRESSIVE"):
            raise PortfolioAccountInvalidError("risk_profile 必须是明确风险档位或空值")

        assets = to_decimal(total_assets)
        cash = to_decimal(available_cash)
        risk = to_decimal(risk_per_trade_pct)
        rr = to_decimal(min_risk_reward_ratio)
        total_pct = to_decimal(max_total_position_pct)
        single_pct = to_decimal(max_single_stock_pct)
        sector_pct = to_decimal(max_sector_pct)
        portfolio_risk_pct = to_decimal(max_portfolio_open_risk_pct)
        sector_risk_pct = to_decimal(max_sector_open_risk_pct)
        daily_risk_pct = to_decimal(max_daily_new_risk_pct)
        drawdown_pct = to_decimal(max_drawdown_pct)
        daily_loss_pct = to_decimal(max_daily_loss_pct)
        nav = to_decimal(net_asset_value)
        peak_nav = to_decimal(peak_net_asset_value)
        day_start_nav = to_decimal(day_start_net_asset_value)

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
            ("max_portfolio_open_risk_pct", portfolio_risk_pct),
            ("max_sector_open_risk_pct", sector_risk_pct),
            ("max_daily_new_risk_pct", daily_risk_pct),
            ("max_drawdown_pct", drawdown_pct),
            ("max_daily_loss_pct", daily_loss_pct),
        ):
            if v is not None:
                check(0 < v <= 1, f"{label} 必须在 (0,1] 区间")
        if rr is not None:
            check(rr > 0, "min_risk_reward_ratio 必须 > 0")
        if single_pct is not None and total_pct is not None:
            check(single_pct <= total_pct, "max_single_stock_pct 必须 <= max_total_position_pct")
        if sector_pct is not None and total_pct is not None:
            check(sector_pct <= total_pct, "max_sector_pct 必须 <= max_total_position_pct")
        if sector_risk_pct is not None and portfolio_risk_pct is not None:
            check(sector_risk_pct <= portfolio_risk_pct, "max_sector_open_risk_pct 必须 <= max_portfolio_open_risk_pct")
        for label, value in (("net_asset_value", nav), ("peak_net_asset_value", peak_nav), ("day_start_net_asset_value", day_start_nav)):
            if value is not None:
                check(value > 0, f"{label} 必须 > 0")
        facts = (nav, peak_nav, day_start_nav, risk_facts_as_of)
        check(all(value is None for value in facts) or all(value is not None for value in facts), "净值风险事实必须四项同时填写或同时留空")
        if nav is not None:
            check(peak_nav >= nav, "peak_net_asset_value 必须 >= net_asset_value")
        values = {
            "total_assets": assets,
            "available_cash": cash,
            "risk_per_trade_pct": risk,
            "min_risk_reward_ratio": rr,
            "max_total_position_pct": total_pct,
            "max_single_stock_pct": single_pct,
            "max_sector_pct": sector_pct,
            "max_portfolio_open_risk_pct": portfolio_risk_pct,
            "max_sector_open_risk_pct": sector_risk_pct,
            "max_daily_new_risk_pct": daily_risk_pct,
            "max_drawdown_pct": drawdown_pct,
            "max_daily_loss_pct": daily_loss_pct,
            "net_asset_value": nav,
            "peak_net_asset_value": peak_nav,
            "day_start_net_asset_value": day_start_nav,
            "risk_facts_as_of": risk_facts_as_of,
            **({} if risk_profile is _UNSET else {"risk_profile": risk_profile}),
        }
        if risk_profile is not _UNSET and risk_profile is not None:
            from backend.modules.investment_workspace.domain.risk_profiles import (
                PROFILES, profile_budget_violations,
            )
            # CREATE may omit numeric fields. A selected tier supplies its
            # frozen ceilings instead of incompatible legacy DB defaults.
            for field, cap in PROFILES[risk_profile].account_caps().items():
                if values[field] is None:
                    values[field] = cap
            excess = profile_budget_violations(risk_profile, values)
            if excess:
                raise PortfolioAccountInvalidError("风险档预算超限或缺失：" + ",".join(excess))
        return values

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
        if self._repo.count_positions(portfolio_id) > 0 or self._repo.has_account_observations(portfolio_id):
            raise PortfolioNotEmptyError("有持仓或账户观察历史的组合不可删除")
        try:
            if not self._repo.conditional_delete(portfolio_id, expected_version):
                raise RevisionConflictError("组合已变更，请重新拉取")
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise PortfolioNotEmptyError("有关联历史的组合不可删除") from None

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
        active_stop_price: float | None = None,
    ) -> PortfolioPositionMutationResult:
        self._validate_position(quantity, average_cost, active_stop_price)
        self._validate_instrument(instrument)
        portfolio = self._repo.get_locked(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        if self._repo.has_ledger_baseline(portfolio_id):
            raise PortfolioLedgerRequiredError("账本基线已建立，持仓须经成交、公司行为或对账调整")
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
                active_stop_price=active_stop_price,
                created_at=now,
                updated_at=now,
            )
            self._positions.add(position)
        else:
            position.quantity = quantity
            position.average_cost = average_cost
            position.active_stop_price = active_stop_price
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
        portfolio = self._repo.get_locked(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError(f"组合不存在：{portfolio_id}")
        if self._repo.has_ledger_baseline(portfolio_id):
            raise PortfolioLedgerRequiredError("账本基线已建立，持仓须经成交、公司行为或对账调整")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            portfolio_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise PortfolioPositionConflictError("持仓已变更，请重新读取")
        if not self._positions.delete_by_instrument(portfolio_id, instrument.market, instrument.symbol):
            raise PortfolioNotFoundError(f"持仓不存在：{instrument.market} {instrument.symbol}")
        self._uow.commit()

    @staticmethod
    def _validate_position(quantity: float, average_cost: float, active_stop_price: float | None = None) -> None:
        if quantity <= 0:
            raise InvalidPositionError("quantity 必须大于 0")
        if average_cost < 0:
            raise InvalidPositionError("average_cost 不得小于 0")
        if active_stop_price is not None and active_stop_price <= 0:
            raise InvalidPositionError("active_stop_price 必须大于 0")

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
            max_portfolio_open_risk_pct=float(portfolio.max_portfolio_open_risk_pct),
            max_sector_open_risk_pct=float(portfolio.max_sector_open_risk_pct),
            max_daily_new_risk_pct=float(portfolio.max_daily_new_risk_pct),
            max_drawdown_pct=float(portfolio.max_drawdown_pct),
            max_daily_loss_pct=float(portfolio.max_daily_loss_pct),
            net_asset_value=float(portfolio.net_asset_value) if portfolio.net_asset_value is not None else None,
            peak_net_asset_value=float(portfolio.peak_net_asset_value) if portfolio.peak_net_asset_value is not None else None,
            day_start_net_asset_value=float(portfolio.day_start_net_asset_value) if portfolio.day_start_net_asset_value is not None else None,
            risk_facts_as_of=portfolio.risk_facts_as_of,
            risk_profile=portfolio.risk_profile,
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
            active_stop_price=float(position.active_stop_price) if position.active_stop_price is not None else None,
            updated_at=position.updated_at,
        )
