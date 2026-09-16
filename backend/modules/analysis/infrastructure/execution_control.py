"""ExecutionControl（plan 4.2.1）：量化执行的取消/失租控制协议。

协议固定为 raise_if_inactive() / register_process(popen) / unregister_process(popen) /
terminate_all()。heartbeat/fencing 更新和取消轮询由 Worker 负责更新回调（is_cancelled /
is_fencing_active），本对象只消费结果，不自行猜测租约状态。
失活时：停止提交新任务、终止全部已登记进程并等待回收、丢弃未持久化批次结果。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import uuid
from typing import Callable

logger = logging.getLogger(__name__)


class ExecutionInactiveError(Exception):
    """取消或失租：执行必须立即停止并丢弃未持久化结果。"""


class ExecutionControl:
    def __init__(
        self,
        task_id: uuid.UUID,
        lease_token: str,
        is_cancelled: Callable[[], bool],
        is_fencing_active: Callable[[], bool],
    ) -> None:
        self._task_id = task_id
        self._lease_token = lease_token
        self._is_cancelled = is_cancelled
        self._is_fencing_active = is_fencing_active
        self._processes: set[subprocess.Popen] = set()

    @property
    def task_id(self) -> uuid.UUID:
        return self._task_id

    @property
    def lease_token(self) -> str:
        return self._lease_token

    def raise_if_inactive(self) -> None:
        if self._is_cancelled():
            raise ExecutionInactiveError(f"任务已取消：{self._task_id}")
        if self._is_fencing_active():
            raise ExecutionInactiveError(f"租约失活（fencing）：{self._task_id}")

    def register_process(self, popen: subprocess.Popen) -> None:
        self._processes.add(popen)

    def unregister_process(self, popen: subprocess.Popen) -> None:
        self._processes.discard(popen)

    def terminate_all(self) -> None:
        """终止全部已登记子进程并等待回收：POSIX 以新 session/process group 启动并 killpg，
        Windows 终止直接子进程并记录 PROCESS_GROUP_TERMINATION_DEGRADED。"""
        for popen in list(self._processes):
            try:
                if popen.poll() is None:
                    if os.name == "posix":
                        try:
                            os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            popen.kill()
                    else:
                        logger.info("PROCESS_GROUP_TERMINATION_DEGRADED: Windows 仅终止直接子进程 pid=%s", popen.pid)
                        popen.kill()
                popen.wait(timeout=5)
            except Exception as exc:  # noqa: BLE001
                logger.warning("终止子进程失败 pid=%s: %s", getattr(popen, "pid", None), exc)
            finally:
                self._processes.discard(popen)
