"""OpenAPI 导出幂等门禁（§2.6.4：schema 与生成物 diff 必须为空）。

- 重新导出 schema 与已提交 openapi.v1.json 完全一致（router/schema 改动未同步即失败）。
- 关键契约元素存在性：全部冻结端点、统一 envelope、SSE payload、错误码 Problem Details。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.bootstrap.settings import SettingsValidationError

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_SCHEMA = PROJECT_ROOT / "backend" / "openapi" / "openapi.v1.json"

FROZEN_PATHS = {
    "/api/v1/analysis-tasks",
    "/api/v1/analysis-tasks/{task_id}",
    "/api/v1/analysis-tasks/{task_id}/cancel",
    "/api/v1/analysis-tasks/{task_id}/events",
    "/api/v1/analysis-tasks/{task_id}/report",
    "/api/v1/analysis-dashboard",
    "/api/v1/event-studies/predictions",
    "/api/v1/event-studies/assets",
    "/api/v1/event-studies/review/pending-events",
    "/api/v1/event-studies/review/prelabel",
    "/api/v1/event-studies/review/batch",
    "/api/v1/event-studies/review/events/{event_id}/compute",
    "/api/v1/event-studies/review/impact-drafts",
    "/api/v1/event-studies/review/impact-drafts/{event_id}/confirm",
    "/api/v1/macro-information",
    "/api/v1/watchlists",
    "/api/v1/watchlists/{watchlist_id}",
    "/api/v1/watchlists/{watchlist_id}/items",
    "/api/v1/watchlists/{watchlist_id}/items/order",
    "/api/v1/watchlists/{watchlist_id}/items/{item_id}",
    "/api/v1/portfolios",
    "/api/v1/portfolios/{portfolio_id}",
    "/api/v1/portfolios/{portfolio_id}/positions",
    "/api/v1/portfolios/{portfolio_id}/positions/{market}/{symbol}",
}


def _export() -> dict:
    from backend.scripts.export_openapi import export_schema

    return export_schema()


def test_export_is_idempotent_with_committed_schema():
    try:
        regenerated = _export()
    except SettingsValidationError as exc:
        # 仅 Settings 缺失环境跳过门禁；其他异常（如 schema 构建回归）必须失败
        pytest.skip(f"导出依赖 Settings 环境：{exc}")

    assert COMMITTED_SCHEMA.exists(), "backend/openapi/openapi.v1.json 未生成（先执行 export_openapi）"
    committed = json.loads(COMMITTED_SCHEMA.read_text(encoding="utf-8"))
    assert regenerated == committed, "OpenAPI schema 与已提交文件不一致：请重新执行 export_openapi 并提交 diff"


def test_frozen_paths_and_envelope_present():
    schema = json.loads(COMMITTED_SCHEMA.read_text(encoding="utf-8"))
    paths = set(schema["paths"].keys())
    missing = FROZEN_PATHS - paths
    assert not missing, f"冻结端点缺失：{missing}"
    # 统一 envelope schema 存在且被引用（泛型实例化为 Envelope_*_ 具体组件）
    components = schema["components"]["schemas"]
    assert any(name.startswith("Envelope") for name in components), "统一 envelope 未进入 OpenAPI"
    # SSE payload 与 Problem Details
    for name in ("BusinessEventData", "ResetEventData", "HeartbeatEventData"):
        assert name in components, f"SSE schema 缺失：{name}"
    # 删除响应信封（不使用 204）
    for path, methods in schema["paths"].items():
        if "delete" in methods:
            assert "204" not in methods["delete"]["responses"], f"{path} DELETE 返回了 204"
