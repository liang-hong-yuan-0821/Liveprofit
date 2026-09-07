"""EventStudyService（§3.1.5：有界准入 → run_in_executor → wait_for(shield) 超时映射）。"""

from __future__ import annotations

import asyncio
import logging

from backend.modules.event_study.application.contracts import (
    EventStudyAssetDTO,
    EventStudyPredictionCommand,
    PredictionDTO,
)
from backend.modules.event_study.application.errors import (
    EventStudyBusyError,
    EventStudyInternalError,
    EventStudyTimeoutError,
)

logger = logging.getLogger(__name__)


class EventStudyService:
    def __init__(
        self,
        *,
        adapter,
        executor,
        asset_reader,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._adapter = adapter
        self._executor = executor
        self._asset_reader = asset_reader
        self._timeout_seconds = timeout_seconds

    async def predict(self, command: EventStudyPredictionCommand) -> PredictionDTO:
        # 1) 不等待准入：饱和立即 503（不提交任何 psycopg 工作）
        if not self._executor.try_admit():
            raise EventStudyBusyError("事件研究请求繁忙，请稍后重试")
        # 2) 独立线程池执行同步算法；超时映射 504（不提前释放名额）
        future = self._executor.submit(lambda: self._adapter.predict(command))
        async_future = asyncio.wrap_future(future)
        try:
            result = await asyncio.wait_for(asyncio.shield(async_future), timeout=self._timeout_seconds)
        except TimeoutError:
            raise EventStudyTimeoutError("本次预测等待超时，请稍后重试") from None
        except Exception as exc:
            logger.warning("事件研究预测失败：%s", exc)
            raise EventStudyInternalError("事件研究内部错误，请稍后重试") from exc
        return PredictionDTO(
            prediction=result.get("prediction") or {},
            template_stats=result.get("template_stats") or {},
            supplement_events=result.get("supplement_events") or [],
            note=result.get("note"),
        )

    def list_assets_sync(self) -> list[EventStudyAssetDTO]:
        """资产清单来自事件研究既有 assets 表（下拉选项不硬编码）。

        同步读取：由 Router 经 API 分析服务线程池执行，禁止使用默认 executor。
        """
        rows = self._asset_reader.list_assets()
        return [EventStudyAssetDTO(ticker=r[0], name=r[1], market=r[2]) for r in rows]
