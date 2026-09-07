"""报告契约测试（§2.6：最新版本、三态区块、404）。"""

from __future__ import annotations

import json
import uuid

from backend.tests.contract.api.test_analysis_tasks import _create


def _seed_report(client, task_id: str, sections: list[dict]) -> None:
    """直接经 SQL 落一条报告（绕过完整任务闭环，聚焦报告契约）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from backend.tests.contract.api.conftest import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO analysis_reports "
                "(id, task_id, attempt_no, report_version, schema_version, report_json, "
                " conclusion_summary, risk_flag, risk_hint, has_report, has_unavailable_sections, generated_at) "
                "VALUES (:id, :task_id, 1, 1, 'v1', :report_json, :summary, :risk_flag, :risk_hint, true, "
                " :has_unavailable, now())"
            ),
            {
                "id": uuid.uuid4(),
                "task_id": task_id,
                "report_json": json.dumps({"sections": sections}, ensure_ascii=False),
                "summary": "买入评级",
                "risk_flag": True,
                "risk_hint": "注意流动性",
                "has_unavailable": any(s.get("status") == "UNAVAILABLE" for s in sections),
            },
        )
    engine.dispose()


def test_report_404_when_missing(client):
    response = client.http.get(f"/api/v1/analysis-tasks/{uuid.uuid4()}/report")
    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"


def test_report_sections_three_states_and_meta(client):
    task_id = _create(client, f"report-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    _seed_report(
        client,
        task_id,
        [
            {"block": "market", "status": "AVAILABLE", "title": "市场环境", "content": "ok"},
            {"block": "sector", "status": "UNAVAILABLE", "unavailable_reason": "数据源失败", "retryable": True},
            {"block": "stock", "status": "NOT_REQUESTED"},
            {"block": "decision", "status": "AVAILABLE", "content": "建议关注"},
        ],
    )
    response = client.http.get(f"/api/v1/analysis-tasks/{task_id}/report")
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["schema_version"] == "v1"
    data = body["data"]
    assert data["schema_version"] == "v1"
    assert data["report_version"] == 1
    assert data["task"]["task_id"] == task_id
    assert [s["block"] for s in data["sections"]] == ["market", "sector", "stock", "decision"]
    by_block = {s["block"]: s for s in data["sections"]}
    assert by_block["market"]["status"] == "AVAILABLE"
    assert by_block["sector"]["status"] == "UNAVAILABLE"
    assert by_block["sector"]["unavailable_reason"] == "数据源失败"
    assert by_block["stock"]["status"] == "NOT_REQUESTED"


def test_report_no_version_query_param(client):
    task_id = _create(client, f"report-v-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    _seed_report(client, task_id, [{"block": "decision", "status": "AVAILABLE"}])
    # 契约：无版本 Query 参数——即使携带 ?version=1 也只返回最新版本
    response = client.http.get(f"/api/v1/analysis-tasks/{task_id}/report?version=1")
    assert response.status_code == 200
    assert response.json()["data"]["report_version"] == 1
