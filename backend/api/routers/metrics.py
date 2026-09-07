"""Prometheus /metrics（不属于 /api/v1 业务 API）。

T1 仅暴露 API 请求计数；队列深度、任务状态数量、SSE 连接等指标在 T10 补齐。
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from backend.bootstrap.observability import render_metrics

router = APIRouter(tags=["system"])

PROMETHEUS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=render_metrics(), media_type=PROMETHEUS_MEDIA_TYPE)
