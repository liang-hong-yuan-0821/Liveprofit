"""
单元测试：migrate_legacy 存量迁移脚本（方案 3.2.3 小样本适配）

伪连接断言 SQL 形态（market./public. 前缀限定、ON CONFLICT 形态、DO UPDATE
覆盖列集、checkpoint 跳过逻辑、源表缺失降级、--drop 清单）。真实数据迁移
在 T8 维护窗口执行（行数核对报告即验收），小样本真实库集成随 T5 测试库基建。
"""

import json

import pytest

from db.instrument.migration import migrate_legacy as mg
from tests.db.instrument._helpers import _FakeConn


@pytest.fixture(autouse=True)
def _tmp_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(mg, "CHECKPOINT_PATH", tmp_path / "ckpt.json")


def _exists_row(qualified):
    return (qualified,)


def _conn(*matchers, **tables):
    """构造伪连接：宽键 to_regclass 控制表存在性，精确键（后插入优先）匹配查询。"""
    base = {"to_regclass": [_exists_row(q) for q in tables if tables[q]]}
    base.update(dict(matchers))
    return _FakeConn(base)


def test_step1_sql_shape_and_checkpoint():
    conn = _conn(
        ("count(*) FROM public.stock_daily", [(1000,)]),
        ("DISTINCT ts_code FROM public.stock_daily", [("000001.SZ",)]),
        ("count(*) FROM market.instrument_daily", [(1000,)]),
        public_stock_daily=True,
    )
    ckpt = {}
    result = mg._step1_stock_daily(conn, ckpt, "2026-09-13T00:00:00Z")
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "INSERT INTO market.instrument_daily" in sqls
    assert "FROM public.stock_daily" in sqls
    assert "ON CONFLICT DO NOTHING" in sqls
    assert "'tushare'" in sqls  # source 填 tushare
    assert "1:000001.SZ" in ckpt  # 批次 checkpoint 落盘
    assert result["skipped"] is False


def test_step1_skips_checkpointed_batch():
    conn = _conn(
        ("count(*) FROM public.stock_daily", [(1000,)]),
        ("DISTINCT ts_code FROM public.stock_daily", [("000001.SZ",)]),
        ("count(*) FROM market.instrument_daily", [(1000,)]),
        public_stock_daily=True,
    )
    ckpt = {"1:000001.SZ": True}
    mg._step1_stock_daily(conn, ckpt, "2026-09-13T00:00:00Z")
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "INSERT INTO market.instrument_daily" not in sqls  # 批次已跳过


def test_step2_do_update_covers_ohlc_not_amount():
    conn = _conn(
        ("count(*) FROM public.market_data", [(800,)]),
        ("count(*) FROM public.market_bars_daily", [(300,)]),
        public_market_data=True, public_assets=True,
        public_market_bars_daily=True, public_market_assets=True,
    )
    ckpt = {}
    mg._step2_index_daily(conn, ckpt, "2026-09-13T00:00:00Z")
    sqls = " ||| ".join(conn.cursor_obj.executed)
    # 第一源 eventStudy：adj_close→close、ts::date、恒 NULL 三列
    assert "a.ticker" in sqls and "m.ts::date" in sqls and "m.adj_close" in sqls
    assert "NULL, NULL, NULL, m.vol" in sqls  # pre_close/change/pct_chg 恒 NULL
    # 第二源 DO UPDATE：覆盖 OHLC/vol/source/updated_at，不覆盖 amount/pre_close
    assert "ON CONFLICT (ts_code, trade_date) DO UPDATE SET" in sqls
    for col in ("open", "high", "low", "close", "vol", "source", "updated_at"):
        assert f"{col} = EXCLUDED.{col}" in sqls
    assert "amount = EXCLUDED.amount" not in sqls
    assert "pre_close = EXCLUDED.pre_close" not in sqls
    assert "change = EXCLUDED.change" not in sqls
    assert "pct_chg = EXCLUDED.pct_chg" not in sqls


def test_step4_close_backfill_uses_market_prefix():
    conn = _conn(
        ("count(*) FROM public.market_index_factors", [(7000,)]),
        public_market_index_factors=True, public_market_assets=True,
    )
    ckpt = {}
    mg._step4_factors(conn, ckpt)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "INSERT INTO market.factor_daily" in sqls
    assert "FROM public.market_index_factors" in sqls
    # close 反查：目标表显式 market. 前缀（同名表防自引用）
    assert "UPDATE market.factor_daily f SET close = d.close" in sqls
    assert "FROM market.instrument_daily d" in sqls


