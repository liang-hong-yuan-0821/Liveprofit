# test-catalog-begin
# {
#   "purpose": "investment_workspace 集成测试（真实 PG：revision 并发、批量排序原子性、校验）。",
#   "keywords": [
#     "投资工作区",
#     "持仓生命周期",
#     "投资组合",
#     "版本修订",
#     "风险",
#     "自选组合",
#     "自选列表",
#     "watchlists_portfolios",
#     "lifecycle",
#     "portfolio",
#     "portfolios",
#     "revision",
#     "risk",
#     "watchlist",
#     "watchlists"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/errors.py",
#     "backend/modules/investment_workspace/application/portfolios.py",
#     "backend/modules/investment_workspace/application/watchlists.py",
#     "backend/modules/investment_workspace/domain/values.py",
#     "backend/modules/investment_workspace/infrastructure/repositories.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""investment_workspace 集成测试（真实 PG：revision 并发、批量排序原子性、校验）。"""

from __future__ import annotations

import pytest

from backend.modules.investment_workspace.application.errors import (
    InvalidPositionError,
    PortfolioNotEmptyError,
    PortfolioPositionConflictError,
    RevisionConflictError,
    WatchlistItemDuplicateError,
    WatchlistItemOrderConflictError,
    WatchlistNameConflictError,
    WatchlistNotEmptyError,
)
from backend.modules.investment_workspace.application.portfolios import PortfolioService
from backend.modules.investment_workspace.application.watchlists import WatchlistService
from backend.modules.investment_workspace.domain.values import InstrumentRef
from backend.modules.investment_workspace.infrastructure.repositories import (
    SqlAlchemyWorkspaceUnitOfWork,
)


def _uow(env):
    return SqlAlchemyWorkspaceUnitOfWork(env["session_factory"])


def test_watchlist_full_lifecycle_and_revision_conflicts(env):
    with _uow(env) as uow:
        service = WatchlistService(uow)
        created = service.create("自选组")
        assert created.version == 1 and created.item_count == 0

        with pytest.raises(WatchlistNameConflictError):
            service.create("自选组")
        with pytest.raises(RevisionConflictError):
            service.rename(created.id, "新名字", expected_version=99)
        renamed = service.rename(created.id, "新名字", expected_version=1)
        assert renamed.version == 2

        # 标的增删排序
        add1 = service.add_item(created.id, "CN", "000001.SH", expected_revision=2)
        assert add1.watchlist_revision == 3
        add2 = service.add_item(created.id, "US", "AAPL", expected_revision=3)
        assert add2.item.display_order == 1
        with pytest.raises(WatchlistItemDuplicateError):
            service.add_item(created.id, "CN", "000001.SH", expected_revision=4)

        # 批量排序：条目集合不一致 → 冲突；正确顺序整体重排
        with pytest.raises(WatchlistItemOrderConflictError):
            service.reorder(created.id, 4, [InstrumentRef("US", "AAPL")])
        items, revision = service.reorder(
            created.id,
            4,
            [InstrumentRef("US", "AAPL"), InstrumentRef("CN", "000001.SH")],
        )
        assert revision == 5
        assert [(i.market, i.symbol) for i in items] == [("US", "AAPL"), ("CN", "000001.SH")]

        # 排序 revision 冲突：用旧 revision 重排被拒
        with pytest.raises(WatchlistItemOrderConflictError):
            service.reorder(created.id, 4, [InstrumentRef("CN", "000001.SH"), InstrumentRef("US", "AAPL")])

        # 非空分组删除被拒；清空后按版本删除
        with pytest.raises(WatchlistNotEmptyError):
            service.delete(created.id, expected_version=5)
        service.remove_item(created.id, add2.item.id, expected_revision=5)
        service.remove_item(created.id, add1.item.id, expected_revision=6)
        service.delete(created.id, expected_version=7)
        with pytest.raises(Exception):
            service.get(created.id)


