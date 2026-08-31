"""
单元测试：全市场日线本地库 DAO（stock_daily_dao，方案 3.6）

- is_fund_ts_code 前缀函数边界（方案指定 7 个用例）
- 批量写入：close NaN 行 drop（计日志）、NaN→None、COPY 临时表 + ON CONFLICT 语句形态、
  DO NOTHING / DO UPDATE 两态、临时表用完即删
- upsert_stock_basic / upsert_concepts / upsert_concept_members（source 列、ts_code→concept_code 重命名）
- get_qfq_daily 前复权公式（构造 2 因子序列手算对照、因子缺失日）
- get_daily / get_cross_section 查询
"""

from datetime import date

import pandas as pd
import pytest

from AI.dataflows.store import stock_daily_dao as dao


# ==================== helpers ====================

class _FakeCopier:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        self.rows.append(row)


class _FakeCursor:
    """伪游标：记录 executed SQL，COPY 行写入 copier.rows。"""

    def __init__(self, rows=None, description=()):
        self.rows = rows or []
        self.description = description
        self.executed = []
        self.copier = None

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def copy(self, sql):
        self.executed.append(sql)
        self.copier = _FakeCopier()
        return self.copier

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Col:
    """伪列描述（psycopg description 元素，含 .name）。"""

    def __init__(self, name):
        self.name = name


class _FakeConn:
    """伪连接：conn.execute 按 SQL 子串匹配返回预设行集（查询函数用）；
    conn.cursor 返回共享伪游标（批量写入用）。"""

    def __init__(self, matchers=None):
        self.matchers = matchers or {}
        self.cursor_obj = _FakeCursor()
        self.commits = 0

    def execute(self, sql, params=None):
        rows = None
        for key, r in self.matchers.items():
            if key in sql:
                rows = r
                break
        self.cursor_obj.rows = rows if rows is not None else []
        self.cursor_obj.executed.append(sql)
        return self.cursor_obj

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


# ==================== is_fund_ts_code 前缀函数 ====================

@pytest.mark.parametrize("ts_code,expected", [
    ("600000.SH", False),   # 沪主板股票
    ("688981.SH", False),   # 科创板股票
    ("159919.SZ", True),    # 深市 ETF
    ("920992.BJ", False),   # 北交所股票
    ("000001.SZ", False),   # 深主板股票
    ("510300.SH", True),    # 沪市 ETF
    ("161725.SZ", True),    # 深市 LOF
])
def test_is_fund_ts_code_boundary(ts_code, expected):
    assert dao.is_fund_ts_code(ts_code) is expected


def test_is_fund_ts_code_no_suffix():
    assert dao.is_fund_ts_code("510300") is False


# ==================== bulk_upsert_daily：清洗与语句形态 ====================

def _daily_df(rows):
    return pd.DataFrame(rows, columns=dao.DAILY_COLS)


def test_bulk_upsert_daily_drops_close_nan_and_nan_to_none(caplog):
    conn = _FakeConn()
    df = _daily_df([
        ("000001.SZ", "20260828", 11.0, 11.5, 11.0, 11.4, 11.3, 0.1, 0.88, 100.0, 1000.0),
        ("600000.SH", "20260828", 8.0, 8.2, 8.0, float("nan"), 8.1, None, None, None, None),
        ("300001.SZ", "20260828", None, None, None, 5.0, float("nan"), None, None,
         float("nan"), None),
    ])
    n = dao.bulk_upsert_daily(conn, df)
    assert n == 2  # close NaN 行已 drop
    assert "close 为 NaN 已 drop" in caplog.text

    written = conn.cursor_obj.copier.rows
    assert len(written) == 2
    first = written[0]
    assert first[0] == "000001.SZ" and first[1] == "2026-08-28"
    # 其余 NaN → None：第三行 open/pre_close/vol 原为 NaN/None
    second = written[1]
    assert second[2] is None      # open
    assert second[6] is None      # pre_close
    assert second[8] is None      # vol


def test_bulk_upsert_daily_sql_shape_do_nothing():
    conn = _FakeConn()
    df = _daily_df([("000001.SZ", "20260828", 11.0, 11.5, 11.0, 11.4,
                     11.3, 0.1, 0.88, 100.0, 1000.0)])
    dao.bulk_upsert_daily(conn, df, update=False)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert 'CREATE TEMP TABLE "tmp_stock_daily_' in sqls
    # LIKE 必须带 INCLUDING DEFAULTS：否则 updated_at（DEFAULT now()）COPY 时 NULL 违例
    assert 'INCLUDING DEFAULTS' in sqls
    assert 'COPY "tmp_stock_daily_' in sqls
    assert 'INSERT INTO "stock_daily"' in sqls
    assert 'ON CONFLICT ("ts_code", "trade_date") DO NOTHING' in sqls
    assert 'DROP TABLE IF EXISTS "tmp_stock_daily_' in sqls


