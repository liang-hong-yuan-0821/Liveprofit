# test-catalog-begin
# {
#   "purpose": "行情服务 / market_refresh（行情刷新）",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "调度任务",
#     "市场分析",
#     "行情刷新",
#     "状态",
#     "个股分析",
#     "后台任务",
#     "market_refresh",
#     "jobs",
#     "market",
#     "refresh",
#     "status",
#     "stock",
#     "worker"
#   ],
#   "covers": [
#     "backend/modules/market_data/application/refresh_service.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError

from backend.modules.market_data.application.refresh_service import build_refresh_service


@pytest.fixture
def refresh_service(client, monkeypatch):
    state = client.http.app.state
    service = build_refresh_service(state.settings, client.redis, state.market_conn, publisher=client.publisher)
    service.clock = SimpleNamespace(now=lambda: datetime(2026, 9, 23, 2, tzinfo=timezone.utc))
    monkeypatch.setattr(state, "market_refresh_service", service, raising=False)
    return service


def test_status_is_read_only_and_empty_stock_catalog_is_blocked(client, refresh_service):
    response = client.http.get("/api/v1/market-data/refresh-status")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["refresh_available"] and len(data["groups"]) == 6
    groups = {g["resource"]: g for g in data["groups"]}
    assert groups["CN_INDEX_BARS"]["expected_trade_date"] == "2026-09-22"
    assert groups["CN_INDEX_BARS"]["auto_eligibility"]["allowed"]
    assert groups["CN_STOCK_DAILY"]["auto_eligibility"]["reason"] == "CATALOG_UNAVAILABLE"
    assert not list(client.redis.scan_iter("liveprofit:market-refresh:*"))
    assert client.publisher.job_ids == []


def test_public_resource_enum_stays_six_values_and_hides_quant_jobs(client, refresh_service):
    openapi = client.http.get("/openapi.json").json()
    assert openapi["components"]["schemas"]["Resource"]["enum"] == [
        "CN_INDEX_BARS", "CN_INDEX_FACTORS", "US_INDEX_BARS", "KR_INDEX_BARS",
        "CN_STOCK_DAILY", "CN_SECTOR_DAILY",
    ]
    assert client.http.post(
        "/api/v1/market-data/refresh",
        json={"resources": ["CN_STOCK_QUANT_INPUTS"]},
    ).status_code == 422

    internal_job_id = "internal-quant-input-job"
    refresh_service.store.redis.set(
        refresh_service.store.key(f"job:{internal_job_id}"),
        json.dumps({"id": internal_job_id, "resource": "CN_STOCK_QUANT_INPUTS"}),
    )
    assert client.http.get(f"/api/v1/market-data/refresh-jobs/{internal_job_id}").status_code == 404


def test_post_reuses_job_and_does_not_publish_to_offline_worker(client, refresh_service):
    body = dict(resources=["CN_INDEX_BARS", "CN_STOCK_DAILY"], mode="auto")
    first = client.http.post("/api/v1/market-data/refresh", json=body)
    assert first.status_code == 202, first.text
    decisions = first.json()["data"]["decisions"]
    assert decisions[1]["decision"] == "BLOCKED"
    job_id = decisions[0]["job_id"]
    second = client.http.post("/api/v1/market-data/refresh", json=body)
    assert second.json()["data"]["decisions"][0]["job_id"] == job_id
    assert client.publisher.job_ids == []
    job = client.http.get(f"/api/v1/market-data/refresh-jobs/{job_id}")
    assert job.status_code == 200 and job.json()["data"]["status"] == "QUEUED"
    assert "target_spec" not in job.json()["data"]


def test_online_publisher_and_manual_switch(client, refresh_service):
    refresh_service.store.config.auto_enabled = False
    response = client.http.post("/api/v1/market-data/refresh", json=dict(resources=["CN_INDEX_BARS"]))
    assert response.status_code == 200
    assert response.json()["data"]["decisions"][0]["reason"] == "AUTO_DISABLED"
    refresh_service.store.worker("fixture", refresh_service.clock.now().timestamp())
    response = client.http.post("/api/v1/market-data/refresh", json=dict(resources=["CN_INDEX_BARS"], mode="retry"))
    assert response.status_code == 202
    assert len(client.publisher.job_ids) == 1


@pytest.mark.parametrize("body", [dict(resources=[]), dict(resources=["INVALID"]), dict(resources=["CN_INDEX_BARS"], mode="force"), dict(resources=["CN_INDEX_BARS"], command="ignored")])
def test_invalid_refresh_request(client, refresh_service, body):
    assert client.http.post("/api/v1/market-data/refresh", json=body).status_code == 422


def test_unknown_job_and_redis_failure(client, refresh_service, monkeypatch):
    assert client.http.get("/api/v1/market-data/refresh-jobs/expired").status_code == 404
    monkeypatch.setattr(refresh_service.store, "ping", lambda: (_ for _ in ()).throw(ConnectionError()))
    response = client.http.get("/api/v1/market-data/refresh-status")
    assert response.status_code == 200 and not response.json()["data"]["refresh_available"]
    response = client.http.post("/api/v1/market-data/refresh", json=dict(resources=["CN_INDEX_BARS"]))
    assert response.status_code == 503 and response.json()["code"] == "REFRESH_UNAVAILABLE"
