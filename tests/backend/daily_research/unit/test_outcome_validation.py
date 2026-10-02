# test-catalog-begin
# {
#   "purpose": "每日研究 / outcome_validation",
#   "keywords": [
#     "每日研究",
#     "数据完整性",
#     "市场分析",
#     "outcome_validation",
#     "coverage",
#     "market"
#   ],
#   "covers": [
#     "backend/modules/daily_research/application/news_pipeline.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.modules.daily_research.application.news_pipeline import (
    _attach_market_outcomes,
    _validate_price_horizons,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _sessions(*days: str) -> list[date]:
    return [date.fromisoformat(day) for day in days]


def test_premarket_event_validates_one_day_then_keeps_longer_windows_waiting():
    sessions = _sessions("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07")
    predictions = [
        {"trading_days": 1, "direction": "bullish"},
        {"trading_days": 5, "direction": "bullish"},
        {"trading_days": 20, "direction": "bearish"},
    ]

    result = _validate_price_horizons(
        announced_at=datetime(2026, 9, 2, 8, 50, tzinfo=SHANGHAI),
        sessions=sessions,
        target_prices={
            sessions[0]: [100.0], sessions[1]: [102.0],
        },
        benchmark_prices={sessions[0]: 100.0, sessions[1]: 101.0},
        as_of_trade_date=sessions[1],
        predicted_horizons=predictions,
        direction_basis="abnormal_return",
    )

    assert result[0]["status"] == "validated"
    assert result[0]["target_return"] == 0.02
    assert result[0]["benchmark_return"] == 0.01
    assert result[0]["actual_direction"] == "bullish"
    assert result[0]["prediction_match"] is True
    assert result[1]["status"] == "awaiting_window"
    assert result[2]["status"] == "awaiting_window"


def test_intraday_event_starts_next_session_and_uses_exact_window_length():
    sessions = _sessions("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09")
    prices = {day: [100.0 + index] for index, day in enumerate(sessions)}
    benchmarks = {day: 100.0 for day in sessions}

    result = _validate_price_horizons(
        announced_at=datetime(2026, 9, 2, 10, 0, tzinfo=SHANGHAI),
        sessions=sessions,
        target_prices=prices,
        benchmark_prices=benchmarks,
        as_of_trade_date=sessions[6],
        predicted_horizons=[{"trading_days": horizon, "direction": "bullish"} for horizon in (1, 5, 20)],
    )

    assert result[0]["start_date"] == "2026-09-03"
    assert result[0]["baseline_date"] == "2026-09-02"
    assert result[0]["end_date"] == "2026-09-03"
    assert result[1]["end_date"] == "2026-09-09"
    assert result[2]["status"] == "awaiting_window"


def test_mature_window_with_less_than_seventy_percent_target_coverage_is_not_validated():
    sessions = _sessions("2026-09-01", "2026-09-02")

    result = _validate_price_horizons(
        announced_at=datetime(2026, 9, 2, 8, 30, tzinfo=SHANGHAI),
        sessions=sessions,
        target_prices={sessions[0]: [100.0, None], sessions[1]: [110.0, 105.0]},
        benchmark_prices={sessions[0]: 100.0, sessions[1]: 101.0},
        as_of_trade_date=sessions[1],
        predicted_horizons=[{"trading_days": 1, "direction": "bullish"}],
        expected_target_count=2,
    )

    assert result[0]["status"] == "data_insufficient"
    assert result[0]["coverage"] == 0.5
    assert "未缩短窗口" in result[0]["reason"]


def test_market_event_report_records_actual_and_benchmark_returns_from_the_cutoff_snapshot():
    sessions = _sessions("2026-09-01", "2026-09-02")

    class FakeMarketConnection:
        def execute(self, sql, params):
            assert "market.instrument_daily" in sql
            assert "2026-09-02" == params[2].isoformat()
            return self

        def fetchall(self):
            return [
                ("000001.SH", sessions[0], 100.0, 1.0),
                ("000001.SH", sessions[1], 102.0, 1.0),
                ("000300.SH", sessions[0], 100.0, 1.0),
                ("000300.SH", sessions[1], 101.0, 1.0),
            ]

    forecasts = [{
        "event_id": 7,
        "announced_at": datetime(2026, 9, 2, 8, 50, tzinfo=SHANGHAI),
        "first_published_at": datetime(2026, 9, 2, 8, 50, tzinfo=SHANGHAI),
        "targets": [{
            "target": "market:CN",
            "scope": "market",
            "scope_refs": [],
            "horizons": [
                {"trading_days": horizon, "direction": "bullish"}
                for horizon in (1, 5, 20)
            ],
        }],
    }]

    _attach_market_outcomes(
        forecasts, market_conn=FakeMarketConnection(), as_of_trade_date=sessions[1],
    )

    event = forecasts[0]
    one_day = event["targets"][0]["horizons"][0]["validation"]
    assert event["status"] == "partial"
    assert one_day["status"] == "validated"
    assert one_day["target_return"] == 0.02
    assert one_day["benchmark_return"] == 0.01
    assert one_day["actual_direction"] == "bullish"
    assert event["targets"][0]["horizons"][1]["validation"]["status"] == "awaiting_window"
