"""EventStudyService 有界准入/超时不提前释放名额单测（fake adapter，无真实 psycopg）。"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.modules.event_study.application.contracts import EventStudyPredictionCommand
from backend.modules.event_study.application.errors import (
    EventStudyBusyError,
    EventStudyTimeoutError,
)
from backend.modules.event_study.application.service import EventStudyService
from backend.modules.event_study.infrastructure.bounded_executor import BoundedEventStudyExecutor


class FakeAdapter:
    def __init__(self, *, result: dict | None = None, delay: float = 0.0,
                 first_delay: float | None = None, error: Exception | None = None) -> None:
        self._result = result or {"prediction": {"direction": "up"}, "template_stats": {}, "supplement_events": []}
        self._delay = delay
        self._first_delay = first_delay if first_delay is not None else delay
        self._error = error
        self.calls = 0

    def predict(self, command):
        self.calls += 1
        wait = self._first_delay if self.calls == 1 else self._delay
        if wait:
            time.sleep(wait)
        if self._error is not None:
            raise self._error
        return self._result


class FakeAssetReader:
    def list_assets(self):
        return [("000001.SH", "上证指数", "CN")]


def _command() -> EventStudyPredictionCommand:
    return EventStudyPredictionCommand(event_text="事件文本", asset_ticker="000001.SH")


def _service(adapter, *, max_workers=1, max_queue=1, timeout=30.0):
    executor = BoundedEventStudyExecutor(max_workers=max_workers, max_queue=max_queue)
    return EventStudyService(adapter=adapter, executor=executor, asset_reader=FakeAssetReader(), timeout_seconds=timeout)


def test_predict_returns_normalized_dto():
    service = _service(FakeAdapter())
    result = asyncio.run(service.predict(_command()))
    assert result.prediction["direction"] == "up"
    assert result.template_stats == {}
    assert result.note is None


def test_saturation_returns_busy_without_calling_adapter():
    slow = FakeAdapter(delay=0.3)
    service = _service(slow)  # capacity = max_workers(1) + max_queue(1) = 2

    async def scenario():
        task1 = asyncio.ensure_future(service.predict(_command()))
        await asyncio.sleep(0.02)
        task2 = asyncio.ensure_future(service.predict(_command()))
        await asyncio.sleep(0.02)
        with pytest.raises(EventStudyBusyError):
            await service.predict(_command())  # 名额已满：不调用 predictor
        results = await asyncio.gather(task1, task2)
        assert len(results) == 2

    asyncio.run(scenario())


def test_timeout_keeps_slot_until_actual_completion():
    # 单 worker + 容量 2：前两个调用都因等待真实执行而超时，名额仍被占用；
    # 第三个调用被拒（503 语义）——证明超时没有提前释放名额。
    adapter = FakeAdapter(delay=0.3)
    service = _service(adapter, timeout=0.05)

    async def scenario():
        with pytest.raises(EventStudyTimeoutError):
            await service.predict(_command())
        task2 = asyncio.ensure_future(service.predict(_command()))
        await asyncio.sleep(0.02)
        with pytest.raises(EventStudyBusyError):
            await service.predict(_command())  # 名额仍被两个未完成调用占用
        with pytest.raises(EventStudyTimeoutError):
            await task2
        # 实际完成后名额释放（轮询至 0，不早于真实结束）
        deadline = time.monotonic() + 2
        while service._executor.active_count > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert service._executor.active_count == 0
        assert adapter.calls == 2

    asyncio.run(scenario())


def test_list_assets_sync():
    service = _service(FakeAdapter())
    assets = service.list_assets_sync()
    assert assets[0].ticker == "000001.SH"
    assert assets[0].name == "上证指数"


def test_stop_accepting_rejects_new_admissions():
    executor = BoundedEventStudyExecutor(max_workers=1, max_queue=1)
    assert executor.try_admit() is True
    executor.stop_accepting()
    assert executor.try_admit() is False  # shutdown 停止接收新请求


def test_executor_rejects_non_positive_config():
    with pytest.raises(ValueError):
        BoundedEventStudyExecutor(max_workers=0, max_queue=1)
    with pytest.raises(ValueError):
        BoundedEventStudyExecutor(max_workers=1, max_queue=0)
