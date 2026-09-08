"""事件研究审核端点契约测试（方案：事件研究审核界面平台集成方案 3.3.1）。

环境隔离：
- AI 侧 review_dao 经模块级 config 常量（PG_CONNECTION_STRING / REDIS_CONNECTION_STRING）连库，
  本模块 autouse fixture 将其 monkeypatch 到契约测试库（liveprofit_contract_test）+ Redis db 11，
  并重置懒加载客户端单例。Redis 由全局 _clean_platform_state 逐用例 flushdb；
  审核三表中 events/event_impacts 由本 fixture 逐用例 TRUNCATE（全局只清平台十张表），
  assets 用幂等种子（ON CONFLICT DO NOTHING）常驻 4 个指数 ticker。
- 触发真实 OLS/Provider 的 compute_all_windows 一律 monkeypatch（契约测试禁止真实计算）。
"""

from __future__ import annotations

import json

import pytest

from backend.bootstrap.settings import CoreSettings

# ---- 与 AI/eventStudy/db/schema.sql 同步的最小列集 ----
# 审核流 INSERT 的 12 列 + 关联读取列；trading_day/surprise/embedding 由 compute/vectorizer
# 写入，契约测试 monkeypatch 掉 compute 故省略。列集漂移会直接让本文件失败（可感知）。
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT,
    event_type      VARCHAR(64),
    event_subtype   VARCHAR(64),
    event_condition VARCHAR(32),
    announced_at    TIMESTAMPTZ NOT NULL,
    expected_value  NUMERIC,
    actual_value    NUMERIC,
    previous_value  NUMERIC,
    importance      SMALLINT NOT NULL DEFAULT 3,
    status          VARCHAR(16) NOT NULL,
    source_url      TEXT
)
"""

_ASSETS_DDL = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id    BIGSERIAL PRIMARY KEY,
    ticker      VARCHAR(32)  NOT NULL,
    name        VARCHAR(64)  NOT NULL,
    asset_class VARCHAR(32)  NOT NULL DEFAULT '指数',
    market      VARCHAR(8)   NOT NULL DEFAULT 'CN',
    UNIQUE (ticker)
)
"""

_ASSETS_SEED = """
INSERT INTO assets (ticker, name, asset_class, market) VALUES
    ('000001.SH', '上证指数', '指数', 'CN'),
    ('000688.SH', '科创50',   '指数', 'CN'),
    ('000698.SH', '科创100',  '指数', 'CN'),
    ('000300.SH', '沪深300',  '指数', 'CN')
ON CONFLICT (ticker) DO NOTHING
"""

# confirm_impacts 的 ON CONFLICT 子句依赖该唯一约束（与 schema.sql 一致）
_IMPACTS_DDL = """
CREATE TABLE IF NOT EXISTS event_impacts (
    impact_id                   BIGSERIAL PRIMARY KEY,
    event_id                    BIGINT NOT NULL,
    asset_id                    BIGINT NOT NULL,
    window_type                 VARCHAR(32) NOT NULL,
    window_days                 INTEGER NOT NULL,
    cumulative_abnormal_return  NUMERIC NOT NULL,
    t_stat                      NUMERIC,
    direction                   SMALLINT NOT NULL DEFAULT 0,
    is_contaminated             BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, asset_id, window_type)
)
"""


def _review_pg_url() -> str:
    from backend.tests.contract.api.conftest import _test_db_url

    return _test_db_url(CoreSettings().resolved_database_url())


def _psycopg_dsn() -> str:
    """AI 侧 psycopg 连接串：去掉 SQLAlchemy 方言后缀。"""
    return _review_pg_url().replace("postgresql+psycopg://", "postgresql://")


def _redis_test_url() -> str:
    from backend.tests.contract.api.conftest import _redis_test_url

    return _redis_test_url()


@pytest.fixture(autouse=True)
def _review_test_env(client, monkeypatch):
    from sqlalchemy import create_engine, text

    import AI.eventStudy.collectors.config as es_config

    monkeypatch.setattr(es_config, "PG_CONNECTION_STRING", _psycopg_dsn())
    monkeypatch.setattr(es_config, "REDIS_CONNECTION_STRING", _redis_test_url())
    monkeypatch.setattr(es_config, "_redis_client", None)  # 懒加载客户端重建，指向 db 11

    engine = create_engine(_review_pg_url())
    with engine.begin() as conn:
        for ddl in (_EVENTS_DDL, _ASSETS_DDL, _IMPACTS_DDL):
            conn.execute(text(ddl))
        conn.execute(text(_ASSETS_SEED))
        conn.execute(text("TRUNCATE events, event_impacts"))
    engine.dispose()

    # 默认替身：批量 approve 触发的影响计算不跑真实 OLS；用例内可再次 monkeypatch 覆盖
    monkeypatch.setattr(
        "AI.eventStudy.processing.event_study.compute_all_windows",
        lambda conn, event_id: {"event_id": event_id, "assets": {}},
    )
    yield


