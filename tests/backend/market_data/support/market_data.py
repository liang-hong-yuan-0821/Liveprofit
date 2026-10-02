"""Shared fixtures/builders for tests.backend.market_data.contract.api.test_market_data; no test cases."""

from __future__ import annotations
from datetime import date, timedelta
import pytest
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar


def _seed_instrument(client, symbol: str, name: str = "上证综指") -> None:
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
                "VALUES (:ts_code, :name, 'index', 'tushare') "
                "ON CONFLICT (ts_code) DO NOTHING"
            ),
            {"ts_code": symbol, "name": name},
        )
    engine.dispose()


def _seed_bars(client, symbol: str = "000001.SH", trade_date: date = date(2026, 9, 4),
               close: float = 3340.0) -> None:
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    _seed_instrument(client, symbol)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO market.instrument_daily "
                "(ts_code, trade_date, open, high, low, close, vol, source) "
                "VALUES (:ts_code, :trade_date, 3300, 3350, 3290, :close, 1000000, 'tushare') "
                "ON CONFLICT (ts_code, trade_date) DO NOTHING"
            ),
            {"ts_code": symbol, "trade_date": trade_date, "close": close},
        )
    engine.dispose()


def _seed_dense_bars(
    client, symbol: str = "000001.SH", start: date = date(2026, 4, 7), days: int = 150
) -> None:
    """密集种连续自然日（含周末；seed 不校验交易日），close = 3300 + offset 线性递增。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    _seed_instrument(client, symbol)
    with engine.begin() as conn:
        for offset in range(days):
            trading_date = start + timedelta(days=offset)
            close = 3300.0 + offset
            conn.execute(
                text(
                    "INSERT INTO market.instrument_daily "
                    "(ts_code, trade_date, open, high, low, close, vol, source) "
                    "VALUES (:ts_code, :trade_date, :open, :high, :low, :close, 1000000, 'tushare') "
                    "ON CONFLICT (ts_code, trade_date) DO NOTHING"
                ),
                {
                    "ts_code": symbol,
                    "trade_date": trading_date,
                    "open": close - 10.0,
                    "high": close + 20.0,
                    "low": close - 20.0,
                    "close": close,
                },
            )
    engine.dispose()


def _seed_dense_factors(
    client, symbol: str = "000001.SH", start: date = date(2026, 4, 7), days: int = 150
) -> None:
    """与 _seed_dense_bars 同区间 seed 因子行：因子值 = close + 固定偏移（确定性公式，便于逐值断言）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        for offset in range(days):
            trading_date = start + timedelta(days=offset)
            close = 3300.0 + offset
            conn.execute(
                text(
                    "INSERT INTO market.factor_daily "
                    "(ts_code, trade_date, ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_60, "
                    "boll_mid_bfq, boll_upper_bfq, boll_lower_bfq, "
                    "macd_dif_bfq, macd_dea_bfq, macd_bfq) "
                    "VALUES (:ts_code, :trade_date, :ma5, :ma10, :ma20, :ma60, "
                    ":bmid, :bup, :blow, :mdif, :mdea, :mhist) "
                    "ON CONFLICT (ts_code, trade_date) DO NOTHING"
                ),
                {
                    "ts_code": symbol,
                    "trade_date": trading_date,
                    "ma5": close + 1.0, "ma10": close + 10.0,
                    "ma20": close + 20.0, "ma60": close + 60.0,
                    "bmid": close + 2.0, "bup": close + 30.0, "blow": close - 30.0,
                    "mdif": close + 0.5, "mdea": close + 0.3, "mhist": close + 0.4,
                },
            )
    engine.dispose()