def test_portfolio_positions_validation_and_conflicts(env):
    with _uow(env) as uow:
        service = PortfolioService(uow)
        portfolio = service.create("组合A")

        with pytest.raises(InvalidPositionError):
            service.upsert_position(portfolio.id, InstrumentRef("CN", "000001.SH"), 0, 10.0, 1)
        with pytest.raises(InvalidPositionError):
            service.upsert_position(portfolio.id, InstrumentRef("CN", "000001.SH"), 1, -1.0, 1)

        upserted = service.upsert_position(portfolio.id, InstrumentRef("CN", "000001.SH"), 100, 12.5, 1)
        assert upserted.portfolio_revision == 2
        # 同标的是同一条持仓（upsert）
        updated = service.upsert_position(portfolio.id, InstrumentRef("CN", "000001.SH"), 200, 13.0, 2)
        assert updated.position.quantity == 200
        positions, revision = service.list_positions(portfolio.id)
        assert len(positions) == 1 and revision == 3

        # revision 冲突
        with pytest.raises(PortfolioPositionConflictError):
            service.upsert_position(portfolio.id, InstrumentRef("US", "AAPL"), 1, 100.0, expected_revision=2)

        # 非空组合删除被拒
        with pytest.raises(PortfolioNotEmptyError):
            service.delete(portfolio.id, expected_version=3)
        service.remove_position(portfolio.id, InstrumentRef("CN", "000001.SH"), expected_revision=3)
        service.delete(portfolio.id, expected_version=4)


def test_two_sessions_revision_conflict_requires_reread(env):
    with _uow(env) as uow:
        created = WatchlistService(uow).create("并发组")

    # 会话 A 基于 revision 1 修改成功
    with _uow(env) as uow:
        WatchlistService(uow).rename(created.id, "并发组-A", expected_version=1)
    # 会话 B 仍持有 revision 1 → 冲突，必须重新拉取
    with _uow(env) as uow:
        with pytest.raises(RevisionConflictError):
            WatchlistService(uow).rename(created.id, "并发组-B", expected_version=1)
        dto = WatchlistService(uow).get(created.id)
        assert dto.name == "并发组-A"
        renamed = WatchlistService(uow).rename(created.id, "并发组-B", expected_version=dto.version)
        assert renamed.version == 3


def test_portfolio_risk_profile_is_explicit_and_patch_omission_preserves_it(env):
    from dataclasses import asdict
    from inspect import signature
    from backend.modules.investment_workspace.application.errors import PortfolioAccountInvalidError

    with _uow(env) as uow:
        service = PortfolioService(uow)
        created = service.create("资格档位", total_assets=100000, available_cash=100000)
        assert created.risk_profile is None

        def update(dto, **changes):
            values = {key: value for key, value in asdict(dto).items()
                      if key in signature(service.update_account).parameters and key != "risk_profile"}
            values.update(expected_version=dto.version, **changes)
            return service.update_account(dto.id, **values)

        with pytest.raises(PortfolioAccountInvalidError, match="风险档预算"):
            update(created, risk_profile="BALANCED")
        selected = update(created, risk_profile="BALANCED",
                          risk_per_trade_pct=.005, max_portfolio_open_risk_pct=.04,
                          max_total_position_pct=.75, max_single_stock_pct=.08,
                          max_sector_open_risk_pct=.02, max_daily_new_risk_pct=.02,
                          max_drawdown_pct=.15)
        assert selected.risk_profile == "BALANCED"
        with pytest.raises(PortfolioAccountInvalidError, match="risk_per_trade_pct"):
            update(selected, risk_per_trade_pct=.01)
        with pytest.raises(PortfolioAccountInvalidError, match="有限数值"):
            update(selected, risk_per_trade_pct=float("nan"))
        preserved = update(selected)
        assert preserved.risk_profile == "BALANCED"
        cleared = update(preserved, risk_profile=None)
        assert cleared.risk_profile is None
        with pytest.raises(PortfolioAccountInvalidError):
            update(cleared, risk_profile="guessed")
        created_tier = service.create("创建时选档", total_assets=300000, available_cash=300000,
                                      risk_profile="BALANCED")
        assert created_tier.risk_per_trade_pct == .005
        assert created_tier.max_total_position_pct == .75
