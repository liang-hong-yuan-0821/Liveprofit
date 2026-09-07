"""health 路由与 trace 中间件测试（fake 探测，无真实 PG/Redis）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.routers import health as health_module
from backend.bootstrap.settings import ApiSettings, CoreSettings, Settings
from backend.main import create_app


def make_settings() -> Settings:
    return Settings(
        core=CoreSettings(
            env="local",
            database_url=SecretStr("postgresql+psycopg://u:p@127.0.0.1:5432/db"),
            redis_url=SecretStr("redis://127.0.0.1:6379/0"),
        ),
        api=ApiSettings(),
    )


def test_health_live_returns_200():
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_health_ready_reports_not_ready_when_probes_fail(monkeypatch):
    async def failing_probe(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(health_module, "probe_postgres", failing_probe)
    monkeypatch.setattr(health_module, "probe_redis", failing_probe)

    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"] == "not_ready"
    assert body["checks"]["redis"] == "not_ready"


def test_health_ready_reports_ready_when_probes_ok(monkeypatch):
    async def ok_probe(*args, **kwargs):
        return None

    monkeypatch.setattr(health_module, "probe_postgres", ok_probe)
    monkeypatch.setattr(health_module, "probe_redis", ok_probe)

    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_trace_id_header_and_metrics():
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/live", headers={"X-Trace-ID": "trace-abc"})
        assert response.headers.get("X-Trace-ID") == "trace-abc"
        metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert b"liveprofit_api_requests_total" in metrics_response.content
