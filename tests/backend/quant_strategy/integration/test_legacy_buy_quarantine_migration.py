# test-catalog-begin
# {
#   "purpose": "量化策略 / legacy_buy_quarantine_migration（迁移）：Migration test only writes conftest's isolated liveprofit_quant_strategy_test.",
#   "keywords": [
#     "量化策略",
#     "迁移",
#     "legacy_buy_quarantine_migration",
#     "migration"
#   ],
#   "covers": [
#     "backend/migrations/versions/0030_suggested_order_rule_decision_time.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Migration test only writes conftest's isolated liveprofit_quant_strategy_test."""

from tests.support.python.paths import PROJECT_ROOT

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder




def _config(session) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {
        "x": ["db_url=" + session.bind.url.render_as_string(hide_password=False)], "name": None,
    })()
    return config


def test_upgrade_quarantines_only_uncertified_active_buy(env):
    with env["session_factory"]() as session:
        config = _config(session)
        command.downgrade(config, "0030")
        portfolio = Portfolio(id=uuid4(), name=f"legacy-buy-{uuid4().hex}",
                              total_assets=Decimal(100000), available_cash=Decimal(100000))
        session.add(portfolio)
        session.flush()
        specs = (("BUY", "PROPOSED", 0), ("BUY", "EXECUTING", 0),
                 ("BUY", "PARTIALLY_FILLED", 40), ("SELL", "PROPOSED", 0),
                 ("BUY", "FILLED", 100))
        ids = []
        for side, status, filled in specs:
            order = SuggestedOrder(
                id=uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                side=side, quantity=Decimal(100), filled_quantity=Decimal(filled),
                limit_price=Decimal(10), reserved_cash=Decimal(1000) if side == "BUY" else Decimal(0),
                reserved_risk=Decimal(0), reason_code="LEGACY", status=status, revision=1,
            )
            session.add(order)
            ids.append(order.id)
        session.commit()
        command.upgrade(config, "head")
        rows = list(session.scalars(select(SuggestedOrder).where(SuggestedOrder.id.in_(ids))))
        by_id = {row.id: row for row in rows}
        assert [(by_id[order_id].status, by_id[order_id].revision) for order_id in ids] == [
            ("SUPERSEDED", 2), ("RECONCILIATION_REQUIRED", 2),
            ("RECONCILIATION_REQUIRED", 2), ("PROPOSED", 1), ("FILLED", 1),
        ]
