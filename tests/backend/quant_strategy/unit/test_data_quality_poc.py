# test-catalog-begin
# {
#   "purpose": "量化策略 / data_quality_poc",
#   "keywords": [
#     "量化策略",
#     "数据完整性",
#     "data_quality_poc",
#     "coverage"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/data_quality_poc.py",
#     "backend/modules/quant_strategy/domain/templates.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from backend.modules.quant_strategy.application import data_quality_poc as poc_module
from backend.modules.quant_strategy.application.data_quality_poc import QuantDataQualityPoc
from backend.modules.quant_strategy.domain.templates import TEMPLATES


def _context(size: int) -> dict:
    values = lambda value: [value for _ in range(size)]
    return {
        "meta": {"symbol": "000001.SZ", "bars_count": size},
        "ohlcv": {
            "trade_date": [f"d{i}" for i in range(size)],
            "open": values(10.0),
            "high": values(10.5),
            "low": values(9.5),
            "close": values(10.0),
            "volume": values(100.0),
            "amount": values(1000.0),
        },
        "indicators": {
            "ma_qfq_5": values(10.0),
            "ma_qfq_20": values(10.0),
            "ma_qfq_60": values(9.0),
            "boll_mid_qfq": values(10.0),
            "boll_upper_qfq": values(11.0),
            "boll_lower_qfq": values(9.0),
            "macd_dif_qfq": values(-0.1),
            "macd_dea_qfq": values(-0.05),
            "macd_qfq": values(0.1),
            "rsi_qfq_6": values(55.0),
        },
        "position": {"shares": 0, "average_cost": None, "market_value": 0},
    }


def test_poc_reports_250_day_and_per_template_actual_coverage(monkeypatch):
    watermark = date(2026, 9, 18)
    monkeypatch.setattr(
        poc_module.DataReadinessGate,
        "resolve",
        lambda _conn, requested: SimpleNamespace(
            requested_trade_date=requested,
            market_as_of_trade_date=watermark,
            daily_trade_date=watermark,
            factor_trade_date=watermark,
            adj_factor_trade_date=watermark,
            trade_status_trade_date=watermark,
        ),
    )
    monkeypatch.setattr(
        poc_module.AllMarketUniverseBuilder,
        "list_active_cn_stocks",
        lambda _conn: ["000001.SZ", "000002.SZ", "000003.SZ"],
    )

    class FakeLoader:
        def __init__(self, *, lookback):
            self.lookback = lookback

        def load_batch(self, _conn, codes, _as_of):
            return [
                {"ts_code": codes[0], "status": "OK", "context": _context(self.lookback)},
                {"ts_code": codes[1], "status": "WARMUP_INCOMPLETE", "context": None},
                {"ts_code": codes[2], "status": "WARMUP_INCOMPLETE", "context": None},
            ]

    monkeypatch.setattr(poc_module, "MarketContextBatchLoader", FakeLoader)
    monkeypatch.setattr(
        poc_module,
        "run_strategy",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, error_code=None),
    )

    report = QuantDataQualityPoc(
        batch_size=20, sample_limit=1, runner_sample_size=1
    ).run(object(), watermark)

    assert report["universe_total"] == 3
    assert report["history_250"]["required_bars"] == 250
    assert report["history_250"]["status_counts"] == {"OK": 1, "WARMUP_INCOMPLETE": 2}
    assert report["history_250"]["sample_truncated_counts"] == {"WARMUP_INCOMPLETE": 1}
    assert set(report["templates"]) == set(TEMPLATES)
    for template in report["templates"].values():
        assert template["coverage"] == 1 / 3
        assert template["promotion_eligible"] is False
    assert report["templates"]["ma_trend_cross_v1"]["runner_sample"] == {
        "attempted": 1,
        "ok": 1,
        "errors": {},
    }
    assert report["operational"]["remote_provider_calls"] == 0
