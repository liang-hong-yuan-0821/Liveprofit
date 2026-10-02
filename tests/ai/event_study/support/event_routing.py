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
