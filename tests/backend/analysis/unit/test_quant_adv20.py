# test-catalog-begin
# {
#   "purpose": "分析任务 / quant_adv20",
#   "keywords": [
#     "分析任务",
#     "quant_adv20"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/quant_execution_market_data.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date, timedelta
from types import SimpleNamespace

from backend.modules.analysis.infrastructure.quant_execution_market_data import _adv20_amount


class Calendar:
    def __init__(self, days, *, available=True):
        self.days = days
        self.available = available

    def schedule(self, market):
        assert market == "CN"
        return SimpleNamespace(
            available=self.available, supported_from=self.days[0],
            supported_through=self.days[-1],
            sessions=[SimpleNamespace(trade_date=day) for day in self.days],
        )


def test_adv20_requires_every_verified_session_and_valid_amount():
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(20)]
    bars = [("000001.SZ", day, None, None, None, None, None, 100 + i)
            for i, day in enumerate(days)]
    calendar = Calendar(days)
    assert _adv20_amount(bars, trade_date=days[-1], calendar=calendar) == "109.5"
    assert _adv20_amount(bars[:-1], trade_date=days[-1], calendar=calendar) is None
    assert _adv20_amount(bars[:8] + bars[9:], trade_date=days[-1], calendar=calendar) is None
    assert _adv20_amount(bars[:5] + [(*bars[5][:7], None)] + bars[6:],
                         trade_date=days[-1], calendar=calendar) is None
    assert _adv20_amount(bars, trade_date=days[-1],
                         calendar=Calendar(days, available=False)) is None