def _seed_sector_daily(client) -> None:
    """seed 板块日线（M4 定稿用例矩阵）：
    - dc BK1753 = 15 行（>10：完整公式）、BK1754 = 5 行（2-10：vol_change=0）、
      BK1755 = 1 行（≤1：pct_chg×0.6）
    - ths 883300.TI = 15 行（应被 source='dc' 过滤）
    全部 close 100+i、vol=100、pct_chg=1.0（BK1755 用 3.0 供降级断言）。
    """
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    start = date(2026, 8, 24)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO market.sector (source, sector_code, name, type) VALUES "
            "('dc', 'BK1753', '光刻胶', 'N'), ('dc', 'BK1754', '半导体', 'N'), "
            "('dc', 'BK1755', '芯片', 'N'), ('ths', '883300.TI', '光刻胶ths', 'N') "
            "ON CONFLICT DO NOTHING"))
        rows_map = {"BK1753": 15, "BK1754": 5, "BK1755": 1, "883300.TI": 15}
        for code, n in rows_map.items():
            source = "ths" if code.startswith("883") else "dc"
            for offset in range(n):
                trading_date = start + timedelta(days=offset + 15 - n)
                pct = 3.0 if code == "BK1755" else 1.0
                conn.execute(
                    text(
                        "INSERT INTO market.sector_daily "
                        "(source, sector_code, trade_date, open, high, low, close, pct_chg, vol, amount) "
                        "VALUES (:source, :code, :d, :close, :close, :close, :close, :pct, 100, 1000) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"source": source, "code": code, "d": trading_date,
                     "close": 100.0 + offset, "pct": pct},
                )
    engine.dispose()


def _seed_concept_tree(client) -> None:
    """seed 概念树（tree 用例，板块概念Treemap方案 3.2.3）：
    - sector_daily 复用 _seed_sector_daily（BK1753 15 行 / BK1754 5 行 / BK1755 1 行，
      as_of=2026-09-07）
    - BK1753 103 成分：600000.SH~600102.SH，pct_chg=(i-51)*0.1 + i*0.0001
      （正负覆盖、绝对值严格递增——避免 |pct| 平手依赖 SQL 返回序）；
      600102.SH 无 instrument 行（名称兜底断言）；600000.SH 无日线行（停牌）
    - BK1754 2 成分：300001.SZ（pct 5.0）、300002.SZ（无日线行 → 停牌 null）
    """
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    as_of = date(2026, 9, 7)
    with engine.begin() as conn:
        for i in range(103):
            ts_code = f"6{i:05d}.SH"
            if i != 102:  # 600102.SH 无 instrument 行：COALESCE 名称兜底 ts_code
                conn.execute(
                    text(
                        "INSERT INTO market.instrument "
                        "(ts_code, name, instrument_type, data_source) "
                        "VALUES (:c, :n, 'stock', 'tushare') "
                        "ON CONFLICT (ts_code) DO NOTHING"
                    ),
                    {"c": ts_code, "n": f"成分{i}"},
                )
            conn.execute(
                text(
                    "INSERT INTO market.sector_member (source, sector_code, ts_code) "
                    "VALUES ('dc', 'BK1753', :c) ON CONFLICT DO NOTHING"
                ),
                {"c": ts_code},
            )
            if i != 0:  # 600000.SH 停牌：无日线行 → pct_chg null
                conn.execute(
                    text(
                        "INSERT INTO market.instrument_daily "
                        "(ts_code, trade_date, open, high, low, close, pct_chg, vol, source) "
                        "VALUES (:c, :d, 10, 10, 10, 10, :pct, 100, 'tushare') "
                        "ON CONFLICT (ts_code, trade_date) DO NOTHING"
                    ),
                    {"c": ts_code, "d": as_of, "pct": (i - 51) * 0.1 + i * 0.0001},
                )
        for ts_code, name, pct in (("300001.SZ", "成分B", 5.0), ("300002.SZ", "成分C", None)):
            conn.execute(
                text(
                    "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
                    "VALUES (:c, :n, 'stock', 'tushare') ON CONFLICT (ts_code) DO NOTHING"
                ),
                {"c": ts_code, "n": name},
            )
            conn.execute(
                text(
                    "INSERT INTO market.sector_member (source, sector_code, ts_code) "
                    "VALUES ('dc', 'BK1754', :c) ON CONFLICT DO NOTHING"
                ),
                {"c": ts_code},
            )
            if pct is not None:
                conn.execute(
                    text(
                        "INSERT INTO market.instrument_daily "
                        "(ts_code, trade_date, open, high, low, close, pct_chg, vol, source) "
                        "VALUES (:c, :d, 10, 10, 10, 10, :pct, 100, 'tushare') "
                        "ON CONFLICT (ts_code, trade_date) DO NOTHING"
                    ),
                    {"c": ts_code, "d": as_of, "pct": pct},
                )
    engine.dispose()


