"""执行调用日志 REST 契约测试（任务执行调用日志方案 §3.2.1 / §3.2.3）。

树端点：任务不存在 → 404 TASK_NOT_FOUND；目录不存在 → 200 available=false；
树构建与 DTO 结构。content 端点：路径逃逸 → 422、不存在文件 → 404、
白名单扩展名、超限 → 422、全量内容。
"""

from __future__ import annotations

import json
import uuid

from backend.tests.contract.api.test_analysis_tasks import _create


def _create_task(client) -> dict:
    key = f"exec-logs-{uuid.uuid4().hex[:8]}"
    response = _create(client, key)
    assert response.status_code == 202, response.text
    task_id = response.json()["data"]["task_id"]
    detail = client.http.get(f"/api/v1/analysis-tasks/{task_id}")
    assert detail.status_code == 200
    return {**detail.json()["data"], "task_id": task_id}


def _point_logs_root(client, monkeypatch, root) -> None:
    """把 execution_logs_root 指向测试目录（相对路径默认指向仓库根 logs/，不污染）。"""
    monkeypatch.setattr(client.http.app.state.settings.core, "execution_logs_root", root)


def _run_dir(root, task) -> str:
    return root / "tasks" / task["task_id"] / str(task["attempt_no"])


def test_tree_404_when_task_missing(client):
    response = client.http.get(f"/api/v1/analysis-tasks/{uuid.uuid4()}/execution-logs")
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_tree_available_false_when_dir_missing(client):
    """PENDING 尚未建目录是正常态：200 + available=false，layers 为空。"""
    task = _create_task(client)
    response = client.http.get(f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"data", "meta"}
    data = body["data"]
    assert data["task_id"] == task["task_id"]
    assert data["attempt_no"] == task["attempt_no"]
    assert data["available"] is False
    assert data["layers"] == []
    assert data["generated_at"] is not None


def test_tree_returns_full_structure(client, monkeypatch, tmp_path):
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    run_dir = _run_dir(tmp_path, task)
    run_dir.mkdir(parents=True)

    node = run_dir / "market" / "001_News_Analyst"
    node.mkdir(parents=True)
    (node / "req.md").write_text("提示词", encoding="utf-8")
    (node / "res.md").write_text("# 结果", encoding="utf-8")
    (node / "meta.json").write_text(
        json.dumps({"model": "gpt-4o", "node": "News Analyst", "seq": 1}), encoding="utf-8")
    dp = node / "001_get_news"
    dp.mkdir()
    (dp / "req.json").write_text('{"kind": "cn"}', encoding="utf-8")
    (dp / "res.md").write_text("# 新闻", encoding="utf-8")
    (dp / "meta.json").write_text(
        json.dumps({"name": "get_news", "desc": "获取新闻", "seq": 1, "ts": "t", "res": "res.md"}),
        encoding="utf-8")

    response = client.http.get(f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert len(data["layers"]) == 1
    layer = data["layers"][0]
    assert layer["name"] == "market"
    node_dto = layer["nodes"][0]
    assert node_dto["dir"] == "market/001_News_Analyst"
    assert node_dto["node"] == "News Analyst"
    assert node_dto["model"] == "gpt-4o"
    assert node_dto["llm_req"]["content"] == "提示词"
    dp_dto = node_dto["dp_calls"][0]
    assert dp_dto["name"] == "get_news"
    assert dp_dto["req"] == {"kind": "cn"}
    assert dp_dto["res"]["content"] == "# 新闻"


def test_content_404_when_task_missing(client):
    response = client.http.get(
        f"/api/v1/analysis-tasks/{uuid.uuid4()}/execution-logs/content", params={"file": "a.md"})
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_content_path_escape_rejected(client, monkeypatch, tmp_path):
    """路径逃逸：../、绝对路径、反斜杠、非白名单扩展名 → 422 VALIDATION_ERROR。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    _run_dir(tmp_path, task).mkdir(parents=True)
    base = f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs/content"

    for bad in ["../evil.md", "/etc/passwd", "..\\evil.md", "sub/..\\evil.md", "evil.exe", ""]:
        response = client.http.get(base, params={"file": bad})
        assert response.status_code == 422, f"file={bad!r} 应 422，实际 {response.status_code}"
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_content_over_10mb_422(client, monkeypatch, tmp_path):
    """content 端点单文件上限 10MB：超限 → 422 VALIDATION_ERROR（稀疏文件，不实际写 10MB）。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    run_dir = _run_dir(tmp_path, task)
    node = run_dir / "market" / "001_N"
    node.mkdir(parents=True)
    big = node / "big.md"
    with big.open("wb") as f:
        f.seek(10 * 1024 * 1024)  # 10MB + 1 字节
        f.write(b"x")

    response = client.http.get(
        f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs/content",
        params={"file": "market/001_N/big.md"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_content_missing_file_404(client, monkeypatch, tmp_path):
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    _run_dir(tmp_path, task).mkdir(parents=True)
    response = client.http.get(
        f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs/content",
        params={"file": "nope.md"})
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def _mark_terminal(task_id: str) -> None:
    """直接 SQL 置终态（DELETE 仅终态任务可删；与 test_report.py 的直连种子同模式）。"""
    from sqlalchemy import create_engine, text

    from backend.bootstrap.settings import CoreSettings
    from backend.tests.contract.api.conftest import _test_db_url

    base_url = CoreSettings().resolved_database_url()
    engine = create_engine(_test_db_url(base_url))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE analysis_tasks SET status='SUCCEEDED' WHERE id=:id"), {"id": task_id})
    engine.dispose()


def test_delete_task_cleans_log_dir(client, monkeypatch, tmp_path):
    """删除任务后 logs/tasks/{task_id}/ 整个目录（含各 attempt）被移除。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    run_dir = _run_dir(tmp_path, task)
    (run_dir / "market").mkdir(parents=True)
    (run_dir / "req.md").write_text("x", encoding="utf-8")
    _mark_terminal(task["task_id"])

    response = client.http.delete(f"/api/v1/analysis-tasks/{task['task_id']}")
    assert response.status_code == 200, response.text
    assert not (tmp_path / "tasks" / task["task_id"]).exists()


def test_content_serves_full_content(client, monkeypatch, tmp_path):
    """白名单扩展名文件全量返回（truncated=false），路径为相对任务目录。"""
    task = _create_task(client)
    _point_logs_root(client, monkeypatch, tmp_path)
    run_dir = _run_dir(tmp_path, task)
    run_dir.mkdir(parents=True)
    node = run_dir / "market" / "001_N"
    node.mkdir(parents=True)
    (node / "res.md").write_text("# 完整内容", encoding="utf-8")

    response = client.http.get(
        f"/api/v1/analysis-tasks/{task['task_id']}/execution-logs/content",
        params={"file": "market/001_N/res.md"})
    assert response.status_code == 200
    dto = response.json()["data"]
    assert dto["path"] == "market/001_N/res.md"
    assert dto["kind"] == "md"
    assert dto["content"] == "# 完整内容"
    assert dto["truncated"] is False
    assert dto["parse_error"] is False
    assert dto["total_bytes"] == len("# 完整内容".encode("utf-8"))