def _seed_pending(client, draft_id: int, draft: dict) -> None:
    payload = json.dumps(draft, ensure_ascii=False)
    client.redis.set(f"events:pending:{draft_id}", payload)


def _pending_draft(**overrides) -> dict:
    d = {
        "title": "中国1月CPI同比上涨2.1%，预期1.9%",
        "content": "国家统计局公布...",
        "source_url": "https://example.com/1",
        "announced_at": "2026-08-01T09:00:00+08:00",
        "importance_hint": 4,
        "source": "金十数据",
        "ai_suggestions": {
            "event_type": "宏观数据",
            "event_subtype": "CPI",
            "event_condition": "超预期",
            "importance": 5,
            "expected_value": 1.9,
            "actual_value": 2.1,
            "previous_value": 1.5,
        },
    }
    d.update(overrides)
    return d


def _impact_draft(**overrides) -> dict:
    d = {
        "event_id": 101,
        "t0": "2026-08-03",
        "computed_at": "2026-08-04T08:31:02.123456",
        "assets": {
            "000001.SH": {
                "pre_event_5d": {
                    "window_days": 5, "cumulative_abnormal_return": 0.012345,
                    "t_stat": 2.15, "direction": 1, "is_contaminated": False,
                },
                "event_day": {
                    "window_days": 1, "cumulative_abnormal_return": 0.0,
                    "t_stat": 0.1, "direction": 0, "is_contaminated": False,
                },
                "post_event_5d": {
                    "window_days": 5, "cumulative_abnormal_return": -0.02,
                    "t_stat": -2.5, "direction": -1, "is_contaminated": True,
                    "error": "not enough data",
                },
            },
            "000300.SH": {
                "event_day": {
                    "window_days": 1, "cumulative_abnormal_return": 0.008,
                    "t_stat": 1.8, "direction": 1, "is_contaminated": False,
                },
            },
        },
    }
    d.update(overrides)
    return d


def _seed_impact(client, draft: dict) -> None:
    client.redis.set(f"event_impacts:draft:{draft['event_id']}", json.dumps(draft, ensure_ascii=False))


def _seed_event_row(pg_url: str, **row) -> None:
    from sqlalchemy import create_engine, text

    columns = ", ".join(row.keys())
    values = ", ".join(f":{k}" for k in row)
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO events ({columns}) VALUES ({values})"), row)
    engine.dispose()


def _events_rows(pg_url: str, **where) -> list[dict]:
    from sqlalchemy import create_engine, text

    conditions = " AND ".join(f"{k} = :{k}" for k in where) or "TRUE"
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM events WHERE {conditions}"), where)
        rows = [dict(r._mapping) for r in result]
    engine.dispose()
    return rows


def _review_log(client) -> list[dict]:
    return [json.loads(s) for s in client.redis.lrange("event_review_log", 0, -1)]


def _batch_row(draft_id: int, action: str, **fields) -> dict:
    row = {"draft_id": draft_id, "action": action}
    row.update(fields)
    return row


# ==================== 1. 待审列表 ====================

def test_pending_events_empty(client):
    resp = client.http.get("/api/v1/event-studies/review/pending-events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["schema_version"] == "v1"
    assert body["data"]["items"] == []


def test_pending_events_sorted_desc_with_suggestions(client):
    _seed_pending(client, 1, _pending_draft(announced_at="2026-08-01T09:00:00+08:00"))
    _seed_pending(client, 2, _pending_draft(title="LPR 公布", announced_at="2026-08-02T09:30:00+08:00"))
    resp = client.http.get("/api/v1/event-studies/review/pending-events")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert [i["draft_id"] for i in items] == [2, 1]  # announced_at 降序
    assert items[0]["title"] == "LPR 公布"
    assert items[1]["ai_suggestions"]["event_type"] == "宏观数据"
    assert items[1]["importance_hint"] == 4


# ==================== 2. AI 预填 ====================

def test_prelabel_llm_unavailable_idempotent(client, monkeypatch):
    monkeypatch.setattr("AI.eventStudy.review.ai_prelabel.get_llm", lambda: None)
    # 无 ai_suggestions 的草稿才进入预填切片
    _seed_pending(client, 1, _pending_draft(ai_suggestions=None))
    _seed_pending(client, 2, _pending_draft(title="LPR 公布", ai_suggestions=None))
    resp = client.http.post("/api/v1/event-studies/review/prelabel", json={"limit": 50})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"prelabeled": 0, "remaining": 2}
    # 同请求重发结果一致（幂等，不阻塞）
    resp2 = client.http.post("/api/v1/event-studies/review/prelabel", json={"limit": 50})
    assert resp2.json()["data"] == {"prelabeled": 0, "remaining": 2}


