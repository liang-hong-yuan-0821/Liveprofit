# test-catalog-begin
# {
#   "purpose": "量化策略 / data_readiness",
#   "keywords": [
#     "量化策略",
#     "个股分析",
#     "任务",
#     "data_readiness",
#     "stock",
#     "task"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/data_readiness.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date

import pytest

from backend.modules.quant_strategy.application.data_readiness import (
    DataReadinessError,
    DataReadinessGate,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self.row = row

    def execute(self, sql, params):
        assert "quant_data_readiness" in sql
        return _Result(self.row)


class _Coverage:
    def __init__(self, freshness="FRESH"):
        self.freshness = freshness
        self.target = None

    def snapshot(self, resource, target, *, conn):
        assert resource.value == "CN_STOCK_QUANT_INPUTS"
        self.target = target
        return {
            "freshness": self.freshness,
            "missing_count": 1 if self.freshness != "FRESH" else 0,
            "coverage_digest": "coverage-fixture",
            "universe_digest": "universe-fixture",
        }


def test_common_watermark_is_minimum_resource_date():
    coverage = _Coverage()
    result = DataReadinessGate.resolve(
        _Conn((date(2026, 9, 18), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 18))),
        date(2026, 9, 20),
        coverage_repository=coverage,
    )
    assert result.market_as_of_trade_date == date(2026, 9, 17)
    assert result.requested_trade_date == date(2026, 9, 20)
    assert coverage.target.expected_trade_date == date(2026, 9, 17)
    assert result.coverage_digest == "coverage-fixture"


def test_missing_resource_blocks_whole_task():
    with pytest.raises(DataReadinessError) as exc:
        DataReadinessGate.resolve(
            _Conn((date(2026, 9, 18), None, date(2026, 9, 18), date(2026, 9, 18))),
            date(2026, 9, 20), coverage_repository=_Coverage(),
        )
    assert exc.value.code == "QUANT_DATA_NOT_READY"


def test_latest_dates_do_not_hide_missing_stock_fields():
    with pytest.raises(DataReadinessError, match="覆盖不足"):
        DataReadinessGate.resolve(
            _Conn((date(2026, 9, 18),) * 4), date(2026, 9, 18),
            coverage_repository=_Coverage("PARTIAL"),
        )
