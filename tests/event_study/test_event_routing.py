"""
三级事件路由测试（T2 / T3，2026-09-11）

覆盖（方案第三章 / 任务 T2、T3 验收）：
- 迁移幂等：schema.sql + industry_codes.sql 可重复执行；路由列与两类索引就位；
  历史行（NULL）归一为 market + []，重复迁移不再变化（确定性、无 LLM）
- 路由查询：market 含历史 NULL 行；sector/stock 精确命中（同名事件不误命中）；
  多目标为并集（命中任一即属该路由）；空目标不成路由；时点排除（防前视）
  与 limit 降序
- 归一化与校验：作用域收敛三值、引用语法（前缀/大小写/裸码）归一、
  作用域—目标组合、引用存在性、行级失败异常（EventScopeValidationError）
  与"草稿不存在"（ValueError）语义隔离
- API 层（T3）：PredictRequest 新增 as_of / event_scope 的缺省语义（旧调用兼容、
  非法作用域 422）、/predict 端点 as_of 缺省 = 事件自身时间（event_id 的
  announced_at）与显式 as_of 优先，响应透传 sample_metadata

真实库用例使用独立测试库（建库 → init_schema），PG 不可达/无建库权限时
整体 skip；其余用例为纯单测（FakeConn），无真实依赖。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from AI.eventStudy.api.schemas import PredictRequest
from AI.eventStudy.db import connection as db_connection
from AI.eventStudy.review import review_dao

_CST = timezone(timedelta(hours=8))

_ROUTE_TEST_DB = "liveprofit_event_routing_test"
_AS_OF = "2026-08-31T00:00:00+08:00"


# ==================== 工具 ====================

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """只记录 SQL 与参数的假连接（路由查询 SQL 契约断言用）。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return _FakeCursor(self.rows)

    def commit(self):
        pass

    def rollback(self):
        pass


def _route_row(**over):
    """12 列路由查询结果行（列序与 SELECT 一致）。"""
    base = {
        "event_id": 1, "title": "事件", "content": "", "event_type": "宏观",
        "event_subtype": "CPI", "event_condition": "超预期",
        "announced_at": "2026-08-01T09:00:00+08:00", "trading_day": None,
        "importance": 3, "event_scope": "market", "affected_scope_refs": [],
        "source_url": None,
    }
    base.update(over)
    return (
        base["event_id"], base["title"], base["content"], base["event_type"],
        base["event_subtype"], base["event_condition"], base["announced_at"],
        base["trading_day"], base["importance"], base["event_scope"],
        base["affected_scope_refs"], base["source_url"],
    )


def _query(conn, scope, refs=(), as_of=_AS_OF, limit=20):
    return review_dao.list_approved_events_for_route(
        conn, review_dao.EventRoute(scope, tuple(refs)), as_of, limit=limit,
    )


# ==================== 归一化 ====================

def test_normalize_scope_converges_three_values():
    assert review_dao.normalize_scope(" market ") == "market"
    assert review_dao.normalize_scope("Sector") == "sector"
    assert review_dao.normalize_scope("STOCK") == "stock"
    assert review_dao.normalize_scope("GLOBAL") is None
    assert review_dao.normalize_scope("") is None
    assert review_dao.normalize_scope(None) is None


def test_normalize_scope_ref_grammar():
    # 行业：显式前缀 / 裸码 / .SI 后缀 / 大小写
    assert review_dao.normalize_scope_ref("SW:801080") == "SW:801080"
    assert review_dao.normalize_scope_ref(" sw:801080 ") == "SW:801080"
    assert review_dao.normalize_scope_ref("SW:801080.SI") == "SW:801080"
    assert review_dao.normalize_scope_ref("801080") == "SW:801080"
    assert review_dao.normalize_scope_ref("801080.SI") == "SW:801080"
    # 概念：东财 dc 代码（.TI 同花顺不在体系内）
    assert review_dao.normalize_scope_ref("CONCEPT:BK1753.DC") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("bk1753.dc") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("BK1753.DC") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("CONCEPT:BK1753.TI") is None
    # 个股：必须带交易所后缀（沪/深/北）
    assert review_dao.normalize_scope_ref("stock:600519.SH") == "stock:600519.SH"
    assert review_dao.normalize_scope_ref("600519.SH") == "stock:600519.SH"
    assert review_dao.normalize_scope_ref("000001.sz") == "stock:000001.SZ"
    assert review_dao.normalize_scope_ref("830799.BJ") == "stock:830799.BJ"
    # 非法：前缀与形态不符 / 裸股票码缺后缀（不猜交易所）/ 无法识别
    assert review_dao.normalize_scope_ref("stock:801080") is None
    assert review_dao.normalize_scope_ref("600519") is None
    assert review_dao.normalize_scope_ref("600519.XX") is None
    assert review_dao.normalize_scope_ref("XYZ") is None
    assert review_dao.normalize_scope_ref("") is None
    assert review_dao.normalize_scope_ref(None) is None


