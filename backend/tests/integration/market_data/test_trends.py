"""get_index_trends 服务层集成测试（趋势对比面板方案 4.2.3）。

- env fixture 由本目录 conftest.py 提供（PG 沙箱 liveprofit_market_test）
- service/_seed 是 test_market_flow 的模块级 helper（pytest 模块级 fixture/
  helper 不跨文件共享）——直接 import 复用；FakeCalendar 从 infrastructure 导入
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.modules.market_data.application.errors import RangeTooLargeError
from backend.modules.market_data.application.service import (
    BOARD_INDEXES,
    CAP_TIER_INDEXES,
)
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar
from backend.tests.integration.market_data.test_market_flow import _seed, service

INDEXES = CAP_TIER_INDEXES
NAMES = dict(INDEXES)


def _seed_bars(env, symbol: str, trade_date: date, close: float) -> None:
    _seed(env,
          "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
          "VALUES (:c, :n, 'index', 'tushare') ON CONFLICT DO NOTHING",
          {"c": symbol, "n": NAMES.get(symbol, symbol)})
    _seed(env,
          "INSERT INTO market.instrument_daily "
          "(ts_code, trade_date, open, high, low, close, vol, source) "
          "VALUES (:c, :d, 100, 100, 100, :close, 1000, 'tushare') "
          "ON CONFLICT DO NOTHING",
          {"c": symbol, "d": trade_date, "close": close})


def test_trends_empty_window_keeps_series_entries(env, service):
    """空窗口契约：全部序列区间无行 → series 条目保留且 points == []、
    as_of=None → UNAVAILABLE（不抛错、不 404）。"""
    svc = service(calendar=FakeCalendar(trading_day=False, last_day=date(2026, 9, 4)))
    dto = svc.get_index_trends(
        indexes=INDEXES, from_date=date(2026, 9, 1), to_date=date(2026, 9, 4))
    assert [s.symbol for s in dto.series] == [c for c, _ in INDEXES]
    assert [s.name for s in dto.series] == [n for _, n in INDEXES]
    assert all(s.points == [] for s in dto.series)
    assert dto.as_of is None
    assert dto.freshness_status == "UNAVAILABLE"
    assert (dto.from_date, dto.to_date) == (date(2026, 9, 1), date(2026, 9, 4))


def test_trends_as_of_and_freshness_derivation(env, service):
    """as_of = 全组末点最大值（全历史口径，与 get_bars latest 同款）；freshness
    按最近交易日推导：末点=最近交易日 → FRESH、落后 → STALE、无行 → UNAVAILABLE。"""
    _seed_bars(env, "000300.SH", date(2026, 9, 4), 3340.0)
    _seed_bars(env, "000852.SH", date(2026, 9, 4), 6100.0)
    _seed_bars(env, "000905.SH", date(2026, 8, 1), 5000.0)    # 区间外历史行 → points 空
    _seed_bars(env, "932000.CSI", date(2026, 9, 3), 2300.0)   # 末点落后最近交易日
    calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    dto = service(calendar=calendar).get_index_trends(
        indexes=INDEXES, from_date=date(2026, 9, 1), to_date=date(2026, 9, 4))
    by_symbol = {s.symbol: s for s in dto.series}
    assert [p["date"] for p in by_symbol["000300.SH"].points] == [date(2026, 9, 4)]
    assert by_symbol["000300.SH"].points[0]["close"] == 3340.0
    assert by_symbol["000905.SH"].points == []   # 区间外历史不产出（空窗口契约）
    assert by_symbol["932000.CSI"].points[0]["close"] == 2300.0
    # 全组末点最大值 = 2026-09-04 ≥ 最近交易日 → FRESH
    assert dto.as_of == date(2026, 9, 4)
    assert dto.freshness_status == "STALE"  # peer indexes have not reached the target
    # 组内末点落后于最近交易日 → STALE
    dto2 = service(calendar=calendar).get_index_trends(
        indexes=[("932000.CSI", "中证2000")],
        from_date=date(2026, 9, 1), to_date=date(2026, 9, 4))
    assert dto2.as_of == date(2026, 9, 3)
    assert dto2.freshness_status == "STALE"
    # 板组同机制抽查：BOARD_INDEXES 三码无行 → 条目保留 + UNAVAILABLE
    dto3 = service(calendar=calendar).get_index_trends(
        indexes=BOARD_INDEXES, from_date=date(2026, 9, 1), to_date=date(2026, 9, 4))
    assert [s.symbol for s in dto3.series] == [c for c, _ in BOARD_INDEXES]
    assert all(s.points == [] for s in dto3.series)
    assert dto3.freshness_status == "UNAVAILABLE"


def test_trends_from_after_to_raises(env, service):
    svc = service(calendar=FakeCalendar())
    with pytest.raises(RangeTooLargeError):
        svc.get_index_trends(
            indexes=INDEXES, from_date=date(2026, 9, 5), to_date=date(2026, 9, 1))