def test_bulk_upsert_daily_sql_shape_do_update():
    conn = _FakeConn()
    df = _daily_df([("000001.SZ", "20260828", 11.0, 11.5, 11.0, 11.4,
                     11.3, 0.1, 0.88, 100.0, 1000.0)])
    dao.bulk_upsert_daily(conn, df, update=True)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "ON CONFLICT (\"ts_code\", \"trade_date\") DO UPDATE SET" in sqls
    # 全列（不含 trade_date，PK 列除外）均更新
    for col in ("open", "high", "low", "close", "pre_close", "change",
                "pct_chg", "vol", "amount"):
        assert f'"{col}" = EXCLUDED."{col}"' in sqls
    assert 'ts_code" = EXCLUDED."ts_code"' not in sqls
    assert 'trade_date" = EXCLUDED."trade_date"' not in sqls


def test_bulk_upsert_factor_nan_to_none():
    conn = _FakeConn()
    df = pd.DataFrame([
        ("000001.SZ", "20260828", 1.0),
        ("600000.SH", "20260828", float("nan")),
    ], columns=dao.FACTOR_COLS)
    n = dao.bulk_upsert_factor(conn, df)
    assert n == 2
    written = conn.cursor_obj.copier.rows
    assert written[0][2] == 1.0
    assert written[1][2] is None
    assert "ON CONFLICT (\"ts_code\", \"trade_date\") DO NOTHING" in " ".join(
        conn.cursor_obj.executed)


# ==================== upsert 基本信息 / 概念 ====================

def test_upsert_stock_basic_sql_shape():
    conn = _FakeConn()
    df = pd.DataFrame([("000001.SZ", "平安银行", "主板", "SZSE", "银行", "深圳",
                        "L", "19910403", None)],
                      columns=dao.STOCK_BASIC_COLS)
    n = dao.upsert_stock_basic(conn, df)
    assert n == 1
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'INSERT INTO "stock_basic"' in sqls
    assert 'ON CONFLICT ("ts_code") DO UPDATE SET' in sqls
    written = conn.cursor_obj.copier.rows
    assert written[0][7] == "1991-04-03"   # list_date 归一 ISO
    assert written[0][8] is None           # delist_date


def test_upsert_concepts_renames_and_source():
    conn = _FakeConn()
    # count 用 float（tushare 实测返回 300.0）且多行含 None——须转 Python int，
    # 否则 COPY 报 invalid input syntax for type integer（单行场景会被
    # Series.apply 的 dtype 推断骗过：单行推 int64、多行含 None 压回 float64）
    df = pd.DataFrame([
        ("883300.TI", "沪深300样本股", 300.0, "A", "20100413", "N"),
        ("883301.TI", "概念B", 50.0, "A", "20200101", "N"),
        ("883302.TI", "概念C", None, "A", "20200101", "N"),
    ], columns=["ts_code", "name", "count", "exchange", "list_date", "type"])
    df["source"] = "ths"
    n = dao.upsert_concepts(conn, df)
    assert n == 3
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'INSERT INTO "concept"' in sqls
    assert 'ON CONFLICT ("source", "concept_code") DO UPDATE SET' in sqls
    written = conn.cursor_obj.copier.rows
    assert written[0][0] == "ths" and written[0][1] == "883300.TI"
    assert written[0][3] == 300 and isinstance(written[0][3], int)
    assert written[1][3] == 50 and isinstance(written[1][3], int)
    assert written[2][3] is None            # count NaN → NULL
    assert written[0][5] == "2010-04-13"


def test_upsert_concepts_missing_source_raises():
    df = pd.DataFrame([("883300.TI", "沪深300样本股", 300, "A", "20100413", "N")],
                      columns=["ts_code", "name", "count", "exchange",
                               "list_date", "type"])
    with pytest.raises(ValueError):
        dao.upsert_concepts(_FakeConn(), df)


def test_upsert_concept_members_all_pk_do_update():
    conn = _FakeConn()
    df = pd.DataFrame([("883300.TI", "000001.SZ")],
                      columns=["concept_code", "ts_code"])
    df["source"] = "ths"
    n = dao.upsert_concept_members(conn, df)
    assert n == 1
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'ON CONFLICT ("source", "concept_code", "ts_code") DO UPDATE SET' in sqls


def test_upsert_concept_members_deduplicates_same_pk():
    # 双保险去重：同批次重复 PK 会使 INSERT ... ON CONFLICT DO UPDATE
    # 报 cannot affect row a second time
    conn = _FakeConn()
    df = pd.DataFrame([("883300.TI", "000001.SZ"),
                       ("883300.TI", "000001.SZ"),
                       ("883300.TI", "600000.SH")],
                      columns=["concept_code", "ts_code"])
    df["source"] = "ths"
    n = dao.upsert_concept_members(conn, df)
    assert n == 2
    written = conn.cursor_obj.copier.rows
    assert [r[2] for r in written] == ["000001.SZ", "600000.SH"]


# ==================== 查询 ====================