def test_normalize_scope_refs_dedupes_and_preserves_order():
    refs = review_dao.normalize_scope_refs(
        ["801080", "SW:801080", " bk1753.dc ", "BK1753.DC", "stock:600519.SH"]
    )
    assert refs == ["SW:801080", "CONCEPT:BK1753.DC", "stock:600519.SH"]
    # 字符串输入按中英文逗号/分号/换行切分；空项丢弃
    assert review_dao.normalize_scope_refs("SW:801080，\nBK1753.DC；") == [
        "SW:801080", "CONCEPT:BK1753.DC",
    ]
    assert review_dao.normalize_scope_refs(None) == []
    assert review_dao.normalize_scope_refs("  ") == []


# ==================== 校验（严格 / 非严格） ====================

def test_resolve_scope_fields_happy_paths():
    assert review_dao.resolve_scope_fields({}) == ("market", [])
    assert review_dao.resolve_scope_fields({"event_scope": "market", "affected_scope_refs": []}) == (
        "market", [],
    )
    assert review_dao.resolve_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "BK1753.DC"]}
    ) == ("sector", ["SW:801080", "CONCEPT:BK1753.DC"])
    assert review_dao.resolve_scope_fields(
        {"event_scope": "stock", "affected_scope_refs": ["600519.SH"]}
    ) == ("stock", ["stock:600519.SH"])


@pytest.mark.parametrize("review,fragment", [
    ({"event_scope": "GLOBAL"}, "作用域非法"),
    ({"event_scope": "market", "affected_scope_refs": ["SW:801080"]}, "market 作用域不允许目标引用"),
    ({"event_scope": "sector", "affected_scope_refs": []}, "至少需要一个目标引用"),
    ({"event_scope": "stock", "affected_scope_refs": []}, "至少需要一个目标引用"),
    ({"event_scope": "stock", "affected_scope_refs": ["SW:801080"]}, "stock 作用域仅支持个股引用"),
    ({"event_scope": "sector", "affected_scope_refs": ["600519.SH"]}, "sector 作用域不支持个股引用"),
    ({"event_scope": "sector", "affected_scope_refs": ["600519"]}, "目标引用格式非法"),
    ({"event_scope": "sector", "affected_scope_refs": ["XYZ"]}, "目标引用格式非法"),
    ({"event_scope": "sector", "affected_scope_refs": ["SW:8010%02d" % i for i in range(21)]}, "超出上限"),
])
def test_resolve_scope_fields_rejects(review, fragment):
    with pytest.raises(review_dao.EventScopeValidationError) as exc:
        review_dao.resolve_scope_fields(review)
    assert fragment in str(exc.value)


def test_resolve_scope_fields_existence_check():
    class Existence:
        def __init__(self, missing):
            self.missing = set(missing)

        def missing_refs(self, refs):
            return [r for r in refs if r in self.missing]

    ok = review_dao.resolve_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080"]},
        Existence([]),
    )
    assert ok == ("sector", ["SW:801080"])
    with pytest.raises(review_dao.EventScopeValidationError) as exc:
        review_dao.resolve_scope_fields(
            {"event_scope": "sector", "affected_scope_refs": ["801080", "801890"]},
            Existence(["SW:801890"]),
        )
    assert "目标引用不存在" in str(exc.value) and "SW:801890" in str(exc.value)


def test_normalize_scope_fields_is_lenient():
    """ignore 路径 / 表单回退：非法作用域与引用不拦，落 market + []。"""
    assert review_dao.normalize_scope_fields({}) == ("market", [])
    assert review_dao.normalize_scope_fields({"event_scope": "GLOBAL"}) == ("market", [])
    assert review_dao.normalize_scope_fields(
        {"event_scope": "market", "affected_scope_refs": ["SW:801080"]}
    ) == ("market", [])
    assert review_dao.normalize_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "JUNK"]}
    ) == ("sector", ["SW:801080"])