def _seed_sector_daily_bars(client) -> None:
    """概念 K 线用例 seed（板块概念Treemap方案 3.3.3）：dc BK1753 三行完整 OHLC +
    一行 OHLC 缺列脏行（应被整行丢弃，BarDTO OHLC 必填 float）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO market.sector (source, sector_code, name, type) "
            "VALUES ('dc', 'BK1753', '光刻胶', 'N') ON CONFLICT DO NOTHING"))
        rows = [
            (date(2026, 9, 2), 100.0, 105.0, 99.0, 102.0, 1000.0),
            (date(2026, 9, 3), 102.0, 106.0, 100.0, 104.0, 1100.0),
            (date(2026, 9, 4), 104.0, 107.0, 101.0, 105.0, 1200.0),
            (date(2026, 9, 5), None, None, None, 106.0, 1300.0),  # 脏行
        ]
        for d, o, h, l, c, v in rows:
            conn.execute(
                text(
                    "INSERT INTO market.sector_daily "
                    "(source, sector_code, trade_date, open, high, low, close, vol) "
                    "VALUES ('dc', 'BK1753', :d, :o, :h, :l, :c, :v) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v},
            )
    engine.dispose()


def _seed_dense_sector_bars(client, sector_code: str = "BK1756",
                            start: date = date(2026, 7, 1), days: int = 40) -> None:
    """概念指标自算用例 seed（m7）：密集连续自然日 close = 100+offset 线性递增，
    OHLC 全有效（脏行路径由 _seed_sector_daily_bars 用例覆盖）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO market.sector (source, sector_code, name, type) "
            "VALUES ('dc', :code, '密集板块', 'N') ON CONFLICT DO NOTHING"),
            {"code": sector_code})
        for offset in range(days):
            trading_date = start + timedelta(days=offset)
            close = 100.0 + offset
            conn.execute(
                text(
                    "INSERT INTO market.sector_daily "
                    "(source, sector_code, trade_date, open, high, low, close, vol) "
                    "VALUES ('dc', :code, :d, :o, :h, :l, :c, 1000) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"code": sector_code, "d": trading_date, "o": close - 1.0,
                 "h": close + 1.0, "l": close - 2.0, "c": close},
            )
    engine.dispose()


def _seed_stock_bars(client) -> None:
    """个股 K 线用例 seed：instrument(stock) + instrument_daily 两行 OHLC；
    另 seed 基金 510300.SH（fund 应 404）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.contract_env import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        for ts_code, name, itype in (
            ("600519.SH", "贵州茅台", "stock"), ("510300.SH", "沪深300ETF", "fund")):
            conn.execute(
                text(
                    "INSERT INTO market.instrument (ts_code, name, instrument_type, data_source) "
                    "VALUES (:c, :n, :t, 'tushare') ON CONFLICT (ts_code) DO NOTHING"
                ),
                {"c": ts_code, "n": name, "t": itype},
            )
        for offset in range(2):
            trading_date = date(2026, 9, 3) + timedelta(days=offset)
            close = 1500.0 + offset
            conn.execute(
                text(
                    "INSERT INTO market.instrument_daily "
                    "(ts_code, trade_date, open, high, low, close, vol, source) "
                    "VALUES ('600519.SH', :d, :o, :h, :l, :c, 5000, 'tushare') "
                    "ON CONFLICT (ts_code, trade_date) DO NOTHING"
                ),
                {"d": trading_date, "o": close - 5.0, "h": close + 10.0,
                 "l": close - 10.0, "c": close},
            )
    engine.dispose()
