"""market_data_dao 包装兼容测试（方案 3.3.1 dtype 契约定稿）。

假 DAO 注入方案（不依赖 PG 环境，与"假 DAO 注入"的既有测试同构）：
断言 wrapper 输出 ts 为 datetime64——market.instrument_daily.trade_date 是 DATE
（psycopg 返 datetime.date → pandas object dtype），不归一则 event_study.py:158
的 df["ts"].dt.date 抛 AttributeError（既有测试 monkeypatch 直替函数、mock 帧
不经过真实 psycopg 类型路径，兜不住——本测试补真实类型路径断言）。
"""

from datetime import date

import pandas as pd
import pytest

from AI.eventStudy.db import market_data_dao as dao


class _FakeConn:
    """伪连接：ticker 查询返回预设行；其余经 monkeypatch 的假 DAO 注入。"""

    def __init__(self, ticker="000001.SH"):
        self._ticker = ticker

    def execute(self, sql, params=None):
        if "FROM assets" in sql:
            return _FakeResult([(self._ticker,)])
        return _FakeResult([])


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _daily_frame_rows():
    """假 DAO 帧：trade_date 为 datetime.date（模拟真实 psycopg DATE 返回）。"""
    return [
        ("000001.SH", date(2026, 9, 4), 3300.0, 3350.0, 3290.0, 3340.0,
         1_000_000.0, 1_000.0),
        ("000001.SH", date(2026, 9, 5), 3340.0, 3360.0, 3330.0, 3355.0,
         1_100_000.0, 1_100.0),
    ]


def test_get_market_data_ts_is_datetime64(monkeypatch):
    import db.instrument.dao.instrument_daily as daily_dao

    fake_frame = pd.DataFrame(_daily_frame_rows(), columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"])
    monkeypatch.setattr(daily_dao, "query_range",
                        lambda conn, ts_code, start, end: fake_frame)

    df = dao.get_market_data(_FakeConn(), 1, "2026-09-01", "2026-09-30")
    # dtype 契约：ts 为 datetime64（真实库 psycopg 返 date → object，必须归一）
    assert df["ts"].dtype.kind == "M"
    assert df["ts"].dt.date.tolist() == [date(2026, 9, 4), date(2026, 9, 5)]
    # 兼容列：adj_close = close 别名（硬编码期望列集，不用模块常量自证）
    assert df["adj_close"].tolist() == [3340.0, 3355.0]
    assert set(df.columns) == {"ts", "open", "high", "low", "adj_close", "vol", "amount"}


def test_get_market_data_empty_frame_keeps_columns(monkeypatch):
    import db.instrument.dao.instrument_daily as daily_dao

    monkeypatch.setattr(daily_dao, "query_range",
                        lambda conn, ts_code, start, end: pd.DataFrame())
    df = dao.get_market_data(_FakeConn(), 1, "2026-09-01", "2026-09-30")
    assert df.empty
    assert set(df.columns) == {"ts", "open", "high", "low", "adj_close", "vol", "amount"}


def test_get_market_data_unknown_asset_returns_empty(monkeypatch):
    df = dao.get_market_data(_FakeConn(ticker=None), 999, "2026-09-01", "2026-09-30")
    assert df.empty


def test_get_daily_returns_first_row_nan(monkeypatch):
    import db.instrument.dao.instrument_daily as daily_dao

    fake_frame = pd.DataFrame(_daily_frame_rows(), columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"])
    monkeypatch.setattr(daily_dao, "query_range",
                        lambda conn, ts_code, start, end: fake_frame)

    df = dao.get_daily_returns(_FakeConn(), 1, "2026-09-01", "2026-09-30")
    assert df["ts"].dtype.kind == "M"
    assert pd.isna(df["ret"].iloc[0])  # 首行无前收盘
    assert df["ret"].iloc[1] == pytest.approx(3355.0 / 3340.0 - 1)
    assert set(df.columns) == {"ts", "adj_close", "ret"}