def test_check_scope_fields_returns_message_without_raising():
    assert review_dao.check_scope_fields({"event_scope": "market"}) is None
    assert "作用域非法" in review_dao.check_scope_fields({"event_scope": "GLOBAL"})

    class BrokenConn:
        def execute(self, sql, params=None):
            raise RuntimeError("码表未迁移")

    # 存在性数据源不可用 → 同样拦为行级失败（不静默放行）
    msg = review_dao.check_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080"]}, BrokenConn()
    )
    assert msg is not None and "存在性数据源不可用" in msg


def test_validation_error_is_not_draft_not_found():
    """行级失败异常类型独立：EventScopeValidationError 不是 ValueError 子类。"""
    assert not issubclass(review_dao.EventScopeValidationError, ValueError)
    assert issubclass(review_dao.EventScopeValidationError, Exception)


# ==================== 路由查询（SQL 契约，FakeConn） ====================

def test_route_query_market_sql_contract():
    conn = _FakeConn([_route_row(event_scope=None, affected_scope_refs=None)])
    rows = _query(conn, "market")
    sql, params = conn.executed[-1]
    assert "status = 'approved'" in sql
    assert "announced_at <= %s::timestamptz" in sql
    assert "event_scope = %s OR event_scope IS NULL" in sql
    assert "ORDER BY announced_at DESC" in sql
    assert params == (_AS_OF, "market", 20)
    # 历史 NULL 行读出即归一（下层消费方不需要再判空）
    assert rows[0]["event_scope"] == "market"
    assert rows[0]["affected_scope_refs"] == []


def test_route_query_sector_sql_contract_and_bare_code_normalization():
    conn = _FakeConn([_route_row(event_scope="sector", affected_scope_refs=["SW:801080"])])
    rows = _query(conn, "sector", ["801080"])
    sql, params = conn.executed[-1]
    assert "event_scope = %s" in sql
    assert "affected_scope_refs ?| %s::text[]" in sql  # 数组命中（并集）
    assert "@>" not in sql
    assert params == (_AS_OF, "sector", ["SW:801080"], 20)
    assert rows[0]["affected_scope_refs"] == ["SW:801080"]


