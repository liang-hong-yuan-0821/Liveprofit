# test-catalog-begin
# {
#   "purpose": "每日投研运行入口 API 契约。",
#   "keywords": [
#     "每日研究",
#     "每日",
#     "历史审计",
#     "幂等",
#     "新闻",
#     "复用",
#     "任务",
#     "daily_research",
#     "daily",
#     "history",
#     "idempotent",
#     "news",
#     "share",
#     "task"
#   ],
#   "covers": [
#     "backend/api/routers/daily_research.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

"""每日投研运行入口 API 契约。"""

from __future__ import annotations

import uuid


def _start(client, *, key: str, kind: str = "news"):
    return client.http.post(
        "/api/v1/daily-research/runs",
        json={"kind": kind},
        headers={"Idempotency-Key": key, "X-Trace-ID": f"trace-{key}"},
    )


def test_manual_news_and_quant_runs_share_idempotent_envelope(client):
    news_key = f"daily-news-{uuid.uuid4().hex}"
    news = _start(client, key=news_key)
    replay = _start(client, key=news_key)
    quant = _start(client, key=f"daily-quant-{uuid.uuid4().hex}", kind="quant")

    assert news.status_code == replay.status_code == quant.status_code == 202
    news_body = news.json()
    assert set(news_body) == {"data", "meta"}
    assert news_body["meta"]["schema_version"] == "v1"
    assert news_body["data"]["kind"] == "news"
    assert news_body["data"]["trigger"] == "manual"
    assert news_body["data"]["idempotent_replay"] is False
    assert replay.json()["data"]["task_id"] == news_body["data"]["task_id"]
    assert replay.json()["data"]["idempotent_replay"] is True
    assert quant.json()["data"]["kind"] == "quant"


def test_manual_run_reused_key_with_other_kind_is_conflict(client):
    key = f"daily-reuse-{uuid.uuid4().hex}"
    assert _start(client, key=key, kind="news").status_code == 202

    response = _start(client, key=key, kind="quant")

    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_daily_research_history_and_detail_are_read_only_task_projections(client):
    key = f"daily-detail-{uuid.uuid4().hex}"
    task_id = _start(client, key=key).json()["data"]["task_id"]

    listed = client.http.get("/api/v1/daily-research/runs?kind=news&limit=10")
    assert listed.status_code == 200
    row = next(item for item in listed.json()["data"]["items"] if item["task_id"] == task_id)
    assert row["kind"] == "news"
    assert row["trigger"] == "manual"
    assert row["has_report"] is False

    detail = client.http.get(f"/api/v1/daily-research/runs/{task_id}")
    assert detail.status_code == 200
    item = detail.json()["data"]["item"]
    assert item["run"]["task_id"] == task_id
    assert item["report"] is None


def test_manual_run_requires_idempotency_key_and_supported_kind(client):
    missing_key = client.http.post("/api/v1/daily-research/runs", json={"kind": "news"})
    invalid_kind = client.http.post(
        "/api/v1/daily-research/runs",
        json={"kind": "event"},
        headers={"Idempotency-Key": f"bad-kind-{uuid.uuid4().hex}"},
    )

    assert missing_key.status_code == 422
    assert missing_key.json()["code"] == "VALIDATION_ERROR"
    assert invalid_kind.status_code == 422
