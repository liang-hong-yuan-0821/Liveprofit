"""Dramatiq 分析任务 actor（§3.1.4）。

- Actor 参数只传可序列化标识（task_id、attempt_no），禁止传 ORM/Service 实例。
- 领取失败（重复/过期消息/已终态）安全退出；业务异常已被 fail_or_retry 处理后
  Actor 正常返回（max_retries=0：禁止叠加 Dramatiq 业务重试）。
"""

from __future__ import annotations

import logging
import uuid

import dramatiq

from backend.workers.analysis_executor import AnalysisExecutor
from backend.workers.wiring import configure_worker, worker_bundle_factory, worker_id

logger = logging.getLogger(__name__)

_actor_settings = None
_actor_artifact_builder = None


def _ensure_configured():
    global _actor_settings
    if _actor_settings is None:
        from backend.bootstrap.settings import Settings

        settings = Settings()
        settings.validate_for_process("worker")
        configure_worker(settings, artifact_builder=_actor_artifact_builder)
        _actor_settings = settings


@dramatiq.actor(actor_name="analysis_task", max_retries=0)
def analysis_task_actor(task_id: str, attempt_no: int, rerun_from: str | None = None) -> None:
    """Dramatiq 入口：只消费消息；执行编排在 run_analysis_task。

    rerun_from：单Agent重跑起点节点 id（消息级触发源；None=全图执行）。
    """
    run_analysis_task(task_id, attempt_no, rerun_from=rerun_from)


def run_analysis_task(
    task_id: str,
    attempt_no: int,
    *,
    rerun_from: str | None = None,
    bundle_factory=None,
    executor: AnalysisExecutor | None = None,
) -> None:
    _ensure_configured()
    from backend.bootstrap.settings import Settings

    settings: Settings = _actor_settings or Settings()
    bundles = bundle_factory or worker_bundle_factory()

    task_uuid = uuid.UUID(task_id)
    with bundles.open() as bundle:
        claimed = bundle.tasks.claim_for_execution(
            task_uuid, attempt_no, worker_id=worker_id(), rerun_from=rerun_from)
    if claimed is None:
        # 重复消息、过期 attempt 或已终态：安全退出（幂等由条件领取保证）
        logger.info("领取失败（重复/过期/终态），安全退出：task=%s attempt=%s", task_id, attempt_no)
        return

    if executor is None:
        from backend.workers.wiring import get_worker_executor

        executor = get_worker_executor()

    executor.execute(claimed, rerun_from=rerun_from)
    logger.info("分析任务执行收口完成：task=%s attempt=%s", task_id, attempt_no)
