"""任务 REST 契约测试（§2.6.2 / §2.6.4：envelope、DTO 白名单、幂等、错误体）。"""

from __future__ import annotations

import json
import uuid

CREATE_BODY = {
    "task_type": "SINGLE_STOCK",
    "ticker": "000001.SZ",
    "requested_trade_date": "2026-09-04",
    "selected_layers": ["market", "sector", "stock"],
    "analysis_options": {"include_memory": True},
}


def _create(client, key: str, body: dict | None = None):
    return client.http.post(
        "/api/v1/analysis-tasks",
        json=body or CREATE_BODY,
        headers={"Idempotency-Key": key, "X-Trace-ID": f"trace-{key}"},
    )


def test_create_task_202_envelope_and_derived_urls(client):
    response = _create(client, f"key-{uuid.uuid4().hex[:8]}")
    assert response.status_code == 202
    body = response.json()
    assert set(body.keys()) == {"data", "meta"}
    assert body["meta"]["schema_version"] == "v1"
    assert body["meta"]["request_id"].startswith("trace-")
    data = body["data"]
    assert data["status"] == "PENDING"
    task_id = data["task_id"]
    assert data["events_url"] == f"/api/v1/analysis-tasks/{task_id}/events"
    assert data["report_url"] == f"/api/v1/analysis-tasks/{task_id}/report"
    assert data["requested_trade_date"] == "2026-09-04"


def test_missing_idempotency_key_422_problem(client):
    response = client.http.post("/api/v1/analysis-tasks", json=CREATE_BODY)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert problem["retryable"] is False
    assert "request_id" in problem


