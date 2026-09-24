from __future__ import annotations

from db.instrument.dao import instrument as instrument_dao
from backend.modules.analysis.infrastructure import quant_execution_market_data


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        return _Rows(self.rows)


def test_shared_active_cn_stock_query_uses_quant_universe_and_order():
    conn = _Conn([("000001.SZ",), ("600000.SH",)])

    result = instrument_dao.list_active_cn_stocks(conn)

    assert result == ["000001.SZ", "600000.SH"]
    assert "instrument_type = 'stock'" in conn.sql
    assert "list_status = 'L'" in conn.sql
    assert "ts_code ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'" in conn.sql
    assert conn.sql.endswith("ORDER BY ts_code")
    assert conn.params == ()


def test_quant_universe_builder_delegates_to_shared_market_dao(monkeypatch):
    marker_conn = object()
    expected = ["000001.SZ", "600000.SH"]
    calls = []

    def fake_list(conn):
        calls.append(conn)
        return expected

    monkeypatch.setattr(quant_execution_market_data, "list_active_cn_stocks", fake_list)

    assert quant_execution_market_data.AllMarketUniverseBuilder.list_active_cn_stocks(marker_conn) == expected
    assert calls == [marker_conn]
