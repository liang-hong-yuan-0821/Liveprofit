"""FastAPI 应用入口：create_app() 工厂。

- 路由只作协议转换；业务编排在 modules/<domain>/application（T3 起已接入）。
- API 不执行 propagate()、不注册定时任务（lifespan 仅装配基础设施与独立 EventStudyExecutor）。
- 生命周期拆分：init_logging / validate_settings / init_postgres / init_redis / shutdown_resources。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from prometheus_client import REGISTRY

from backend.api.analysis_services import build_api_analysis_services
from backend.api.exception_handlers import register_exception_handlers
from backend.api.routers import (
    agents,
    analysis_dashboard,
    analysis_events,
    analysis_tasks,
    event_studies,
    event_study_review,
    execution_logs,
    graph_topology,
    health,
    macro_information,
    market_assets,
    market_data,
    metrics,
    portfolios,
    reports,
    watchlists,
)
from backend.bootstrap.container import ApiContainer, build_api_container
from backend.bootstrap.observability import API_REQUESTS_TOTAL, init_logging, new_trace_id, set_trace_id
from backend.bootstrap.settings import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    init_logging(settings.core.log_level)
    logger.info(
        "API 启动（事件循环：%s）：装配 async/sync engine、redis、分析服务线程池与事件研究有界执行器",
        type(asyncio.get_running_loop()).__name__,
    )
    container: ApiContainer = build_api_container(settings)
    app.state.container = container
    app.state.analysis_services = build_api_analysis_services(container)
    app.state.event_study_service = _build_event_study_service(settings)
    app.state.event_study_executor = app.state.event_study_service._executor
    app.state.event_study_review_service = _build_event_study_review_service()
    app.state.market_calendar = _build_market_calendar()
    metrics_collector, metrics_cache, refresh_task = await _install_metrics(app, container)
    try:
        yield
    finally:
        logger.info("API 关闭：停止事件研究准入、回收线程池并释放连接")
        app.state.event_study_executor.stop_accepting()
        await asyncio.sleep(0.2)  # 受控期限：等待在飞请求收尾
        app.state.event_study_executor.shutdown(wait=False, cancel_futures=True)
        refresh_task.cancel()
        REGISTRY.unregister(metrics_collector)
        app.state.analysis_services.shutdown()
        await container.dispose()


def _build_market_calendar():
    """市场新鲜度/开闭市判定日历（CN；测试可注入 FakeCalendar 覆盖 app.state.market_calendar）。"""
    from backend.modules.market_data.infrastructure.calendar_adapter import CNCalendarAdapter

    return CNCalendarAdapter()


async def _install_metrics(app: FastAPI, container: ApiContainer):
    """指标采集与 event loop 解耦：后台任务经线程池定期刷新快照缓存，
    scrape 时 collector 只读内存缓存（不在事件循环上做同步 DB 查询）。"""
    from sqlalchemy import text

    from backend.api.routers.analysis_events import active_sse_connections
    from backend.bootstrap.observability import register_platform_collector

    services = app.state.analysis_services
    cache = {"task_counts": {}, "queue_depth": 0, "sse": 0}

    def _collect_snapshot() -> dict:
        with container.sync_session_factory() as session:
            rows = session.execute(
                text("SELECT status, count(*) FROM analysis_tasks GROUP BY status")
            ).all()
            queue_depth = session.execute(
                text("SELECT count(*) FROM task_outbox WHERE status IN ('PENDING', 'DISPATCHING')")
            ).scalar_one()
        return {
            "task_counts": {status: count for status, count in rows},
            "queue_depth": int(queue_depth),
            "sse": active_sse_connections(),
        }

    async def _refresh_loop() -> None:
        await asyncio.sleep(5)  # 首轮延迟：避开测试环境模块装配期
        while True:
            try:
                snapshot = await services.run(_collect_snapshot)
                cache.clear()
                cache.update(snapshot)
            except Exception:  # noqa: BLE001 - PG 不可达时保留上次快照，不影响 /metrics
                logger.warning("指标快照刷新失败（保留上次快照）")
            await asyncio.sleep(15)

    refresh_task = asyncio.create_task(_refresh_loop())
    collector = register_platform_collector()
    collector.set_source(lambda: (cache["task_counts"], cache["queue_depth"], cache["sse"]))
    return collector, cache, refresh_task


def _build_event_study_service(settings: Settings):
    """唯一 API 进程创建一个独立 EventStudyExecutor（禁止 asyncio.to_thread/默认 executor）。"""
    from backend.modules.event_study.application.service import EventStudyService
    from backend.modules.event_study.infrastructure.bounded_executor import BoundedEventStudyExecutor
    from backend.modules.event_study.infrastructure.event_study_adapter import EventStudyAdapter
    from backend.modules.event_study.infrastructure.readers import EventStudyAssetReader

    executor = BoundedEventStudyExecutor(
        max_workers=settings.event_study.max_workers,
        max_queue=settings.event_study.max_queue,
    )
    return EventStudyService(
        adapter=EventStudyAdapter(),
        executor=executor,
        asset_reader=EventStudyAssetReader(),
        timeout_seconds=settings.event_study.timeout_seconds,
    )


def _build_event_study_review_service():
    """审核服务复用 API 分析服务线程池（低频管理流，不新建独立 executor）。"""
    from backend.modules.event_study.application.review_service import EventStudyReviewService
    from backend.modules.event_study.infrastructure.review_adapter import EventStudyReviewAdapter

    return EventStudyReviewService(adapter=EventStudyReviewAdapter())


def _ensure_windows_selector_loop() -> None:
    """Windows ProactorEventLoop 不支持 psycopg async（'ProactorEventLoop' InterfaceError）。

    在应用装配前切换到 SelectorEventLoop（uvicorn 官方 Windows 建议）；
    幂等：已有 Selector 策略时不重复设置。
    """
    if sys.platform == "win32" and not isinstance(
        asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
    ):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_app(settings: Settings | None = None) -> FastAPI:
    _ensure_windows_selector_loop()
    settings = settings or Settings()
    settings.validate_for_process("api")

    app = FastAPI(title="Liveprofit API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(agents.router)
    app.include_router(analysis_tasks.router)
    app.include_router(execution_logs.router)
    app.include_router(graph_topology.router)
    app.include_router(analysis_dashboard.router)
    app.include_router(analysis_events.router)
    app.include_router(reports.router)
    app.include_router(event_studies.router)
    app.include_router(event_study_review.router)
    app.include_router(macro_information.router)
    app.include_router(watchlists.router)
    app.include_router(portfolios.router)
    app.include_router(market_assets.router)
    app.include_router(market_data.router)
    register_exception_handlers(app)

    @app.middleware("http")
    async def trace_context_middleware(request: Request, call_next):
        # 请求进入即建立 trace context；响应回写 X-Trace-ID 便于排障关联
        trace_id = request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID") or new_trace_id()
        set_trace_id(trace_id)
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        API_REQUESTS_TOTAL.labels(method=request.method, route=request.url.path, status=response.status_code).inc()
        return response

    return app