def test_get_daily_orders_by_trade_date():
    # 伪连接不执行 ORDER BY，行序即"SQL 已排序"的返回（DAO 依赖 SQL 排序）
    rows = [
        ("000001.SZ", date(2026, 8, 26), 9.9, 10.1, 9.8, 10.1, 10.0,
         0.1, 1.0, 90.0, 900.0),
        ("000001.SZ", date(2026, 8, 27), 10.0, 10.5, 9.8, 10.2, 10.1,
         0.1, 0.99, 100.0, 1000.0),
    ]
    conn = _FakeConn({"FROM stock_daily": rows})
    df = dao.get_daily(conn, "000001.SZ", "2026-08-01", "2026-08-31")
    assert df["trade_date"].tolist() == [date(2026, 8, 26), date(2026, 8, 27)]
    sql = conn.cursor_obj.executed[-1]
    assert "ORDER BY trade_date" in sql


def test_get_cross_section_filters_by_prefix():
    rows = [
        ("000001.SZ", date(2026, 8, 28), 10.0, 10.5, 9.8, 10.2, 10.1,
         0.1, 0.99, 100.0, 1000.0),
        ("510300.SH", date(2026, 8, 28), 4.0, 4.1, 3.9, 4.05, 4.0,
         0.05, 1.2, 500.0, 5000.0),
        ("159919.SZ", date(2026, 8, 28), 2.0, 2.1, 1.9, 2.05, 2.0,
         0.05, 2.5, 300.0, 3000.0),
    ]
    conn = _FakeConn({"FROM stock_daily": rows})
    stocks = dao.get_cross_section(conn, "2026-08-28", market="stock")
    assert stocks["ts_code"].tolist() == ["000001.SZ"]
    funds = dao.get_cross_section(conn, "2026-08-28", market="fund")
    assert funds["ts_code"].tolist() == ["510300.SH", "159919.SZ"]
    all_rows = dao.get_cross_section(conn, "2026-08-28")
    assert len(all_rows) == 3   # market=None 不分类


def test_get_basic_stock_then_fund():
    conn = _FakeConn()
    # 模拟 cursor：stock_basic 命中
    conn.cursor_obj.rows = [("000001.SZ", "平安银行")]
    conn.cursor_obj.description = [_Col("ts_code"), _Col("name")]
    result = dao.get_basic(conn, "000001.SZ")
    assert result == {"ts_code": "000001.SZ", "name": "平安银行"}
    # stock_basic 未命中 → fund_basic
    conn.cursor_obj.rows = []
    assert dao.get_basic(conn, "000001.SZ") is None


# ==================== get_qfq_daily 前复权公式 ====================

def _qfq_conn(daily_rows, factor_rows):
    return _FakeConn({
        "FROM stock_daily": daily_rows,
        "FROM adj_factor": factor_rows,
    })


def test_get_qfq_daily_formula_hand_computed():
    daily_rows = [
        ("000001.SZ", date(2026, 1, 5), 10.0, 10.5, 9.8, 10.0, 9.9,
         0.1, 1.0, 100.0, 1000.0),
        ("000001.SZ", date(2026, 1, 6), 20.0, 20.5, 19.8, 20.0, 19.9,
         0.1, 0.5, 200.0, 2000.0),
        ("000001.SZ", date(2026, 1, 7), 30.0, 30.5, 29.8, 30.0, 29.9,
         0.1, 0.3, 300.0, 3000.0),
    ]
    factor_rows = [
        (date(2026, 1, 5), 1.0),
        (date(2026, 1, 6), 2.0),
        # 1/7 因子缺失（停牌/新上市场景）
    ]
    df = dao.get_qfq_daily(_qfq_conn(daily_rows, factor_rows),
                           "000001.SZ", "2026-01-01", "2026-01-31")
    # factor_latest = 2.0（区间内最新因子）
    # qfq = x * factor_t / factor_latest
    assert df["qfq_close"].iloc[0] == pytest.approx(10.0 * 1.0 / 2.0)
    assert df["qfq_close"].iloc[1] == pytest.approx(20.0 * 2.0 / 2.0)
    assert pd.isna(df["qfq_close"].iloc[2])   # 因子缺失日 → NaN
    # 原始列保留、adj_factor 不输出
    assert "close" in df.columns
    assert "adj_factor" not in df.columns
    assert set(df.columns) == set(dao.DAILY_COLS +
                                  ["qfq_open", "qfq_high", "qfq_low", "qfq_close"])


def test_get_qfq_daily_no_factor_returns_none_qfq():
    daily_rows = [
        ("000001.SZ", date(2026, 1, 5), 10.0, 10.5, 9.8, 10.0, 9.9,
         0.1, 1.0, 100.0, 1000.0),
    ]
    df = dao.get_qfq_daily(_qfq_conn(daily_rows, []),
                           "000001.SZ", "2026-01-01", "2026-01-31")
    assert df["qfq_close"].isna().all()


def test_get_qfq_daily_empty_daily():
    df = dao.get_qfq_daily(_qfq_conn([], []), "000001.SZ",
                           "2026-01-01", "2026-01-31")
    assert df.empty
    assert set(df.columns) == set(dao.DAILY_COLS +
                                  ["qfq_open", "qfq_high", "qfq_low", "qfq_close"])