def test_invalid_create_body_422_problem(client):
    # MARKET_WIDE 禁止 ticker（discriminated union 校验）
    response = client.http.post(
        "/api/v1/analysis-tasks",
        json={"task_type": "MARKET_WIDE", "ticker": "000001.SZ", "requested_trade_date": "2026-09-04",
              "selected_layers": ["market", "sector", "screening"]},
        headers={"Idempotency-Key": "key-invalid-body"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    # SINGLE_STOCK 缺 ticker
    response = client.http.post(
        "/api/v1/analysis-tasks",
        json={"task_type": "SINGLE_STOCK", "requested_trade_date": "2026-09-04",
              "selected_layers": ["market"]},
        headers={"Idempotency-Key": "key-no-ticker"},
    )
    assert response.status_code == 422
    # position 仅随 screening：服务层复验
    response = client.http.post(
        "/api/v1/analysis-tasks",
        json={"task_type": "MARKET_WIDE", "requested_trade_date": "2026-09-04",
              "selected_layers": ["market", "sector", "position"]},
        headers={"Idempotency-Key": "key-position"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "TASK_CREATE_INVALID"


def test_idempotent_replay_same_key_same_input(client):
    key = f"replay-{uuid.uuid4().hex[:8]}"
    first = _create(client, key)
    second = _create(client, key)
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["task_id"] == second.json()["data"]["task_id"]


def test_idempotency_key_reused_with_different_input_409(client):
    key = f"reuse-{uuid.uuid4().hex[:8]}"
    assert _create(client, key).status_code == 202
    changed = dict(CREATE_BODY, requested_trade_date="2026-09-03")
    response = _create(client, key, changed)
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_list_tasks_envelope_fields_and_cursor_pagination(client):
    for i in range(3):
        assert _create(client, f"list-{uuid.uuid4().hex[:8]}").status_code == 202

    response = client.http.get("/api/v1/analysis-tasks?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert set(body["data"].keys()) == {"items"}
    assert len(body["data"]["items"]) == 2
    item = body["data"]["items"][0]
    # TaskListItemDTO 字段白名单（不泄露 request_params/幂等键/租约/Outbox）
    assert set(item.keys()) == {
        "id", "task_type", "ticker", "effective_trade_date", "status", "attempt_no",
        "error_code", "error_summary", "created_at", "updated_at",
    }
    assert body["meta"]["next_cursor"] is not None

    page2 = client.http.get(f"/api/v1/analysis-tasks?limit=2&cursor={body['meta']['next_cursor']}")
    assert page2.status_code == 200
    page2_ids = {item["id"] for item in page2.json()["data"]["items"]}
    page1_ids = {item["id"] for item in body["data"]["items"]}
    assert page1_ids.isdisjoint(page2_ids)
    assert page2.json()["meta"]["next_cursor"] is None  # 最后一页


def test_invalid_status_filter_422(client):
    response = client.http.get("/api/v1/analysis-tasks?status=bogus")
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TASK_FILTER"


def test_get_task_detail_and_404(client):
    task_id = _create(client, f"detail-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    response = client.http.get(f"/api/v1/analysis-tasks/{task_id}")
    assert response.status_code == 200
    dto = response.json()["data"]
    assert dto["task_type"] == "SINGLE_STOCK"
    assert dto["selected_layers"] == ["market", "sector", "stock"]
    assert dto["events_url"] == f"/api/v1/analysis-tasks/{task_id}/events"
    assert dto["report_url"] == f"/api/v1/analysis-tasks/{task_id}/report"

    missing = client.http.get(f"/api/v1/analysis-tasks/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "TASK_NOT_FOUND"


def test_cancel_pending_task_to_cancelled(client):
    task_id = _create(client, f"cancel-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    response = client.http.post(f"/api/v1/analysis-tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"
    # 重复取消幂等
    again = client.http.post(f"/api/v1/analysis-tasks/{task_id}/cancel")
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "CANCELLED"


def test_dashboard_empty_is_normal(client):
    response = client.http.get("/api/v1/analysis-dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["pending_actions"] == []
    assert body["data"]["active_tasks"] == []
    assert body["data"]["recent_conclusions"] == []
    assert body["meta"]["schema_version"] == "v1"


def test_unknown_api_route_404_problem(client):
    response = client.http.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    problem = response.json()
    assert problem["code"] == "RESOURCE_NOT_FOUND"
    assert problem["retryable"] is False


def test_problem_details_never_leak_internals(client):
    response = _create(client, f"leak-{uuid.uuid4().hex[:8]}")
    assert response.status_code == 202
    # 用非法 cursor 触发 422，确认错误体不含堆栈/Token
    response = client.http.get("/api/v1/analysis-tasks?cursor=%%%invalid%%%")
    assert response.status_code == 422
    problem = response.json()
    assert set(problem.keys()) >= {"type", "title", "status", "detail", "code", "request_id", "retryable"}
    rendered = json.dumps(problem, ensure_ascii=False)
    assert "Traceback" not in rendered and "token" not in rendered.lower()


# ---------- 单Agent重跑（单Agent重跑与提示词编辑方案 3.4） ----------

def _to_terminal(client, task_id: str, status: str = "FAILED", attempt_no: int = 1) -> None:
    with client.http.app.state.analysis_services.open() as bundle:
        ok = bundle.uow.tasks.conditional_update(
            task_id, expect={"attempt_no": attempt_no}, changes={"status": status})
        bundle.uow.commit()
        assert ok


def _point_logs_root(client, monkeypatch, root) -> None:
    monkeypatch.setattr(client.http.app.state.settings.core, "execution_logs_root", root)


def _make_complete_attempt(root, task_id: str, attempt_no: int = 1) -> None:
    """伪造完整 attempt 目录：complete.json + CN News checkpoint（CN Tech 的前驱）。"""
    run_dir = root / "tasks" / task_id / str(attempt_no)
    (run_dir / "checkpoints" / "market").mkdir(parents=True)
    (run_dir / "complete.json").write_text('{"completed_at": "x"}', encoding="utf-8")
    (run_dir / "checkpoints" / "market" / "CN_News_Analyst.json").write_text(
        '{"saved_at": "x", "node_id": "market:CN News Analyst", "state": {"messages": []}}',
        encoding="utf-8",
    )


def test_rerun_terminal_task_200_pending_next_attempt(client, monkeypatch, tmp_path):
    task = _create(client, f"rerun-{uuid.uuid4().hex[:8]}")
    task_id = task.json()["data"]["task_id"]
    _to_terminal(client, task_id, "FAILED")
    _point_logs_root(client, monkeypatch, tmp_path)
    _make_complete_attempt(tmp_path, task_id, attempt_no=1)

    response = client.http.post(
        f"/api/v1/analysis-tasks/{task_id}/rerun",
        json={"node_id": "market:CN Tech Analyst"},
        headers={"X-Trace-ID": f"trace-rerun-{task_id}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "PENDING"
    assert data["attempt_no"] == 2
    assert data["rerun_from_node_id"] == "market:CN Tech Analyst"


def test_rerun_non_terminal_409(client):
    task = _create(client, f"rerun-{uuid.uuid4().hex[:8]}")
    task_id = task.json()["data"]["task_id"]
    response = client.http.post(
        f"/api/v1/analysis-tasks/{task_id}/rerun", json={"node_id": "market:CN Tech Analyst"})
    assert response.status_code == 409
    assert response.json()["code"] == "TASK_NOT_TERMINAL"


def test_rerun_unknown_node_404(client, monkeypatch, tmp_path):
    task = _create(client, f"rerun-{uuid.uuid4().hex[:8]}")
    task_id = task.json()["data"]["task_id"]
    _to_terminal(client, task_id, "FAILED")
    _point_logs_root(client, monkeypatch, tmp_path)
    response = client.http.post(
        f"/api/v1/analysis-tasks/{task_id}/rerun", json={"node_id": "market:不存在节点"})
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NODE_NOT_FOUND"


def test_rerun_no_checkpoint_409(client, monkeypatch, tmp_path):
    task = _create(client, f"rerun-{uuid.uuid4().hex[:8]}")
    task_id = task.json()["data"]["task_id"]
    _to_terminal(client, task_id, "FAILED")
    _point_logs_root(client, monkeypatch, tmp_path)
    # 无 attempt 目录（旧版本运行）→ entry 不可用
    response = client.http.post(
        f"/api/v1/analysis-tasks/{task_id}/rerun", json={"node_id": "market:CN Tech Analyst"})
    assert response.status_code == 409
    assert response.json()["code"] == "RERUN_NOT_AVAILABLE"
