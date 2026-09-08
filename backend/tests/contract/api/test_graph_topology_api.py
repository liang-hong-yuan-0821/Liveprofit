"""图拓扑 REST 契约测试（任务拓扑图方案 §3.2.3）。

端点：任务不存在 → 404 TASK_NOT_FOUND；目录不存在 → 200 available=false 且
nodes 为静态结构；正常 → 200 结构断言；TaskDTO 含 graph_topology_url。
"""

from __future__ import annotations

import json
import uuid

from backend.tests.contract.api.test_analysis_tasks import _create


def _create_task(client) -> dict:
    key = f"graph-topo-{uuid.uuid4().hex[:8]}"
    response = _create(client, key)
    assert response.status_code == 202, response.text
    task_id = response.json()["data"]["task_id"]
    detail = client.http.get(f"/api/v1/analysis-tasks/{task_id}")
    assert detail.status_code == 200
    return {**detail.json()["data"], "task_id": task_id}


def _point_logs_root(client, monkeypatch, root) -> None:
    monkeypatch.setattr(client.http.app.state.settings.core, "execution_logs_root", root)


def _run_dir(root, task) -> str:
    return root / "tasks" / task["task_id"] / str(task["attempt_no"])


def test_topology_404_when_task_missing(client):
    response = client.http.get(f"/api/v1/analysis-tasks/{uuid.uuid4()}/graph-topology")
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_topology_available_false_static_structure(client, monkeypatch, tmp_path):
    """run 目录不存在：200 + available=false，nodes 仍为静态结构且全 not_executed。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    response = client.http.get(f"/api/v1/analysis-tasks/{task['task_id']}/graph-topology")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"data", "meta"}
    data = body["data"]
    assert data["task_id"] == task["task_id"]
    assert data["attempt_no"] == task["attempt_no"]
    assert data["available"] is False
    assert data["generated_at"] is not None
    # 静态结构：三层节点齐全（_create 默认 selected_layers=["market","sector","stock"]）
    assert len(data["nodes"]) == 4 + 3 + 12
    assert all(n["status"] == "not_executed" for n in data["nodes"])
    assert all(n["dirs"] == [] and n["invocation_count"] == 0 for n in data["nodes"])
    # 层间链边存在
    edge_keys = {(e["source"], e["target"], e["kind"]) for e in data["edges"]}
    assert ("market:CN Tech Analyst", "sector:Sector News Analyst", "direct") in edge_keys
    assert ("sector:Sector Rotation Analyst", "stock:Stock Tech Analyst", "direct") in edge_keys


def test_topology_status_overlay(client, monkeypatch, tmp_path):
    """目录存在：已执行节点 executed、DP error 节点 error、dirs 匹配。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    run_dir = _run_dir(tmp_path, task)

    node = run_dir / "market" / "001_International_News_Analyst"
    node.mkdir(parents=True)
    (node / "req.md").write_text("提示词", encoding="utf-8")
    (node / "res.md").write_text("# 结果", encoding="utf-8")
    (node / "meta.json").write_text(
        json.dumps({"model": "gpt-4o", "node": "International News Analyst", "seq": 1}),
        encoding="utf-8")
    dp = node / "001_get_news"
    dp.mkdir()
    (dp / "meta.json").write_text(
        json.dumps({"name": "get_news", "error": True}), encoding="utf-8")

    response = client.http.get(f"/api/v1/analysis-tasks/{task['task_id']}/graph-topology")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["market:International News Analyst"]["status"] == "error"
    assert by_id["market:International News Analyst"]["invocation_count"] == 1
    assert by_id["market:International News Analyst"]["dirs"] == ["market/001_International_News_Analyst"]
    assert by_id["market:International Event Extraction Analyst"]["status"] == "not_executed"
    # 条件边标记（intl_news 工具循环 → 出边 conditional）
    edges = {(e["source"], e["target"]): e for e in data["edges"]}
    assert edges[("market:International News Analyst", "market:CN News Analyst")]["kind"] == "conditional"


def test_task_dto_contains_graph_topology_url(client):
    task = _create_task(client)
    assert task["graph_topology_url"] == f"/api/v1/analysis-tasks/{task['task_id']}/graph-topology"
