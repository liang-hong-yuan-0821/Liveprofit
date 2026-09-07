"""平台稳定入口（console scripts）：

- liveprofit-api       等价于 uvicorn backend.main:create_app --factory
- liveprofit-worker    等价于 dramatiq backend.workers.analysis_actor --processes 1 --threads 1（T4 接入）
- liveprofit-dispatcher 等价于 python -m backend.workers.dispatcher（T4 接入）

local 环境强制 loopback；容器内（LIVEPROFIT_ENV=container）允许 0.0.0.0 监听。
"""

from __future__ import annotations

import argparse
import sys

from backend.bootstrap.observability import init_logging
from backend.bootstrap.settings import Settings, SettingsValidationError


def _build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=prog, description=description)


def api_main(argv: list[str] | None = None) -> int:
    """uvicorn backend.main:create_app --factory（host/port 由 Settings 决定）。"""
    parser = _build_parser("liveprofit-api", "Liveprofit API（FastAPI），仅 REST/SSE，不执行 AI 图")
    parser.parse_args(argv)
    try:
        settings = Settings()
        settings.validate_for_process("api")
    except SettingsValidationError as exc:
        print(f"liveprofit-api 配置校验失败：{exc}", file=sys.stderr)
        return 2
    init_logging(settings.core.log_level)
    # Windows：uvicorn 在加载工厂前已创建事件循环——必须在 run 之前切换 Selector 策略
    # （Proactor 不支持 psycopg async；create_app 内的幂等设置对 uvicorn 场景太晚）
    from backend.main import _ensure_windows_selector_loop

    _ensure_windows_selector_loop()
    import uvicorn

    uvicorn.run(
        "backend.main:create_app",
        factory=True,
        host=settings.api.host,
        port=settings.api.port,
        workers=settings.api.workers,
        # Windows 上 uvicorn 的 loop="auto" 会强制安装 Proactor 策略并覆盖上面的 Selector；
        # loop="none" 跳过 uvicorn 的 loop 安装，保留我们的 SelectorEventLoopPolicy
        loop="none",
        log_level=settings.core.log_level.lower(),
    )
    return 0


def worker_main(argv: list[str] | None = None) -> int:
    """Dramatiq 分析 Worker：进程内 Worker 模型（单进程，consumer 线程同进程运行）。

    不使用 dramatiq CLI 的 WorkerProcess（Windows 下 spawn 子进程经 pickle 传递
    RedisBroker 会丢失认证信息，consumer 报 HELLO 认证错误——见 CLAUDE.md 踩坑）。
    """
    import logging
    import signal

    parser = _build_parser("liveprofit-worker", "Liveprofit 分析 Worker（Dramatiq）")
    parser.parse_args(argv)
    try:
        settings = Settings()
        settings.validate_for_process("worker")
    except SettingsValidationError as exc:
        print(f"liveprofit-worker 配置校验失败：{exc}", file=sys.stderr)
        return 2
    init_logging(settings.core.log_level)
    logger = logging.getLogger("backend.cli")

    import time

    import dramatiq

    from backend.workers.broker import configure_broker
    from backend.workers.wiring import configure_worker

    configure_broker(settings.core.resolved_redis_url())
    configure_worker(settings, artifact_builder=None)  # 默认注入 build_artifact_from_state（wiring）
    from backend.workers.analysis_actor import analysis_task_actor  # noqa: F401 - 注册 actor

    # queues=["default"]：Worker 构造时启动对应 consumer 线程（否则 start() 后无事可做立即退出）
    worker = dramatiq.Worker(
        dramatiq.get_broker(),
        queues=["default"],
        worker_threads=settings.worker.dramatiq_threads,
        worker_timeout=1000,
    )
    running = True

    def _stop(signum, frame):  # noqa: ARG001
        nonlocal running
        logger.info("收到信号 %s，停止 Worker", signum)
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info(
        "liveprofit-worker 启动：进程内 Worker（lease=%ss heartbeat=%ss）",
        settings.worker.lease_ttl_seconds, settings.worker.heartbeat_interval_seconds,
    )
    worker.start()
    while running:  # 主线程保活（dramatiq CLI 同款模式）
        time.sleep(1)
    worker.stop()
    logger.info("liveprofit-worker 已退出")
    return 0


def dispatcher_main(argv: list[str] | None = None) -> int:
    """Outbox Dispatcher 常驻进程（backend.workers.dispatcher:main）。"""
    parser = _build_parser("liveprofit-dispatcher", "Liveprofit Outbox Dispatcher（发布/恢复唯一调度者）")
    parser.parse_args(argv)
    try:
        settings = Settings()
        settings.validate_for_process("dispatcher")
    except SettingsValidationError as exc:
        print(f"liveprofit-dispatcher 配置校验失败：{exc}", file=sys.stderr)
        return 2

    from backend.workers.dispatcher import main as dispatcher_run

    return dispatcher_run()
