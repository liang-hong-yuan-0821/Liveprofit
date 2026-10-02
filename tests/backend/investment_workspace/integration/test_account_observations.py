# test-catalog-begin
# {
#   "purpose": "投资工作区 / account_observations（账户）",
#   "keywords": [
#     "投资工作区",
#     "历史审计",
#     "幂等",
#     "不可变历史",
#     "来源观测",
#     "投资组合",
#     "重放",
#     "来源证据",
#     "account_observations",
#     "history",
#     "idempotent",
#     "immutable",
#     "observation",
#     "portfolio",
#     "replay",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/errors.py",
#     "backend/modules/investment_workspace/application/portfolios.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/infrastructure/account_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/repositories.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.errors import PortfolioNotEmptyError
from backend.modules.investment_workspace.application.portfolios import PortfolioService
from backend.modules.investment_workspace.application.reconciliation import AccountObservation, ObservedHolding
from backend.modules.investment_workspace.infrastructure.account_models import AccountObservationRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.investment_workspace.infrastructure.repositories import SqlAlchemyWorkspaceUnitOfWork


DAY = date(2026, 9, 29)
CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)


def _portfolio(factory):
    with factory() as session:
        portfolio = Portfolio(id=uuid.uuid4(), name=f"snapshot-{uuid.uuid4().hex}", version=1,
                              total_assets=Decimal("10000"), available_cash=Decimal("1000"))
        session.add(portfolio)
        session.commit()
        return portfolio.id


def _snapshot(portfolio_id, *, cash="1000.00", sellable="0", reverse=False):
    holdings = [
        ObservedHolding("CN", "000001.SZ", "100", sellable),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ]
    return AccountObservation(
        portfolio_id=portfolio_id, trade_date=DAY, captured_at=CAPTURED,
        cash=cash, holdings=tuple(reversed(holdings) if reverse else holdings),
        complete_holdings=True, source_ref="import-001",
    )


def test_observation_replay_is_idempotent_and_never_changes_portfolio(env):
    factory = env["session_factory"]
    portfolio_id = _portfolio(factory)
    with factory() as session:
        first, created = AccountObservationService(session).record(
            _snapshot(portfolio_id), source_type="MANUAL_IMPORT")
        session.commit()
        first_id, first_hash = first.id, first.payload_sha256
    with factory() as session:
        repeated, created = AccountObservationService(session).record(
            _snapshot(portfolio_id, cash="1000", reverse=True), source_type="MANUAL_IMPORT")
        assert not created and repeated.id == first_id and repeated.payload_sha256 == first_hash
        assert session.scalar(select(func.count()).select_from(AccountObservationRow)) == 1
        assert repeated.holdings == [
            ["CN", "000001.SZ", "100.0000", "0.0000"],
            ["HK", "000001.SZ", "30.0000", "30.0000"],
        ]
        portfolio = session.get(Portfolio, portfolio_id)
        assert portfolio.available_cash == Decimal(1000) and portfolio.version == 1


def test_same_source_identity_with_changed_content_is_rejected(env):
    factory = env["session_factory"]
    portfolio_id = _portfolio(factory)
    with factory() as session:
        AccountObservationService(session).record(_snapshot(portfolio_id), source_type="BROKER_EXPORT")
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="replay changed"):
            AccountObservationService(session).record(
                _snapshot(portfolio_id, sellable="100"), source_type="BROKER_EXPORT")
        session.rollback()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AccountObservationRow)) == 1


def test_invalid_sellable_is_not_persisted_and_history_is_immutable(env):
    factory = env["session_factory"]
    portfolio_id = _portfolio(factory)
    with factory() as session:
        with pytest.raises(ValueError, match="exceeds quantity"):
            AccountObservationService(session).record(
                _snapshot(portfolio_id, sellable="101"), source_type="BROKER_API")
        session.rollback()
        row, created = AccountObservationService(session).record(
            _snapshot(portfolio_id), source_type="MANUAL_IMPORT")
        assert created
        session.commit()
        row_id = row.id
    for statement in (
        "UPDATE account_observations SET cash=999 WHERE id=:id",
        "DELETE FROM account_observations WHERE id=:id",
        "UPDATE account_observations SET holdings='[]'::jsonb WHERE id=:id",
    ):
        with factory() as session:
            with pytest.raises(DBAPIError, match="account observation history is immutable"):
                session.execute(text(statement), {"id": row_id})
            session.rollback()
    with factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(text("TRUNCATE account_observations"))
        session.rollback()
    with factory() as session:
        assert session.get(AccountObservationRow, row_id).cash == Decimal(1000)
        assert len(session.get(AccountObservationRow, row_id).holdings) == 2


def test_observed_portfolio_cannot_be_deleted_even_without_local_positions(env):
    factory = env["session_factory"]
    portfolio_id = _portfolio(factory)
    with factory() as session:
        AccountObservationService(session).record(_snapshot(portfolio_id), source_type="MANUAL_IMPORT")
        session.commit()
    with SqlAlchemyWorkspaceUnitOfWork(factory) as uow:
        with pytest.raises(PortfolioNotEmptyError, match="账户观察历史"):
            PortfolioService(uow).delete(portfolio_id, expected_version=1)
    with factory() as session:
        assert session.get(Portfolio, portfolio_id) is not None