def test_route_query_datetime_as_of_normalized():
    from datetime import datetime, timedelta, timezone

    conn = _FakeConn([])
    # 时分秒微秒全零（`datetime.combine(d, time())` 日边界语义）→ 归一为当日末（M5 口径统一）
    as_of = datetime(2026, 8, 31, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    _query(conn, "market", (), as_of=as_of)
    assert conn.executed[-1][1][0] == "2026-08-31 23:59:59.999999"

    # 带时刻 → 原样保留（不扩大到当日末；tz-aware 保留偏移）
    stamp = datetime(2026, 8, 31, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    _query(conn, "market", (), as_of=stamp)
    assert conn.executed[-1][1][0] == stamp.isoformat()

    # None / 空串 → 跳过时间条件（动态 SQL：条件整条不出现）
    for value in (None, "", "   "):
        conn = _FakeConn([])
        _query(conn, "market", (), as_of=value)
        sql, params = conn.executed[-1]
        assert "announced_at <= %s::timestamptz" not in sql
        assert params == ("market", 20)


def test_route_query_empty_targets_is_not_a_route():
    conn = _FakeConn([])
    assert _query(conn, "sector", ()) == []
    assert _query(conn, "stock", []) == []
    assert conn.executed == []  # 不落到全量事件，且不发查询


@pytest.mark.parametrize("scope,refs,limit", [
    ("GLOBAL", (), 20),
    ("sector", ["XYZ"], 20),
    ("market", (), 0),
])
def test_route_query_rejects_programmer_errors(scope, refs, limit):
    conn = _FakeConn([])
    with pytest.raises(ValueError):
        _query(conn, scope, refs, limit=limit)
    assert conn.executed == []


# ==================== 真实库（独立测试库；PG 不可达则 skip） ====================

def _with_dbname(dsn: str, dbname: str) -> str:
    """把 libpq 关键字串 / URL 串的库名替换为 dbname。"""
    if dsn.startswith(("postgres://", "postgresql://")):
        main, _, query = dsn.partition("?")
        head, _, _ = main.rpartition("/")
        out = f"{head}/{dbname}"
        return f"{out}?{query}" if query else out
    parts = [p for p in dsn.split() if not p.startswith("dbname=")]
    parts.append(f"dbname={dbname}")
    return " ".join(parts)


@pytest.fixture(scope="module")
def routing_db():
    """建独立测试库并切 AI 侧 DSN（config.PG_CONNECTION_STRING 为文档注入点）。"""
    import psycopg

    from AI.eventStudy.collectors import config as es_config

    base = es_config.pg_dsn()
    try:
        admin = psycopg.connect(base, connect_timeout=5, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可达，跳过路由入库测试: {e}")
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{_ROUTE_TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{_ROUTE_TEST_DB}"')
    except Exception as e:  # noqa: BLE001
        admin.close()
        pytest.skip(f"无法创建测试库（权限不足），跳过路由入库测试: {e}")

    original = es_config.PG_CONNECTION_STRING
    es_config.PG_CONNECTION_STRING = _with_dbname(base, _ROUTE_TEST_DB)
    conn = db_connection.get_connection()
    assert db_connection.init_schema(conn), "测试库建表失败"
    # market schema 建表（先注入 db.instrument.db 模块常量再 init_schema——
    # 次序定稿 R1 minor 12；行业/板块字典行由 init_schema 种子落库）
    import db.instrument.db as market_db
    _prev_market_dsn = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = _with_dbname(base, _ROUTE_TEST_DB)
    try:
        from db.instrument.db import init_schema as market_init_schema
        assert market_init_schema(conn), "测试库 market schema 建表失败"
    except Exception:
        market_db.PG_CONNECTION_STRING = _prev_market_dsn
        raise
    # 存在性校验底座种子（真实表列集：stock_info 无 name 列；幂等 ON CONFLICT
    # DO NOTHING——industry/concept 行已由 init_schema 种子落库，R2 polish 3-7）
    conn.execute(
        "INSERT INTO market.stock_info (ts_code) VALUES"
        " ('600519.SH'), ('000001.SZ') ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO market.sector (source, sector_code, name) VALUES"
        " ('dc', 'BK1753.DC', '光刻胶'),"
        " ('ths', 'BK9001.DC', '同花顺来源占位') "
        "ON CONFLICT (source, sector_code) DO NOTHING"
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        es_config.PG_CONNECTION_STRING = original
        market_db.PG_CONNECTION_STRING = _prev_market_dsn
        try:
            admin.execute(f'DROP DATABASE IF EXISTS "{_ROUTE_TEST_DB}" WITH (FORCE)')
        finally:
            admin.close()


@pytest.fixture()
def conn(routing_db):
    """逐用例清空事件表（保留资产/行业码表种子）。"""
    routing_db.execute("TRUNCATE events, event_impacts, predictions RESTART IDENTITY")
    routing_db.commit()
    yield routing_db


def _insert_event(conn, title, announced_at, scope=None, refs=None, status="approved"):
    conn.execute(
        "INSERT INTO events (title, content, announced_at, importance, status, "
        "                    event_scope, affected_scope_refs) "
        "VALUES (%s, '', %s::timestamptz, 3, %s, %s, %s::jsonb)",
        (title, announced_at, status, scope,
         None if refs is None else json.dumps(refs, ensure_ascii=False)),
    )


def _ids(conn, scope, refs=(), as_of=_AS_OF, limit=20):
    return [r["event_id"] for r in _query(conn, scope, refs, as_of=as_of, limit=limit)]


def test_init_schema_idempotent_and_route_columns(routing_db):
    """迁移可重复执行；路由列、B-tree 与 GIN 索引、行业码表就位。"""
    assert db_connection.init_schema(routing_db) is True
    assert db_connection.init_schema(routing_db) is True

    cols = {
        r[0] for r in routing_db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'events'"
        ).fetchall()
    }
    assert {"event_scope", "affected_scope_refs"} <= cols

    indexdefs = {
        r[0]: (r[1] or "").lower() for r in routing_db.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'events'"
        ).fetchall()
    }
    assert "using gin" in indexdefs["idx_events_scope_refs"]
    assert "using btree" in indexdefs["idx_events_scope_announced"]
    assert "event_scope, announced_at" in indexdefs["idx_events_scope_announced"]

    codes = [
        r[0] for r in routing_db.execute(
            "SELECT industry_code FROM market.industry WHERE source = 'SW2021' "
            "ORDER BY industry_code"
        ).fetchall()
    ]
    assert len(codes) == 31 and codes[0] == "801010" and codes[-1] == "801980"


def test_migration_normalizes_legacy_rows(routing_db):
    """历史行（NULL）归一 market + []，重复迁移结果不变（确定性）。"""
    routing_db.execute("TRUNCATE events RESTART IDENTITY")
    routing_db.execute(
        "INSERT INTO events (title, announced_at, status, event_scope, affected_scope_refs) "
        "VALUES ('迁移前旧事件', '2026-08-01T09:00:00+08:00'::timestamptz, 'approved', NULL, NULL)"
    )
    routing_db.commit()

    assert db_connection.init_schema(routing_db) is True
    first = routing_db.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE title = '迁移前旧事件'"
    ).fetchone()
    assert first == ("market", [])

    assert db_connection.init_schema(routing_db) is True
    second = routing_db.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE title = '迁移前旧事件'"
    ).fetchone()
    assert second == ("market", [])

    # §归一后的旧行在市场层可见（不落空：旧事件仍参与路由）
    assert _ids(routing_db, "market") == [1]


def test_route_query_scope_isolation(conn):
    """同名事件按作用域/目标精确隔离（不误命中、不串层）。"""
    _insert_event(conn, "同名政策", "2026-08-01T09:00:00+08:00", "market", [])
    _insert_event(conn, "同名政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "同名政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    _insert_event(conn, "同名政策", "2026-08-04T09:00:00+08:00", "stock", ["stock:600519.SH"])
    conn.commit()

    assert _ids(conn, "market") == [1]
    assert _ids(conn, "sector", ["SW:801080"]) == [2]
    assert _ids(conn, "sector", ["SW:801750"]) == [3]
    assert _ids(conn, "stock", ["stock:600519.SH"]) == [4]
    # 跨层不串：sector 事件不因「同为 sector 层或代码形态相似」被 stock 路由命中
    assert _ids(conn, "stock", ["stock:801080.SH"]) == []


def test_route_query_bare_codes_and_isolation_by_code(conn):
    """裸代码可查询；不同代码互不命中（SW:801080 与 SW:801750 严格区分）。"""
    _insert_event(conn, "电子行业政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "计算机行业政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    conn.commit()

    assert _ids(conn, "sector", ["801080"]) == [1]
    assert _ids(conn, "sector", ["801750"]) == [2]
    assert _ids(conn, "sector", ["SW:801080"]) == [1]
    assert _ids(conn, "sector", ["SW:801760"]) == []  # 相邻代码不命中


def test_route_query_multi_target_is_union(conn):
    """route 多目标为并集：命中任一目标引用即属该路由（单目标等价包含查询）。"""
    _insert_event(conn, "电子政策", "2026-08-01T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "电子+概念", "2026-08-02T09:00:00+08:00", "sector",
                  ["SW:801080", "CONCEPT:BK1753.DC"])
    _insert_event(conn, "计算机政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    conn.commit()

    assert _ids(conn, "sector", ["SW:801080"]) == [2, 1]  # 降序：近事优先
    assert _ids(conn, "sector", ["CONCEPT:BK1753.DC"]) == [2]
    assert _ids(conn, "sector", ["SW:801080", "SW:801750"]) == [3, 2, 1]


def test_route_query_excludes_ignored_and_future(conn):
    """status/时点过滤：ignored 与 announced_at > as_of 的事件排除（防前视）。"""
    as_of = "2026-08-31T10:00:00+08:00"
    _insert_event(conn, "边界前", "2026-08-31T09:59:59+08:00", "market", [])
    _insert_event(conn, "恰在时点", as_of, "market", [])
    _insert_event(conn, "时点之后", "2026-08-31T10:00:01+08:00", "market", [])
    _insert_event(conn, "被忽略", "2026-08-30T09:00:00+08:00", "market", [], status="ignored")
    _insert_event(conn, "未来行业事件", "2026-09-02T09:00:00+08:00", "sector", ["SW:801080"])
    conn.commit()

    assert [r["title"] for r in _query(conn, "market", as_of=as_of)] == ["恰在时点", "边界前"]
    assert _ids(conn, "sector", ["SW:801080"], as_of=as_of) == []


def test_route_query_limit_and_order(conn):
    for i in range(3):
        _insert_event(conn, f"市场事件{i}", f"2026-08-0{i + 1}T09:00:00+08:00", "market", [])
    conn.commit()

    assert [r["title"] for r in _query(conn, "market", limit=2)] == ["市场事件2", "市场事件1"]
    assert len(_query(conn, "market", limit=3)) == 3


def test_route_query_returns_normalized_fields(conn):
    _insert_event(conn, "电子政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    # 历史 NULL 行（迁移前形态）在读取侧同样归一出 event_scope/refs
    _insert_event(conn, "历史行", "2026-08-03T09:00:00+08:00", None, None)
    conn.commit()
    row = _query(conn, "sector", ["SW:801080"])[0]
    assert row["event_scope"] == "sector"
    assert row["affected_scope_refs"] == ["SW:801080"]
    assert row["announced_at"].startswith("2026-08-02T09:00:00")
    assert set(row) >= {
        "event_id", "title", "content", "event_type", "event_subtype",
        "event_condition", "announced_at", "trading_day", "importance",
        "event_scope", "affected_scope_refs", "source_url",
    }


def test_insert_event_persists_normalized_route_fields(conn):
    """写入即归一（大小写/裸码/前缀统一），随后可被同路由查询命中。"""
    draft = {"title": "电子行业政策", "content": "", "announced_at": "2026-08-02T09:00:00+08:00",
             "source_url": None, "importance_hint": None}
    review = {"event_scope": "sector", "affected_scope_refs": ["801080", "BK1753.DC"],
              "importance": 4}
    event_id = review_dao._insert_event(conn, draft, review, "approved")

    stored = conn.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE event_id = %s", (event_id,)
    ).fetchone()
    assert stored == ("sector", ["SW:801080", "CONCEPT:BK1753.DC"])
    assert _ids(conn, "sector", ["SW:801080"]) == [event_id]
    assert _ids(conn, "sector", ["CONCEPT:BK1753.DC"]) == [event_id]


def test_check_scope_fields_against_real_store_tables(conn):
    """存在性校验对真实 store 表：行业码表 / stock_basic / concept(source=dc)。"""
    ok = {
        "sector": [["801080"], ["CONCEPT:BK1753.DC"], ["SW:801080", "CONCEPT:BK1753.DC"]],
        "stock": [["600519.SH"], ["000001.SZ"]],
        "market": [[]],
    }
    for scope, ref_lists in ok.items():
        for refs in ref_lists:
            assert review_dao.check_scope_fields(
                {"event_scope": scope, "affected_scope_refs": refs}, conn
            ) is None, (scope, refs)

    missing = {
        "sector": [["SW:999999"], ["CONCEPT:BK9999.DC"], ["SW:801080", "SW:999999"]],
        "stock": [["600519.SZ"], ["300750.SZ"]],
    }
    for scope, ref_lists in missing.items():
        for refs in ref_lists:
            msg = review_dao.check_scope_fields(
                {"event_scope": scope, "affected_scope_refs": refs}, conn
            )
            assert msg is not None and "目标引用不存在" in msg, (scope, refs)

    # 同花顺来源的概念代码不算命中（存在性锁定 source='dc'）
    msg = review_dao.check_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["CONCEPT:BK9001.DC"]}, conn
    )
    assert msg is not None and "目标引用不存在" in msg


def test_approve_event_validates_scope_and_persists(conn, monkeypatch):
    """approve_event：幻化引用拦为行级失败（不落库），合法引用落库后可被路由命中。"""
    monkeypatch.setattr(review_dao, "_delete_draft", lambda draft_id: None)
    monkeypatch.setattr(review_dao, "_log_review", lambda *a, **kw: None)
    draft = {"draft_id": 7, "title": "电子行业政策", "content": "...",
             "announced_at": "2026-08-02T09:00:00+08:00", "source_url": None,
             "importance_hint": None}
    monkeypatch.setattr(review_dao, "get_pending_event", lambda draft_id: dict(draft))

    with pytest.raises(review_dao.EventScopeValidationError):
        review_dao.approve_event(
            conn, 7, {"event_scope": "sector", "affected_scope_refs": ["SW:999999"]}
        )
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0

    event_id = review_dao.approve_event(
        conn, 7, {"event_scope": "sector", "affected_scope_refs": ["801080"], "importance": 4}
    )
    assert _ids(conn, "sector", ["SW:801080"]) == [event_id]


# ==================== API 层（T3：as_of / event_scope） ====================

def _api_main():
    """延迟导入 API 模块：FastAPI/APScheduler 依赖缺失只影响本组用例。

    进程内直调端点函数（不经 TestClient / 不触发 lifespan 调度器）。
    """
    from AI.eventStudy.api import main
    return main


class _ApiConn:
    """as_of 缺省解析用假连接：按事件 ID 返回 announced_at，记录执行。"""

    def __init__(self, announced_at=None):
        self.announced_at = announced_at
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM events WHERE event_id" in sql:
            rows = [(self.announced_at,)] if self.announced_at is not None else []
            return _FakeCursor(rows)
        return _FakeCursor([])

    def close(self):
        pass


def _api_predict(monkeypatch, announced_at=None, **over):
    """调用 /predict 端点；返回 (conn, predict_impact 实参, 端点返回值)。"""
    api_main = _api_main()
    conn = _ApiConn(announced_at)
    captured = {}
    monkeypatch.setattr(api_main, "_open_conn", lambda: conn)
    monkeypatch.setattr(
        api_main.predictor, "predict_impact",
        lambda *a, **kw: (captured.update(kw), {
            "prediction": {"predicted_direction": 0, "predicted_return": None},
            "sample_metadata": {"history_match_status": "ok", "sample_count": 0},
        })[1],
    )
    payload = {"event_text": "事件文本", "asset_ticker": "000300.SH"}
    payload.update(over)
    result = api_main.predict(PredictRequest(**payload))
    return conn, captured, result


def test_predict_request_defaults_keep_legacy_calls():
    """as_of / event_scope 均可缺省，旧调用方字段集不变（兼容）。"""
    req = PredictRequest(event_text="事件", asset_ticker="000300.SH")
    assert req.as_of is None and req.event_scope is None
    assert req.window_type == "post_event_5d"
    assert req.save is False and req.event_id is None
    legacy = PredictRequest(**{
        "event_text": "事件", "asset_ticker": "000300.SH", "event_type": "宏观",
        "event_subtype": "CPI", "event_condition": "超预期", "save": True, "event_id": 3,
    })
    assert legacy.event_scope is None and legacy.as_of is None


@pytest.mark.parametrize("scope", ["market", "sector", "stock"])
def test_predict_request_accepts_three_scopes(scope):
    assert PredictRequest(event_text="e", asset_ticker="000300.SH",
                          event_scope=scope).event_scope == scope


def test_predict_request_rejects_invalid_scope():
    with pytest.raises(ValidationError):
        PredictRequest(event_text="e", asset_ticker="000300.SH", event_scope="GLOBAL")


def test_predict_endpoint_defaults_as_of_to_event_time(monkeypatch):
    """as_of 缺省 = 事件自身时间：按 event_id 取 announced_at 后传给预测器。"""
    announced = datetime(2026, 8, 31, 10, 0, tzinfo=_CST)
    conn, captured, result = _api_predict(
        monkeypatch, announced_at=announced, event_id=7, event_scope="market",
    )
    assert captured["as_of"] == announced.isoformat()
    assert captured["event_scope"] == "market"
    assert captured["asset_ticker"] == "000300.SH"
    assert "sample_metadata" in result  # 样本元数据随响应透传（供预取器降级）
    assert [p for _, p in conn.executed] == [(7,)]


def test_predict_endpoint_explicit_as_of_wins_over_event_time(monkeypatch):
    conn, captured, _ = _api_predict(
        monkeypatch, announced_at=datetime(2026, 8, 31, 10, 0, tzinfo=_CST),
        event_id=7, as_of="2026-01-01T00:00:00+08:00",
    )
    assert captured["as_of"] == "2026-01-01T00:00:00+08:00"
    assert conn.executed == [], "显式 as_of 时不应查询事件时间"


def test_predict_endpoint_without_as_of_or_event_id_keeps_no_filter(monkeypatch):
    """两者均缺省 → as_of=None（不限定时间）与 event_scope=None（不限定作用域）。"""
    conn, captured, _ = _api_predict(monkeypatch)
    assert captured["as_of"] is None and captured["event_scope"] is None
    assert conn.executed == []


def test_predict_endpoint_unknown_event_id_fails_loudly(monkeypatch):
    """event_id 无对应事件 → 404（不静默丢弃时间过滤，避免混入未来样本）。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _api_predict(monkeypatch, announced_at=None, event_id=999)
    assert exc.value.status_code == 404
    assert "as_of" in exc.value.detail


def test_predict_endpoint_invalid_window_type_422(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _api_predict(monkeypatch, window_type="post_event_3d")
    assert exc.value.status_code == 422
