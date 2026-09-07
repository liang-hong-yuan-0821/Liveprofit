"""事件研究与宏观信息契约测试（Q-05 冻结契约 / §2.6.1）。"""

from __future__ import annotations

import json
import uuid

from backend.modules.event_study.application.contracts import EventStudyAssetDTO, PredictionDTO


class _DummyExecutor:
    def stop_accepting(self) -> None:
        pass

    def shutdown(self, *, wait, cancel_futures) -> None:
        pass


class FakeEventStudyService:
    """契约测试注入：绕过真实 predictor（契约层只验证协议与错误映射）。"""

    def __init__(self, *, busy: bool = False, timeout: bool = False) -> None:
        self._busy = busy
        self._timeout = timeout
        self._executor = _DummyExecutor()

    async def predict(self, command):
        from backend.modules.event_study.application.errors import (
            EventStudyBusyError,
            EventStudyTimeoutError,
        )

        if self._busy:
            raise EventStudyBusyError("繁忙")
        if self._timeout:
            raise EventStudyTimeoutError("超时")
        return PredictionDTO(
            prediction={"direction": "up", "predicted_return": 0.02, "confidence": 0.6},
            template_stats={"sample_count": 10, "avg_car": 0.01, "win_rate": 0.55},
            supplement_events=[{"event_id": 1, "title": "…", "similarity": 0.9, "weight": 0.3}],
            note=None,
        )

    def list_assets_sync(self):
        return [
            EventStudyAssetDTO(ticker="000001.SH", name="上证指数", market="CN"),
            EventStudyAssetDTO(ticker="000688.SH", name="科创50", market="CN"),
            EventStudyAssetDTO(ticker="000698.SH", name="科创100", market="CN"),
            EventStudyAssetDTO(ticker="000300.SH", name="沪深300", market="CN"),
        ]


def _install_fake_service(client, **kwargs):
    client.http.app.state.event_study_service = FakeEventStudyService(**kwargs)


def test_predict_success_envelope(client):
    _install_fake_service(client)
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "央行降息", "asset_ticker": "000001.SH"},
        headers={"X-Trace-ID": "trace-predict"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["schema_version"] == "v1"
    data = body["data"]
    assert data["prediction"]["direction"] == "up"
    assert data["template_stats"]["sample_count"] == 10
    assert data["supplement_events"][0]["event_id"] == 1


def test_predict_validation_422(client):
    _install_fake_service(client)
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "", "asset_ticker": "000001.SH"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "x" * 20001, "asset_ticker": "000001.SH"},
    )
    assert response.status_code == 422
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "x", "asset_ticker": "000001.SH", "window_type": "bogus"},
    )
    assert response.status_code == 422


def test_predict_busy_and_timeout_problem_details(client):
    _install_fake_service(client, busy=True)
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "x", "asset_ticker": "000001.SH"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "EVENT_STUDY_BUSY"
    assert response.json()["retryable"] is True

    _install_fake_service(client, timeout=True)
    response = client.http.post(
        "/api/v1/event-studies/predictions",
        json={"event_text": "x", "asset_ticker": "000001.SH"},
    )
    assert response.status_code == 504
    assert response.json()["code"] == "EVENT_STUDY_TIMEOUT"
    assert response.json()["retryable"] is True


def test_assets_returns_four_cn_indices(client):
    _install_fake_service(client)
    response = client.http.get("/api/v1/event-studies/assets")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["ticker"] for item in items] == ["000001.SH", "000688.SH", "000698.SH", "000300.SH"]


def _seed_macro(rows: list[dict]) -> None:
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from backend.tests.contract.api.conftest import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO macro_information "
                    "(id, event_id, title, occurred_at, market_tags, macro_topic, summary, source, "
                    " related_assets, review_status, research_status) "
                    "VALUES (:id, :event_id, :title, :occurred_at, :market_tags, :topic, :summary, :source, "
                    " :related, :review, :research)"
                ),
                {
                    "id": uuid.uuid4(),
                    "event_id": row.get("event_id"),
                    "title": row["title"],
                    "occurred_at": row.get("occurred_at"),
                    "market_tags": json.dumps(row.get("market_tags", ["CN"]), ensure_ascii=False),
                    "topic": row.get("topic"),
                    "summary": row.get("summary"),
                    "source": row.get("source"),
                    "related": json.dumps(row.get("related") or [], ensure_ascii=False),
                    "review": row.get("review", "APPROVED"),
                    "research": row.get("research", "已审核"),
                },
            )
    engine.dispose()


def test_macro_information_approved_only_and_pagination(client):
    _seed_macro(
        [
            {"title": "第一条", "occurred_at": "2026-09-05T08:00:00Z", "topic": "货币政策"},
            {"title": "第二条", "occurred_at": "2026-09-04T08:00:00Z"},
            {"title": "待审核", "occurred_at": "2026-09-03T08:00:00Z", "review": "PENDING"},
        ]
    )
    response = client.http.get("/api/v1/macro-information?limit=10")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["title"] for item in items] == ["第一条", "第二条"]  # PENDING 不返回
    assert items[0]["event_id"] is None
    assert items[0]["market_tags"] == ["CN"]

    # 分页：limit=1 → next_cursor → 第二页
    page1 = client.http.get("/api/v1/macro-information?limit=1")
    assert page1.json()["meta"]["next_cursor"] is not None
    page2 = client.http.get(
        f"/api/v1/macro-information?limit=1&cursor={page1.json()['meta']['next_cursor']}"
    )
    assert [item["title"] for item in page2.json()["data"]["items"]] == ["第二条"]
    assert page2.json()["meta"]["next_cursor"] is None

    # 筛选：topic
    filtered = client.http.get("/api/v1/macro-information?limit=10&topic=货币政策")
    assert [item["title"] for item in filtered.json()["data"]["items"]] == ["第一条"]


def test_macro_information_empty_is_normal(client):
    response = client.http.get("/api/v1/macro-information?limit=10")
    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