# ==================== 3-6. 批量提交 ====================

def test_batch_approve_persists_and_logs(client):
    _seed_pending(client, 1, _pending_draft())
    resp = client.http.post(
        "/api/v1/event-studies/review/batch",
        json={"items": [_batch_row(1, "approve", event_type="宏观数据", importance=5,
                                   expected_value=0, operator="tester")]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    row = data["results"][0]
    assert row["ok"] is True and row["compute_status"] == "ok"
    assert row["event_id"] > 0
    assert data["summary"] == {"approved": 1, "ignored": 0, "computed": 1}
    # PG：approved + 字段落库（expected_value=0 是合法值，不被吞）
    rows = _events_rows(_review_pg_url(), status="approved")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "宏观数据" and rows[0]["importance"] == 5
    assert float(rows[0]["expected_value"]) == 0.0
    # Redis：草稿删除 + 审核日志
    assert client.redis.exists("events:pending:1") == 0
    log = _review_log(client)
    assert len(log) == 1 and log[0]["action"] == "approve" and log[0]["operator"] == "tester"
    assert log[0]["changed_fields"]["expected_value"] == 0


def test_batch_ignore_persists_null_fields(client):
    _seed_pending(client, 1, _pending_draft())
    resp = client.http.post(
        "/api/v1/event-studies/review/batch",
        json={"items": [_batch_row(1, "ignore")]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["results"][0]["ok"] is True and data["results"][0]["compute_status"] == "skipped"
    assert data["summary"] == {"approved": 0, "ignored": 1, "computed": 0}
    rows = _events_rows(_review_pg_url(), status="ignored")
    assert len(rows) == 1
    assert rows[0]["event_type"] is None and rows[0]["importance"] == 4  # importance_hint 默认链
    assert client.redis.exists("events:pending:1") == 0
    assert _review_log(client)[0]["action"] == "ignore"


def test_batch_per_row_draft_not_found_does_not_fail_batch(client):
    _seed_pending(client, 1, _pending_draft())
    resp = client.http.post(
        "/api/v1/event-studies/review/batch",
        json={"items": [_batch_row(1, "approve"), _batch_row(9999, "approve")]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    ok_row, failed_row = data["results"]
    assert ok_row["ok"] is True
    assert failed_row["ok"] is False and failed_row["error_code"] == "REVIEW_DRAFT_NOT_FOUND"
    assert "不存在" in failed_row["error_message"]
    assert data["summary"] == {"approved": 1, "ignored": 0, "computed": 1}


def test_batch_row_db_error_isolated_with_rollback(client):
    _seed_pending(client, 1, _pending_draft(announced_at="not-a-date"))  # 脏草稿：timestamptz DataError
    _seed_pending(client, 2, _pending_draft(title="正常草稿"))
    resp = client.http.post(
        "/api/v1/event-studies/review/batch",
        json={"items": [_batch_row(1, "approve"), _batch_row(2, "approve")]},
    )
    assert resp.status_code == 200
    dirty, normal = resp.json()["data"]["results"]
    assert dirty["ok"] is False and dirty["error_code"] == "REVIEW_ROW_FAILED"
    assert normal["ok"] is True  # 失败路径 rollback 后事务未毒化后续行
    assert len(_events_rows(_review_pg_url(), status="approved")) == 1


def test_batch_compute_failure_keeps_row_ok(client, monkeypatch):
    _seed_pending(client, 1, _pending_draft())

    def _boom(conn, event_id):
        raise RuntimeError("OLS failed")

    monkeypatch.setattr("AI.eventStudy.processing.event_study.compute_all_windows", _boom)
    resp = client.http.post(
        "/api/v1/event-studies/review/batch",
        json={"items": [_batch_row(1, "approve")]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    row = data["results"][0]
    assert row["ok"] is True and row["compute_status"] == "failed"
    assert data["summary"] == {"approved": 1, "ignored": 0, "computed": 0}
    # 行仍落库，daily_job 兜底重算
    assert len(_events_rows(_review_pg_url(), status="approved")) == 1


# ==================== 7. 补算端点 ====================

def test_compute_endpoint_event_missing_404(client, monkeypatch):
    def _missing(conn, event_id):
        raise ValueError(f"事件 {event_id} 不存在")

    monkeypatch.setattr("AI.eventStudy.processing.event_study.compute_all_windows", _missing)
    resp = client.http.post("/api/v1/event-studies/review/events/99/compute", json={})
    assert resp.status_code == 404
    problem = resp.json()
    assert problem["code"] == "REVIEW_EVENT_NOT_FOUND" and problem["retryable"] is False


def test_compute_endpoint_failure_500_retryable(client, monkeypatch):
    def _boom(conn, event_id):
        raise RuntimeError("provider down")

    monkeypatch.setattr("AI.eventStudy.processing.event_study.compute_all_windows", _boom)
    resp = client.http.post("/api/v1/event-studies/review/events/7/compute", json={})
    assert resp.status_code == 500
    problem = resp.json()
    assert problem["code"] == "REVIEW_COMPUTE_FAILED" and problem["retryable"] is True


def test_compute_endpoint_ok(client):
    resp = client.http.post("/api/v1/event-studies/review/events/7/compute", json={})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"event_id": 7, "status": "ok", "message": None}


def test_compute_endpoint_all_windows_failed_200(client, monkeypatch):
    def _all_err(conn, event_id):
        return {
            "event_id": event_id,
            "assets": {"000001.SH": {"pre_event_5d": {"error": "no data"}, "event_day": {"error": "no data"}}},
        }

    monkeypatch.setattr("AI.eventStudy.processing.event_study.compute_all_windows", _all_err)
    resp = client.http.post("/api/v1/event-studies/review/events/7/compute", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "failed"
    assert "兜底" in resp.json()["data"]["message"]


# ==================== 8-9. 影响草稿与确认 ====================

def test_impact_drafts_with_title_join(client):
    _seed_event_row(_review_pg_url(), event_id=101, title="CPI 公布",
                    announced_at="2026-08-01T09:00:00+08:00", status="approved")
    _seed_impact(client, _impact_draft())
    resp = client.http.get("/api/v1/event-studies/review/impact-drafts")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["event_id"] == 101 and item["title"] == "CPI 公布"
    assert item["t0"] == "2026-08-03"
    assert "pre_event_5d" in item["assets"]["000001.SH"]


def test_confirm_impacts_skips_error_windows_and_conflicts(client):
    _seed_event_row(_review_pg_url(), event_id=101, title="CPI 公布",
                    announced_at="2026-08-01T09:00:00+08:00", status="approved")
    _seed_impact(client, _impact_draft())
    resp = client.http.post(
        "/api/v1/event-studies/review/impact-drafts/101/confirm",
        json={"tickers": ["000001.SH", "000300.SH", "999999.SH"], "operator": "tester"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"event_id": 101, "inserted": 3}
    # 仅正常窗口落表（000001 pre_event_5d+event_day、000300 event_day；000001 error 窗口跳过；
    # 999999 不在草稿 → 跳过）
    from sqlalchemy import create_engine, text

    engine = create_engine(_review_pg_url())
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT asset_id, window_type FROM event_impacts WHERE event_id = 101 ORDER BY window_type")
        ).all()
    engine.dispose()
    assert sorted(r.window_type for r in rows) == ["event_day", "event_day", "pre_event_5d"]
    assert client.redis.exists("event_impacts:draft:101") == 0
    log = _review_log(client)
    assert log[0]["action"] == "confirm_impacts" and log[0]["operator"] == "tester"
    assert log[0]["changed_fields"]["assets"] == ["000001.SH", "000300.SH", "999999.SH"]
    # 重复确认（草稿已删）→ 404
    resp2 = client.http.post(
        "/api/v1/event-studies/review/impact-drafts/101/confirm",
        json={"tickers": ["000001.SH"]},
    )
    assert resp2.status_code == 404
    assert resp2.json()["code"] == "REVIEW_DRAFT_NOT_FOUND"


# ==================== 10. Redis 不可用 ====================

def test_redis_unavailable_503(client, monkeypatch):
    monkeypatch.setattr("AI.eventStudy.review.review_dao.is_redis_available", lambda: False)
    resp = client.http.get("/api/v1/event-studies/review/pending-events")
    assert resp.status_code == 503
    problem = resp.json()
    assert problem["code"] == "REVIEW_UPSTREAM_UNAVAILABLE" and problem["retryable"] is True


# ==================== 11. 参数校验 ====================

def test_validation_errors(client):
    cases = [
        ({"items": []}, 422),
        ({"items": [_batch_row(1, "approve", importance=6)]}, 422),
        ({"items": [_batch_row(1, "bogus")]}, 422),
        ({"items": [_batch_row(i, "approve") for i in range(51)]}, 422),
    ]
    for payload, expected in cases:
        resp = client.http.post("/api/v1/event-studies/review/batch", json=payload)
        assert resp.status_code == expected, payload
        assert resp.json()["code"] == "VALIDATION_ERROR"
    # limit 越界
    resp = client.http.post("/api/v1/event-studies/review/prelabel", json={"limit": 201})
    assert resp.status_code == 422
