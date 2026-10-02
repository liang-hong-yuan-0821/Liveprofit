"""Current account facts under the portfolio lock shared by planners and fills."""
from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerBaselineRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillReportRow
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder

ACTIVE = ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")
RISK_FIELDS = (
    "risk_per_trade_pct", "min_risk_reward_ratio", "max_total_position_pct",
    "max_single_stock_pct", "max_sector_pct", "max_portfolio_open_risk_pct",
    "max_sector_open_risk_pct", "max_daily_new_risk_pct", "max_drawdown_pct",
    "max_daily_loss_pct", "net_asset_value", "peak_net_asset_value",
    "day_start_net_asset_value", "risk_facts_as_of",
)


def lock_portfolio(session, portfolio_id):
    return session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                          .with_for_update().execution_options(populate_existing=True))


def requires_account_reconciliation(session, portfolio_id) -> bool:
    """Under the portfolio lock, refuse new risk while account facts lack certification."""
    return (session.scalar(select(AccountLedgerBaselineRow.id).where(
        AccountLedgerBaselineRow.portfolio_id == portfolio_id).limit(1)) is not None
        or session.scalar(select(AccountFillReportRow.id).where(
            AccountFillReportRow.portfolio_id == portfolio_id).limit(1)) is not None)


def planning_account(session, portfolio):
    """Caller holds portfolio lock until accepted orders are flushed/committed."""
    from .portfolio_drawdown_actions import PortfolioDrawdownActions
    pause = PortfolioDrawdownActions(session).active_pause(portfolio.id)
    account_reconciliation_required = requires_account_reconciliation(session, portfolio.id)
    positions = list(session.scalars(select(PortfolioPosition).where(
        PortfolioPosition.portfolio_id == portfolio.id, PortfolioPosition.quantity > 0,
    ).execution_options(populate_existing=True)))
    orders = list(session.scalars(select(SuggestedOrder).where(
        SuggestedOrder.portfolio_id == portfolio.id, SuggestedOrder.status.in_(ACTIVE),
        SuggestedOrder.quantity > SuggestedOrder.filled_quantity,
    ).execution_options(populate_existing=True)))
    from .ownership import collect_owner_versions
    return {
        "owner_versions": collect_owner_versions(session, portfolio.id),
        "portfolio_snapshot": {
            "id": str(portfolio.id), "name": portfolio.name, "version": portfolio.version,
            "risk_profile": portfolio.risk_profile,
            "risk_pause_event_id": str(pause.id) if pause is not None else None,
            "account_reconciliation_required": account_reconciliation_required,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "total_assets": str(portfolio.total_assets), "available_cash": str(portfolio.available_cash),
            "risk": {key: str(getattr(portfolio, key)) if getattr(portfolio, key) is not None else None
                     for key in RISK_FIELDS},
        },
        "positions": [{"symbol": p.symbol, "market": p.market, "quantity": p.quantity,
                       "average_cost": p.average_cost, "active_stop_price": p.active_stop_price}
                      for p in positions],
        "pending_orders": [{
            "id": str(o.id), "side": o.side, "symbol": o.symbol, "market": o.market,
            "industry_code": o.industry_code, "remaining_quantity": o.quantity - o.filled_quantity,
            "order_entry_price": o.limit_price, "order_stop_price": o.stop_price,
            # Keep the whole fee buffer after partial fills; filled principal is
            # already deducted from account cash. Never release pending principal.
            "reserved_cash": max(Decimal(0), o.reserved_cash - o.filled_quantity * o.limit_price),
        } for o in orders],
    }
