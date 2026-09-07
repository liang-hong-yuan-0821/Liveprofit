"""Dramatiq Redis Broker 配置（§3.1.4）。

Broker 不保存业务真相：消息仅含 task_id + attempt_no，状态与报告在 PostgreSQL。
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import dramatiq
from dramatiq.brokers.redis import RedisBroker

logger = logging.getLogger(__name__)

_configured = False


def configure_broker(redis_url: str) -> dramatiq.Broker:
    """幂等装配：进程启动时调用一次；Worker 与 Dispatcher 共用同一 Broker 配置。

    注意：Worker 子进程（spawn/fork）会各自执行一次——诊断日志记录 host/db/密码有无，
    便于排查子进程认证问题（HELLO 认证错误 = 子进程拿到无密码 URL）。
    """
    global _configured
    if _configured:
        return dramatiq.get_broker()
    parsed = urlsplit(redis_url)
    logger.info(
        "Dramatiq RedisBroker 配置：host=%s port=%s db=%s password=%s",
        parsed.hostname, parsed.port, parsed.path.lstrip("/") or "0",
        "已设置" if parsed.password else "缺失",
    )
    broker = RedisBroker(url=redis_url)
    dramatiq.set_broker(broker)
    _configured = True
    return broker