def test_step5_sector_rename_mapping():
    conn = _conn(
        ("count(*) FROM public.concept", [(2000,)]),
        ("count(*) FROM public.concept_member", [(120000,)]),
        ("DISTINCT source, concept_code FROM public.concept_member",
         [("ths", "883300.TI")]),
        public_concept=True, public_concept_member=True,
    )
    ckpt = {}
    mg._step5_sectors(conn, ckpt)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "INSERT INTO market.sector" in sqls
    assert "concept_code, name, count, exchange, list_date, type" in sqls  # type 原样迁
    assert "INSERT INTO market.sector_member" in sqls


def test_step6_industry_column_mapping():
    conn = _conn(
        ("count(*) FROM public.industry_codes", [(31,)]),
        public_industry_codes=True,
    )
    ckpt = {}
    mg._step6_industry(conn, ckpt)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "INSERT INTO market.industry (source, industry_code, name)" in sqls
    assert "SELECT source, code, name FROM public.industry_codes" in sqls
    assert "ON CONFLICT (source, industry_code) DO NOTHING" in sqls
    # count 不迁：INSERT 列清单不含 count（首期恒 NULL）
    insert_sql = sqls.split("FROM public.industry_codes")[0]
    assert ", count" not in insert_sql


def test_step7_fund_list_status_null_and_write_order():
    conn = _conn(
        ("count(*) FROM public.stock_basic", [(5551,)]),
        ("count(*) FROM public.fund_basic", [(2917,)]),
        ("count(*) FROM public.market_assets", [(12,)]),
        ("count(*) FROM public.assets", [(4,)]),
        public_stock_basic=True, public_fund_basic=True,
        public_market_assets=True, public_assets=True,
    )
    ckpt = {}
    mg._step7_instruments(conn, ckpt)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    # fund 行 list_status 恒 NULL（不推导不造数据）
    assert "SELECT ts_code, name, 'fund', NULL, list_date, delist_date" in sqls
    # 写入顺序：market_assets 先、assets 后（同键先到者胜 = 平台口径名称）
    assert sqls.index("FROM public.market_assets") < sqls.index("FROM public.assets")
    # fund_info 直映射
    assert "INSERT INTO market.fund_info" in sqls
    # stock_info 三列原文直迁
    assert "INSERT INTO market.stock_info (ts_code, exchange, market, area)" in sqls


def test_missing_tables_skipped():
    conn = _conn()
    ckpt = {}
    assert mg._step1_stock_daily(conn, ckpt, "x")["skipped"] is True
    assert mg._step3_adj_factor(conn, ckpt)["skipped"] is True
    report = mg._step2_index_daily(conn, ckpt, "x")
    assert report["eventstudy_src"] == "skipped(表缺失)"


def test_drop_only_store_and_eventstudy_tables(monkeypatch):
    conn = _conn(**{f"public_{t}": True for t in mg.DROP_TABLES})
    # _conn 的 tables 键带 public_ 前缀不是合格名——直接用宽键
    conn = _FakeConn({"to_regclass": [_exists_row(f"public.{t}") for t in mg.DROP_TABLES]})
    monkeypatch.setattr(mg, "get_connection", lambda: _ctx(conn))
    result = mg._run_drop()
    sqls = " ||| ".join(conn.cursor_obj.executed)
    for table in mg.DROP_TABLES:
        assert f"DROP TABLE IF EXISTS public.{table}" in sqls
    # backend 三表不在 --drop 清单（走 alembic 0007）
    assert "market_bars_daily" not in " ".join(mg.DROP_TABLES)
    assert "market_assets" not in " ".join(mg.DROP_TABLES)
    assert result["dropped"] == mg.DROP_TABLES


class _ctx:
    """伪 contextmanager：with 块内返回连接。"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def test_run_migration_summary_and_checkpoint_persistence(monkeypatch, tmp_path):
    conn = _FakeConn({
        "to_regclass": [],
        "count(*) FROM market.": [(0,)],
    })
    monkeypatch.setattr(mg, "get_connection", lambda: _ctx(conn))
    monkeypatch.setattr(mg, "CHECKPOINT_PATH", tmp_path / "ckpt.json")
    summary = mg.run_migration(drop=False)
    # 全部源表缺失 → 各步骤 skipped，不抛异常；行数核对仍输出
    assert "8 行数核对" in summary
    assert summary["1 个股基金日线"]["skipped"] is True
    # checkpoint 文件仅在批次完成时落盘（本场景无批次 → 不创建/空）
    ckpt = json.loads((tmp_path / "ckpt.json").read_text()) if (tmp_path / "ckpt.json").exists() else {}
    assert ckpt == {}
