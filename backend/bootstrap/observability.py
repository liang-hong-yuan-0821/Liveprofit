"""可观测性：Trace ID ContextVar、结构化日志与最小指标。

trace context 由 HTTP 写入（X-Trace-ID），后续由 Outbox 传递至 Worker/Graph（T3/T4 接入）。
最小指标先落地 API 请求计数；队列深度、任务状态、SSE 连接等在 T10 补齐。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from prometheus_client import REGISTRY, Counter, generate_latest
from prometheus_client.metrics_core import GaugeMetricFamily

TRACE_ID_VAR: ContextVar[str | None] = ContextVar("trace_id", default=None)

# 指标：API 请求计数（即时）+ 任务状态/队列深度/SSE 连接（scrape 时采集）
API_REQUESTS_TOTAL = Counter(
    "liveprofit_api_requests_total",
    "API 请求总数",
    ["method", "route", "status"],
)

_metrics_source = None


class PlatformMetricsCollector:
    """scrape 时从 PG/进程状态采集：任务状态数量、Outbox 队列深度、SSE 连接。"""

    def __init__(self) -> None:
        self._source = None

    def set_source(self, source) -> None:
        self._source = source

    def collect(self):
        if self._source is None:
            return
        try:
            task_counts, queue_depth, sse_connections = self._source()
        except Exception:  # noqa: BLE001 - 采集失败输出空指标，不影响 /metrics
            return
        family = GaugeMetricFamily(
            "liveprofit_task_status_total", "任务状态数量", labels=["status"]
        )
        for status, count in (task_counts or {}).items():
            family.add_metric([status], count)
        yield family
        yield GaugeMetricFamily(
            "liveprofit_outbox_queue_depth", "待发布 Outbox 队列深度", value=queue_depth
        )
        yield GaugeMetricFamily(
            "liveprofit_sse_connections", "活跃 SSE 连接数", value=sse_connections
        )


def register_platform_collector() -> PlatformMetricsCollector:
    collector = PlatformMetricsCollector()
    REGISTRY.register(collector)
    return collector

_logging_configured = False


def get_trace_id() -> str | None:
    return TRACE_ID_VAR.get()


def set_trace_id(trace_id: str | None) -> None:
    TRACE_ID_VAR.set(trace_id)


def new_trace_id() -> str:
    return uuid.uuid4().hex


class TraceIdFilter(logging.Filter):
    """把当前 ContextVar 中的 trace_id 注入每条日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = TRACE_ID_VAR.get() or "-"
        return True


def init_logging(level: str = "INFO") -> None:
    """结构化日志：timestamp、level、trace_id + 既有 logger 名称/消息。"""
    global _logging_configured
    if _logging_configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s trace_id=%(trace_id)s logger=%(name)s %(message)s"
        )
    )
    handler.addFilter(TraceIdFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _logging_configured = True


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
