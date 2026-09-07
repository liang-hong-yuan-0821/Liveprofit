"""市场数据契约测试（§2.6.1：资产目录、bars 参数/错误语义、热点空态）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar


def _seed_bars(client, symbol: str = "000001.SH", trade_date: date = date(2026, 9, 4)) -> None:
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from backend.tests.contract.api.conftest import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        asset_id = conn.execute(
            text("SELECT id FROM market_assets WHERE market='CN' AND symbol=:symbol"), {"symbol": symbol}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO market_bars_daily (asset_id, trading_date, open, high, low, close, volume, source) "
                "VALUES (:asset_id, :trade_date, 3300, 3350, 3290, 3340, 1000000, 'tushare') "
                "ON CONFLICT (asset_id, trading_date) DO NOTHING"
            ),
            {"asset_id": asset_id, "trade_date": trade_date},
        )
    engine.dispose()


def test_market_assets_catalog_envelope(client):
    response = client.http.get("/api/v1/market-assets?enabled=true")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["schema_version"] == "v1"
    items = body["data"]["items"]
    assert len(items) == 12
    # 服务端只保证 display_order，组序由前端固定：断言每组组内顺序升序且 US 三资产齐全
    us = [item for item in items if item["market"] == "US"]
    assert [item["symbol"] for item in us] == [".INX", ".DJI", ".IXIC"]
    us_orders = [item["display_order"] for item in us]
    assert us_orders == sorted(us_orders)
    cn = [item for item in items if item["market"] == "CN"]
    assert [item["symbol"] for item in cn] == [
        "000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000016.SH", "000852.SH", "000015.SH",
    ]
    assert cn[0]["availability_status"] == "AVAILABLE"
    assert cn[0]["supported_intervals"] == ["1d"]
    assert cn[0]["market_timezone"] == "Asia/Shanghai"


def test_bars_success_with_freshness_and_closed(client):
    _seed_bars(client)
    # 注入假日历：最近交易日 = 数据日，非交易日 → CLOSED + FRESH
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-05"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["interval"] == "1d"
    assert data["from"] == "2026-09-01"
    assert data["to"] == "2026-09-05"
    assert len(data["bars"]) == 1
    assert data["bars"][0]["close"] == 3340.0
    assert data["freshness_status"] == "FRESH"
    assert data["market_session_status"] == "CLOSED"
    assert data["market_closed_reason"] is not None
    assert data["asset"]["symbol"] == "000001.SH"
    # 单根 bars：指标仍在（前导不足时窗口处为 null，降级语义固化）
    indicators = data["indicators"]
    assert indicators is not None
    assert indicators["ma"][0]["values"] == [None]
    assert indicators["boll"]["mid"] == [None]


def test_bars_param_errors(client):
    # interval 非白名单 → 422
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=5d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INTERVAL_NOT_SUPPORTED"
    # 范围超窗 → 422（不静默截断）
    response = client.http.get(
        "/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2025-01-01&to=2026-09-04"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RANGE_TOO_LARGE"
    # 未知资产 → 404
    response = client.http.get(
        "/api/v1/market-data/indices/999999.SH/bars?market=CN&interval=1d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def _seed_dense_bars(
    client, symbol: str = "000001.SH", start: date = date(2026, 4, 7), days: int = 150
) -> None:
    """密集种连续自然日（含周末；seed 不校验交易日），close = 3300 + offset 线性递增。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from backend.tests.contract.api.conftest import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        asset_id = conn.execute(
            text("SELECT id FROM market_assets WHERE market='CN' AND symbol=:symbol"), {"symbol": symbol}
        ).scalar_one()
        for offset in range(days):
            trading_date = start + timedelta(days=offset)
            close = 3300.0 + offset
            conn.execute(
                text(
                    "INSERT INTO market_bars_daily (asset_id, trading_date, open, high, low, close, volume, source) "
                    "VALUES (:asset_id, :trade_date, :open, :high, :low, :close, 1000000, 'tushare') "
                    "ON CONFLICT (asset_id, trading_date) DO NOTHING"
                ),
                {
                    "asset_id": asset_id,
                    "trade_date": trading_date,
                    "open": close - 10.0,
                    "high": close + 20.0,
                    "low": close - 20.0,
                    "close": close,
                },
            )
    engine.dispose()


def test_bars_include_indicators_aligned_with_warmup(client):
    """指标契约（K线指标叠加方案 §3.2.3）：补窗口后首根即有值、各数组与 bars 等长对齐。"""
    _seed_dense_bars(client)
    from_date = date(2026, 4, 7) + timedelta(days=90)
    to_date = date(2026, 4, 7) + timedelta(days=149)
    client.http.app.state.market_calendar = FakeCalendar(trading_day=False, last_day=to_date)
    response = client.http.get(
        f"/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from={from_date}&to={to_date}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # 返回区间仍为 [from, to]，共 60 根
    assert len(data["bars"]) == 60
    assert data["bars"][0]["close"] == 3390.0
    # 指标与 bars 等长、按 index 对齐
    indicators = data["indicators"]
    assert indicators is not None
    assert [line["period"] for line in indicators["ma"]] == [5, 10, 20, 60]
    for line in indicators["ma"]:
        assert len(line["values"]) == 60
    for key in ("mid", "upper", "lower"):
        assert len(indicators["boll"][key]) == 60
    # 补窗口：90 根前导 ≥ 59 → 首根 MA60/BOLL 即有值（close 线性序列可精确推导）
    assert indicators["ma"][3]["values"][0] == pytest.approx(3360.5)  # mean(3331..3390)
    assert indicators["boll"]["mid"][0] == pytest.approx(3380.5)  # mean(3371..3390)
    # 旧字段回归
    assert data["freshness_status"] == "FRESH"
    assert data["market_session_status"] == "CLOSED"
    assert data["asset"]["symbol"] == "000001.SH"


def test_bars_us_asset_upstream_unavailable_503(client):
    response = client.http.get(
        "/api/v1/market-data/indices/.INX/bars?market=US&interval=1d&from=2026-08-01&to=2026-09-04"
    )
    assert response.status_code == 503
    problem = response.json()
    assert problem["code"] == "MARKET_DATA_UPSTREAM_UNAVAILABLE"
    assert problem["retryable"] is True


def test_hot_concepts_empty_is_normal_business_state(client):
    response = client.http.get(
        "/api/v1/market-data/concepts/hot?market=CN&interval=1d&from=2026-08-01&to=2026-09-04&limit=20"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result_status"] == "NO_HOT_CONCEPTS"
    assert data["items"] == []
    assert data["algorithm_version"] == "heat_v1"
    assert response.json()["meta"]["schema_version"] == "v1"
