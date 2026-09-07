"""E2E：API 创建 → Dispatcher 投递 → Worker(fake graph) 执行 → SSE/报告/看板/指标 全链路。

浏览器级 E2E（Playwright + Compose）依赖前端工程，另行阻塞；本层用 fake graph
覆盖后端完整链路（无 LLM）。loopback 绑定检查见 T1 人工验收（netstat 已验证）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import OutboxDispatcherService, TaskService
from backend.modules.analysis.infrastructure.redis_task_event_stream import RedisTaskEventStream
from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
from backend.modules.analysis.infrastructure.trading_graph_adapter import TradingGraphAdapter
from backend.shared.clock import SystemClock
from backend.tests.unit.analysis.fakes import FakeCalendar, FakeClock
from backend.workers.analysis_executor import AnalysisExecutor

RETRY_CFG = type("Retry", (), {"max_retry_attempts": 3, "retry_base_delay_seconds": 60})()

CREATE_BODY = {
    "task_type": "SINGLE_STOCK",
    "ticker": "000001.SZ",
    "requested_trade_date": "2026-09-04",
    "selected_layers": ["market", "sector", "stock"],
    "analysis_options": {"include_memory": True},
}


class E2EBundle:
    def __init__(self, env, clock):
        self._sf = env.session_factory
        self._stream = RedisTaskEventStream(env.redis)
        self._clock = clock

    def __enter__(self):
        self._uow = SqlAlchemyAnalysisUnitOfWork(self._sf).__enter__()
        self.uow = self._uow
        self.outbox = self._uow.outbox
        self.events = TaskEventService(self._uow, clock=self._clock, stream=self._stream)
        self.tasks = TaskService(
            self._uow,
            clock=self._clock,
            calendar=FakeCalendar(),
            events=self.events,
            lease_ttl_seconds=120,
            retry=RETRY_CFG,
        )
        self.reports = ReportService(clock=self._clock)
        self.build_artifact = _artifact
        return self

    def __exit__(self, *args):
        self._uow.__exit__(*args)


class BundleFactory:
    def __init__(self, env, clock):
        self._env, self._clock = env, clock

    def open(self):
        return E2EBundle(self._env, self._clock)


class FakeGraph:
    def __init__(self, *, messages=("市场层分析完成", "板块层分析完成", "个股层分析完成"), final_state=None):
        self._messages = messages
        self._final_state = final_state or {
            "selected_layers": ["market", "sector", "stock"],
            "international_news_report": "全球风险偏好回暖",
            "sector_news_report": "半导体景气",
            "stock_tech_report": "技术面偏多",
            "decision": "综合判断：买入评级，目标价上调。",
            "risk_gate": "通过：无系统性风险",
        }

    def propagate(self, init_state, progress_callback):
        for message in self._messages:
            progress_callback(message)
        return self._final_state


def _artifact(state):
    from backend.modules.analysis.application.contracts import AnalysisArtifact

    return AnalysisArtifact(
        report_json={
            "sections": [
                {"block": "market", "status": "AVAILABLE", "title": "市场环境", "content": "回暖"},
                {"block": "decision", "status": "AVAILABLE", "title": "交易决策", "content": "买入"},
            ]
        },
        conclusion_summary="买入评级，目标价上调。",
        risk_flag=False,
        risk_hint=None,
        decision={"direction": "up"},
        artifact_uri=None,
        checksum=None,
        duration_ms=100,
    )


def test_full_task_flow_api_to_report(env):
    clock = FakeClock()
    # 1) API 创建任务
    key = f"e2e-{uuid.uuid4().hex[:8]}"
    response = env.http.post(
        "/api/v1/analysis-tasks", json=CREATE_BODY,
        headers={"Idempotency-Key": key, "X-Trace-ID": f"trace-{key}"},
    )
    assert response.status_code == 202
    task_id = response.json()["data"]["task_id"]

    # 2) Dispatcher 投递（RecordingPublisher → Broker 语义由 T4 集成测试覆盖）
    published: list = []

    class Publisher:
        def publish(self, payload):
            published.append(payload)

    bf = BundleFactory(env, clock)
    with bf.open() as b:
        dispatcher = OutboxDispatcherService(
            b.uow, task_service=b.tasks, publisher=Publisher(), clock=clock, dispatch_lease_seconds=30
        )
        assert dispatcher.dispatch_due(limit=10) == 1
    assert len(published) == 1

    # 3) Worker 执行（fake graph）
    executor = AnalysisExecutor(
        graph_adapter=TradingGraphAdapter(lambda layers=None: FakeGraph()),
        bundle_factory=bf,
        worker_id="e2e-w1",
        heartbeat_interval_seconds=0.05,
        max_attempt_runtime_seconds=3600,
    )
    from backend.workers.analysis_actor import run_analysis_task

    run_analysis_task(task_id, 1, bundle_factory=bf, executor=executor)

    # 4) 任务详情 → SUCCEEDED；报告可读
    detail = env.http.get(f"/api/v1/analysis-tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "SUCCEEDED"
    report = env.http.get(f"/api/v1/analysis-tasks/{task_id}/report")
    assert report.status_code == 200
    assert report.json()["data"]["report_version"] == 1
    blocks = {s["block"]: s["status"] for s in report.json()["data"]["sections"]}
    assert blocks["decision"] == "AVAILABLE"

    # 5) 看板聚合包含该结论
    dashboard = env.http.get("/api/v1/analysis-dashboard")
    assert dashboard.status_code == 200
    conclusions = dashboard.json()["data"]["recent_conclusions"]
    assert any(item["task_id"] == task_id and item["has_report"] for item in conclusions)
    assert conclusions[0]["conclusion_summary"] == "买入评级，目标价上调。"

    # 6) SSE 回放：queued/started/progress…/completed 后关闭
    with env.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events") as sse:
        frames = []
        current = None
        for line in sse.iter_lines():
            if line.startswith("id: "):
                if current:
                    frames.append(current)
                current = {"id": line[4:], "event": None, "data": None}
            elif line.startswith("event: "):
                if current is None or current["event"] is not None:
                    if current:
                        frames.append(current)
                    current = {"id": None, "event": None, "data": None}
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
        if current:
            frames.append(current)
    events = [f["event"] for f in frames]
    assert events[0] == "queued"
    assert events[-1] == "completed"
    assert "started" in events and "progress" in events
    assert all(f["data"]["schema_version"] == "v1" for f in frames)

    # 7) 指标：任务状态数量与队列深度
    metrics = env.http.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert "liveprofit_task_status_total" in text
    assert "liveprofit_outbox_queue_depth" in text
    assert "liveprofit_sse_connections" in text


def test_idempotent_retry_via_api_same_key(env):
    key = f"e2e-idem-{uuid.uuid4().hex[:8]}"
    first = env.http.post("/api/v1/analysis-tasks", json=CREATE_BODY, headers={"Idempotency-Key": key})
    second = env.http.post("/api/v1/analysis-tasks", json=CREATE_BODY, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["task_id"] == second.json()["data"]["task_id"]
