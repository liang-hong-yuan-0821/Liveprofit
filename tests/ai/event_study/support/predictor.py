"""
预测模块测试（方案 3.7：模板匹配 + 向量检索加权融合；T3：路由/as_of/元数据）

验证合并加权规则：
- 模板内事件权重 1.0
- 向量补充事件权重 = 相似度 × 0.5（相似度 < 0.5 不纳入）
- 方向按加权平均 CAR 符号判定
- 事件库无样本时返回中性 + 明确提示

T3 增量（方案第三章）：
- 防前视：announced_at > as_of 排除、边界 == as_of 包含（模板 + 向量双通道）
- 同作用域过滤：sector/stock 仅命中目标引用（并集）、同名不误命中、
  空引用 = 空路由（零样本，不落到全量事件）、market 含历史 NULL 行
- 样本元数据：no_sample / coverage_missing / api_unavailable 可区分；
  contaminated 污染提示；缺省参数（不传 as_of/event_scope）保持旧行为
- 非法路由（作用域 / 引用格式）抛 ValueError（不静默降级为空样本）

真实库用例使用独立测试库（建库 → init_schema），PG 不可达/无建库权限时
整体 skip；其余用例为纯单测（FakeConn），无真实依赖。
"""

import json

import uuid

from datetime import datetime, timezone

from decimal import Decimal

from types import SimpleNamespace

import pytest

from AI.eventStudy.prediction import predictor, similarity_search

from AI.eventStudy.review import news_dao

_PRED_TEST_DB = "liveprofit_predictor_test"

_AS_OF = "2026-08-31T10:00:00+08:00"

_EMB = [0.1] * 1024

_VEC_LITERAL = "[" + ",".join(["0.1"] * 1024) + "]"

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

class FakeConn:
    """按 SQL 子串分派返回结果的最小连接桩（executed 记录 SQL 契约）。"""

    def __init__(self, matcher):
        self._matcher = matcher
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

def _conn_with(template_cars, supplement_impacts, asset_rows=None, registered=True):
    """模板（car）/ 补充影响（car, direction, contaminated）/ 覆盖探测分派桩。"""
    def matcher(sql, params=None):
        if "SELECT ei.cumulative_abnormal_return, ei.direction" in sql:
            event_id = params[0]
            return supplement_impacts.get(event_id, [])
        if "FROM event_impacts" in sql:  # 模板匹配查询
            return [(c, False) for c in template_cars]
        if "SELECT 1 FROM assets WHERE ticker" in sql:  # 资产覆盖探测
            return [(1,)] if registered else []
        if "SELECT asset_id FROM assets" in sql:
            return asset_rows or [(1,)]
        return None

    return FakeConn(matcher)

def _tpl_calls(conn):
    return [c for c in conn.executed if "FROM event_impacts" in c[0]
            and "ei.direction" not in c[0]]

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
def predict_db():
    """建独立测试库并切 AI 侧 DSN（config.PG_CONNECTION_STRING 为文档注入点）。"""
    import psycopg

    from AI.eventStudy.collectors import config as es_config
    from AI.eventStudy.db import connection as db_connection

    base = es_config.pg_dsn()
    try:
        admin = psycopg.connect(base, connect_timeout=5, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可达，跳过预测器入库测试: {e}")
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{_PRED_TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{_PRED_TEST_DB}"')
    except Exception as e:  # noqa: BLE001
        admin.close()
        pytest.skip(f"无法创建测试库（权限不足），跳过预测器入库测试: {e}")

    original = es_config.PG_CONNECTION_STRING
    es_config.PG_CONNECTION_STRING = _with_dbname(base, _PRED_TEST_DB)
    conn = db_connection.get_connection()
    assert db_connection.init_schema(conn), "测试库建表失败"
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        es_config.PG_CONNECTION_STRING = original
        try:
            admin.execute(f'DROP DATABASE IF EXISTS "{_PRED_TEST_DB}" WITH (FORCE)')
        finally:
            admin.close()

@pytest.fixture()
def db(predict_db):
    """逐用例清空事件及其新审计事实（保留 assets 与行业码表种子）。"""
    predict_db.execute(
        "TRUNCATE event_assessment, news, events, event_impacts, predictions RESTART IDENTITY"
    )
    predict_db.commit()
    yield predict_db

def _insert_event(conn, title, announced_at, scope="market", refs=None,
                  status="approved", event_type="宏观", event_subtype="CPI",
                  event_condition="超预期", embedding=None):
    row = conn.execute(
        "INSERT INTO events (title, content, announced_at, importance, status, canonical_key, "
        "  event_type, event_subtype, event_condition, event_scope, "
        "  affected_scope_refs, embedding) "
        "VALUES (%s, '', %s::timestamptz, 3, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector) "
        "RETURNING event_id",
        (title, announced_at, status, f"predictor-test:{uuid.uuid4()}",
         event_type, event_subtype, event_condition,
         scope, None if refs is None else json.dumps(refs, ensure_ascii=False), embedding),
    ).fetchone()
    return int(row[0])

def _insert_impact(conn, event_id, car, ticker="000300.SH",
                   window_type="post_event_5d", contaminated=False):
    asset_id = conn.execute(
        "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
    ).fetchone()[0]
    direction = 1 if car > 0 else (-1 if car < 0 else 0)
    conn.execute(
        "INSERT INTO event_impacts (event_id, asset_id, window_type, window_days, "
        "  cumulative_abnormal_return, direction, is_contaminated) "
        "VALUES (%s, %s, %s, 5, %s, %s, %s)",
        (event_id, asset_id, window_type, car, direction, contaminated),
    )

def _predict(conn, monkeypatch, as_of=None, event_scope=None, scope_refs=(),
             ticker="000300.SH", labels=("宏观", "CPI", "超预期")):
    """模板通道确定性断言。

    向量通道保持可用（encode_text 返回有效向量）：测试库中事件 embedding 均为
    NULL → 检索零候选且无错误（不污染覆盖状态）；模板样本数断言不受影响。
    """
    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)
    return predictor.predict_impact(
        conn, "查询事件", ticker, event_type=labels[0], event_subtype=labels[1],
        event_condition=labels[2], as_of=as_of, event_scope=event_scope,
        scope_refs=scope_refs,
    )
