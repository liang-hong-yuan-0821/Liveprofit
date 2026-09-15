"""指数采集集成测试（迁移自 backend/tests/integration/market_data/test_market_flow.py
的六个采集/仓库用例，3.3.4 定稿——fixture provider 工厂 + 真实测试库）。

- 指数日线/因子幂等重跑（DO UPDATE 行数不变）
- 因子 NaN 清洗（NaN→None）
- 65535 参数上限回归（bars/factor 各 >2000 行单批）
- 因子缺段拒绝部分入库
- 采集目标守卫（2026-09-14 US/KR 上线放宽：CN 格式或 NON_CN_INDEX_TARGETS
  白名单通过，白名单外非 CN 目标如 KOSDAQ 仍 ValueError）
"""

from datetime import date, timedelta

import pandas as pd
import pytest

import db.instrument.db as market_db
from db.instrument.db import get_connection
from db.instrument.ingest.incremental import (
    INDEX_TARGETS, _assert_index_targets_valid, _is_cn_index_code,
    _ingest_index_bars_and_factors,
)


@pytest.fixture
def conn(pg_env, clean_market_state):
    prev = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = pg_env["psycopg_dsn"]
    try:
        with get_connection() as c:
            yield c
    finally:
        market_db.PG_CONNECTION_STRING = prev


DAYS = ["20260904", "20260905"]


class FakeTushareProvider:
    def get_index_data_df(self, index_code, start_date, end_date):
        return pd.DataFrame(
            [
                {"trade_date": "20260904", "open": 3300.0, "high": 3350.0, "low": 3290.0,
                 "close": 3340.0, "pre_close": 3290.0, "change": 50.0, "pct_chg": 1.5,
                 "vol": 1_000_000.0, "amount": 1_000.0},
                {"trade_date": "20260905", "open": 3340.0, "high": 3360.0, "low": 3330.0,
                 "close": 3355.0, "pre_close": 3340.0, "change": 15.0, "pct_chg": 0.45,
                 "vol": 1_100_000.0, "amount": 1_100.0},
            ]
        )

    def get_index_factor_df(self, index_code, start_date, end_date, fields=None):
        return None


def test_index_bars_upsert_idempotent(conn):
    result = _ingest_index_bars_and_factors(
        conn, FakeTushareProvider(), None, DAYS)
    assert result["bars"] >= 2  # 13 个目标均返回同帧（每目标 2 行）
    first_total = conn.execute(
        "SELECT count(*) FROM market.instrument_daily").fetchone()[0]
    # 幂等重跑：DO UPDATE 行数不变
    _ingest_index_bars_and_factors(conn, FakeTushareProvider(), None, DAYS)
    second_total = conn.execute(
        "SELECT count(*) FROM market.instrument_daily").fetchone()[0]
    assert first_total == second_total
    # 自举落库：13 指数 instrument 行存在
    n_inst = conn.execute(
        "SELECT count(*) FROM market.instrument "
        "WHERE ts_code = ANY(%s)", (list(INDEX_TARGETS.keys()),)).fetchone()[0]
    assert n_inst == len(INDEX_TARGETS)
    # 扩列三列入库（pre_close/change/pct_chg）
    row = conn.execute(
        "SELECT pre_close, change, pct_chg FROM market.instrument_daily "
        "WHERE ts_code='000001.SH' AND trade_date='2026-09-04'").fetchone()
    assert row == (3290.0, 50.0, 1.5)


class FakeTushareFactorProvider:
    def get_index_data_df(self, index_code, start_date, end_date):
        return None  # 因子测试不依赖 bars

    def get_index_factor_df(self, index_code, start_date, end_date, fields=None):
        # 含一个 NaN 因子值（20260905 的 ma_bfq_5）：清洗为 None 入库
        return pd.DataFrame(
            [
                {"trade_date": "20260904", "close": 3340.0,
                 "ma_bfq_5": 3300.0, "ma_bfq_10": 3290.0, "ma_bfq_20": 3280.0, "ma_bfq_60": 3200.0,
                 "boll_mid_bfq": 3310.0, "boll_upper_bfq": 3400.0, "boll_lower_bfq": 3220.0,
                 "macd_dif_bfq": 10.5, "macd_dea_bfq": 8.2, "macd_bfq": 4.6},
                {"trade_date": "20260905", "close": 3355.0,
                 "ma_bfq_5": float("nan"), "ma_bfq_10": 3300.0, "ma_bfq_20": 3290.0, "ma_bfq_60": 3210.0,
                 "boll_mid_bfq": 3320.0, "boll_upper_bfq": 3410.0, "boll_lower_bfq": 3230.0,
                 "macd_dif_bfq": 11.0, "macd_dea_bfq": 8.8, "macd_bfq": 4.4},
            ]
        )


