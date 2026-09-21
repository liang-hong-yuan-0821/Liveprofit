from datetime import date

import pytest

from backend.modules.quant_strategy.application.data_readiness import DataReadinessError, DataReadinessGate


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


def test_common_watermark_is_minimum_resource_date():
    result = DataReadinessGate.resolve(
        _Conn((date(2026, 9, 18), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 18))),
        date(2026, 9, 20),
    )
    assert result.market_as_of_trade_date == date(2026, 9, 17)
    assert result.requested_trade_date == date(2026, 9, 20)


def test_missing_resource_blocks_whole_task():
    with pytest.raises(DataReadinessError) as exc:
        DataReadinessGate.resolve(_Conn((date(2026, 9, 18), None, date(2026, 9, 18), date(2026, 9, 18))), date(2026, 9, 20))
    assert exc.value.code == "QUANT_DATA_NOT_READY"
