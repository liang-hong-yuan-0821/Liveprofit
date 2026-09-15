"""market_data 集成测试（读路径走 db.instrument：新鲜度/开闭市、校验、US 空 bars、热点空态）。

原六个采集/仓库用例（ingestion 规范化/幂等/65535 参数上限/缺段拒绝/live 验收守卫）
已随 ingestion 迁移至 tests/db/instrument/test_index_ingestion.py（fixture provider
工厂）；目录断言（list_assets）与上游不可用断言（503 语义）删除——前端写死清单。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import db.instrument.db as market_db
from backend.modules.market_data.application.errors import (
    IntervalNotSupportedError,
    RangeTooLargeError,
)
from backend.modules.market_data.application.service import MarketDataService
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar


@pytest.fixture
def service(env):
    """market_conn 工厂注入（模块常量直赋值，module 级 fixture 内不得用 function monkeypatch）。"""
    market_db.PG_CONNECTION_STRING = env["psycopg_dsn"]
    from db.instrument.db import get_connection

    return lambda **kw: MarketDataService(market_conn=get_connection, **kw)


def _seed(env, sql: str, params: dict) -> None:
    from sqlalchemy import text

    with env["session_factory"]() as session:
        session.execute(text(sql), params)
        session.commit()


def test_get_bars_freshness_and_closed_session(env, service):
    clock = type("Clock", (), {"now": lambda self: datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)})()
    calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    _seed(env,
          "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
          "VALUES ('000001.SH', '上证综指', 'index', 'tushare') ON CONFLICT DO NOTHING",
          {})
    _seed(env,
          "INSERT INTO market.instrument_daily "
          "(ts_code, trade_date, open, high, low, close, vol, source) "
          "VALUES ('000001.SH', '2026-09-04', 3300, 3350, 3290, 3340, 1000000, 'tushare') "
          "ON CONFLICT DO NOTHING",
          {})
    dto = service(clock=clock, calendar=calendar).get_bars(
        market="CN", symbol="000001.SH", interval="1d",
        from_date=date(2026, 9, 1), to_date=date(2026, 9, 5),
    )
    assert dto.freshness_status == "FRESH"  # 覆盖最近已收盘交易日（9-4）
    assert dto.market_session_status == "CLOSED"  # 非交易日 → 休市
    assert dto.market_closed_reason is not None
    assert len(dto.bars) == 1
    assert dto.bars[0]["close"] == 3340.0


def test_bars_validation_errors(env, service):
    svc = service(calendar=FakeCalendar())
    with pytest.raises(IntervalNotSupportedError):
        svc.get_bars(
            market="CN", symbol="000001.SH", interval="5d",
            from_date=date(2026, 8, 1), to_date=date(2026, 9, 1),
        )
    # from>to 拒绝语义保留（M2 定稿：K 线方案落地后的用例承接方）
    with pytest.raises(RangeTooLargeError):
        svc.get_bars(
            market="CN", symbol="000001.SH", interval="1d",
            from_date=date(2026, 9, 5), to_date=date(2026, 9, 1),
        )


def test_us_asset_returns_empty_bars_unavailable(env, service):
    """US 资产：instrument 有行、instrument_daily 无数据 → 200 空 bars +
    UNAVAILABLE（原 503 语义整体删除，决策 13）。"""
    _seed(env,
          "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
          "VALUES ('.INX', '标普500', 'index', 'tushare') ON CONFLICT DO NOTHING",
          {})
    dto = service(calendar=FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))).get_bars(
        market="US", symbol=".INX", interval="1d",
        from_date=date(2026, 8, 1), to_date=date(2026, 9, 4),
    )
    assert dto.bars == []
    assert dto.freshness_status == "UNAVAILABLE"
    assert dto.asset.market == "US"  # 形参回显


def test_hot_concepts_empty_and_non_cn(env, service):
    """NO_HOT_CONCEPTS 重建（现场计算语义）：空表 → 空态；market≠CN → 空态
    （原 HotConceptsUpstreamUnavailableError 503 语义删除）。"""
    svc = service(calendar=FakeCalendar(trading_day=False, last_day=date(2026, 9, 4)))
    as_of, items, status, freshness, source_updated = svc.get_hot_concepts(market="CN", as_of=None, limit=30)
    assert status == "NO_HOT_CONCEPTS"
    assert items == []
    assert as_of is None
    # 非 CN 返回空态而非异常
    _, items_us, status_us, _, _ = svc.get_hot_concepts(market="US", as_of=None, limit=30)
    assert status_us == "NO_HOT_CONCEPTS" and items_us == []