def test_index_factor_nan_cleaned_and_idempotent(conn):
    _ingest_index_bars_and_factors(
        conn, FakeTushareFactorProvider(), None, DAYS)
    rows = conn.execute(
        "SELECT trade_date, ma_bfq_10, macd_bfq, ma_bfq_5 FROM market.factor_daily "
        "WHERE ts_code='000001.SH' ORDER BY trade_date").fetchall()
    assert [(r[0], float(r[1]), float(r[2])) for r in rows] == [
        (date(2026, 9, 4), 3290.0, 4.6),
        (date(2026, 9, 5), 3300.0, 4.4),
    ]
    # NaN 清洗：ma_bfq_5 缺失为 None（数据缺失是事实，不写 NaN）
    assert float(rows[0][3]) == 3300.0
    assert rows[1][3] is None
    # 幂等重跑
    _ingest_index_bars_and_factors(conn, FakeTushareFactorProvider(), None, DAYS)
    total = conn.execute("SELECT count(*) FROM market.factor_daily").fetchone()[0]
    # 非 CN 目标（US 3 + KS11）不采因子 → 仅 CN 9 目标 × 2 行
    cn_count = sum(1 for c in INDEX_TARGETS if _is_cn_index_code(c))
    assert total == 2 * cn_count


class FakeLargeFactorProvider(FakeTushareFactorProvider):
    """单批超过分批上限（2000 行）的回归场景（2026-09-12 参数上限踩坑）。"""

    def __init__(self, rows: int = 2001) -> None:
        self._rows = rows

    def get_index_factor_df(self, index_code, start_date, end_date, fields=None):
        start = date(2026, 1, 1)
        return pd.DataFrame([
            {
                "trade_date": (start + timedelta(days=i)).isoformat(),
                "close": float(3000 + i),
                "ma_bfq_5": float(2990 + i), "ma_bfq_10": float(2980 + i),
                "ma_bfq_20": float(2970 + i), "ma_bfq_60": float(2960 + i),
                "boll_mid_bfq": float(3005 + i), "boll_upper_bfq": float(3100 + i),
                "boll_lower_bfq": float(2900 + i),
                "macd_dif_bfq": float(i) / 10, "macd_dea_bfq": float(i) / 20, "macd_bfq": float(i) / 30,
            }
            for i in range(self._rows)
        ])


def test_factor_ingestion_large_batch_beyond_param_limit(conn):
    """全历史规模（>2000 行）单资产入库：COPY 写入路径无参数上限
    （原 65535 参数上限回归场景——写路径已从 executemany 换为 COPY，
    大帧完整性保持回归价值，docstring 按新路径语义更新）。"""
    result = _ingest_index_bars_and_factors(
        conn, FakeLargeFactorProvider(rows=2001), None, DAYS)
    assert result["factors"] >= 2001
    total = conn.execute(
        "SELECT count(*) FROM market.factor_daily WHERE ts_code='000001.SH'").fetchone()[0]
    assert total == 2001
    first = conn.execute(
        "SELECT ma_bfq_5 FROM market.factor_daily WHERE ts_code='000001.SH' "
        "ORDER BY trade_date LIMIT 1").fetchone()
    assert float(first[0]) == 2990.0


class FakeLargeBarsProvider(FakeTushareProvider):
    def __init__(self, rows: int = 2001) -> None:
        self._rows = rows

    def get_index_data_df(self, index_code, start_date, end_date):
        start = date(2026, 1, 1)
        return pd.DataFrame([
            {
                "trade_date": (start + timedelta(days=i)).isoformat(),
                "open": float(3000 + i), "high": float(3010 + i),
                "low": float(2990 + i), "close": float(3005 + i),
                "pre_close": None, "change": None, "pct_chg": None,
                "vol": 1_000_000.0, "amount": 1_000.0,
            }
            for i in range(self._rows)
        ])


def test_bars_ingestion_large_batch_beyond_param_limit(conn):
    """bars 大帧（>2000 行）入库：COPY 路径无参数上限，大帧完整性回归。"""
    result = _ingest_index_bars_and_factors(
        conn, FakeLargeBarsProvider(rows=2001), None, DAYS)
    assert result["bars"] >= 2001
    total = conn.execute(
        "SELECT count(*) FROM market.instrument_daily WHERE ts_code='000001.SH'").fetchone()[0]
    assert total == 2001


class FakeMissingChunkProvider(FakeTushareFactorProvider):
    """分段拉取缺段：frame 带 attrs.missing_chunks>0 → 拒绝部分入库（Code Review major 回归）。"""

    def get_index_factor_df(self, index_code, start_date, end_date, fields=None):
        frame = super().get_index_factor_df(index_code, start_date, end_date, fields)
        frame.attrs["missing_chunks"] = 2
        return frame


def test_factor_ingestion_rejects_partial_backfill_with_missing_chunks(conn):
    result = _ingest_index_bars_and_factors(
        conn, FakeMissingChunkProvider(), None, DAYS)
    # 缺段拒绝后未入库
    assert result["factors"] == 0
    total = conn.execute("SELECT count(*) FROM market.factor_daily").fetchone()[0]
    assert total == 0


def test_whitelisted_non_cn_targets_pass_guard():
    """实测验收通过的 US/KR 白名单通过守卫（原 CN-only 语义反转，2026-09-14 上线）。"""
    _assert_index_targets_valid()  # 13 目标（9 CN + 4 白名单）不抛


def test_unknown_non_cn_target_rejected_by_guard(monkeypatch):
    """守卫保留防御：白名单外非 CN 目标（KOSDAQ）→ ValueError。"""
    monkeypatch.setitem(INDEX_TARGETS, "KOSDAQ", "韩国科斯达克指数")
    with pytest.raises(ValueError, match="未认可目标"):
        _assert_index_targets_valid()
