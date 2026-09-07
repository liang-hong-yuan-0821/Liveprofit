"""事件研究专属有界执行器（§3.1.5）。

- ThreadPoolExecutor 自身队列无界：必须始终由**非等待**准入计数（max_workers + max_queue）
  包裹，运行+排队总量受限；准入计数只在 API event loop 线程读写（单线程无需锁）。
- 超时请求不得提前释放名额：只有 future 实际完成（经 call_soon_threadsafe 回到
  event loop）才释放名额，物理线程不可取消时也不会绕过容量上限。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class BoundedEventStudyExecutor:
    def __init__(self, *, max_workers: int, max_queue: int) -> None:
        if max_workers <= 0 or max_queue <= 0:
            raise ValueError("max_workers/max_queue 均须为正")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="event-study")
        self._limit = max_workers + max_queue
        self._active = 0
        self._accepting = threading.Event()
        self._accepting.set()

    @property
    def active_count(self) -> int:
        """当前占用名额（event loop 线程读取；测试观测用）。"""
        return self._active

    def try_admit(self) -> bool:
        """不等待准入：满则返回 False（Router 映射 503 EVENT_STUDY_BUSY）。"""
        if not self._accepting.is_set() or self._active >= self._limit:
            return False
        self._active += 1
        return True

    def submit(self, fn: Callable[[], Any]) -> Future:
        """已准入后提交；完成回调经 event loop 释放名额（超时不提前释放）。"""
        future = self._executor.submit(fn)
        loop = asyncio.get_running_loop()

        def _release(_: Future) -> None:
            loop.call_soon_threadsafe(self._decrement)

        future.add_done_callback(_release)
        return future

    def _decrement(self) -> None:
        self._active = max(0, self._active - 1)

    def stop_accepting(self) -> None:
        self._accepting.clear()

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
