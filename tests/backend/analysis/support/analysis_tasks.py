"""Shared fixtures/builders for tests.backend.analysis.contract.api.test_analysis_tasks; no test cases."""

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
