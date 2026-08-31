"""
事件研究 REST API（方案 3.8，FastAPI）

- POST /predict：接收事件文本 + 资产代码 → 预测（模板匹配 + 向量检索融合）
- GET  /health ：服务与依赖健康检查

运行：uvicorn AI.eventStudy.api.main:app --host 0.0.0.0 --port 8100
日常调用不落库（save=False 默认）；save=True 显式保存供事后追踪。

常驻调度（方案 B）：服务启动时内置 APScheduler 每天 08:30（本地时区，
EVENT_STUDY_DAILY_TIME 可改）以子进程方式触发 daily_job——运行约束：
单 worker、禁用 --reload（避免调度器重复启动），详见 scheduler_setup.md。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from AI.eventStudy.api.schemas import HealthResponse, PredictRequest
from AI.eventStudy.collectors.config import WINDOW_TYPES, is_redis_available
from AI.eventStudy.db.connection import get_connection
from AI.eventStudy.prediction import predictor
from AI.eventStudy.scheduler.app_scheduler import (
    start_daily_scheduler, stop_daily_scheduler,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """服务生命周期：启动时挂 daily_job 常驻调度器，关闭时停止。"""
    start_daily_scheduler()
    yield
    stop_daily_scheduler()


app = FastAPI(title="事件研究预测 API", version="0.1.0", lifespan=lifespan)


def _open_conn():
    try:
        return get_connection()
    except Exception as e:
        logger.error(f"PostgreSQL 连接失败: {e}")
        raise HTTPException(status_code=503, detail="数据库不可用")


@app.post("/predict")
def predict(req: PredictRequest):
    """对新事件输出历史影响统计及预测（3.8.1）。

    响应包含：预测（方向/幅度/置信度）+ 模板统计（平均CAR/胜率/样本数）
    + 补充相似事件列表（相似度/权重，模板与补充分开报告）。
    """
    if req.window_type not in WINDOW_TYPES:
        raise HTTPException(status_code=422, detail=f"window_type 非法（可选: {WINDOW_TYPES}）")
    conn = _open_conn()
    try:
        result = predictor.predict_impact(
            conn,
            new_event_text=req.event_text,
            asset_ticker=req.asset_ticker,
            window_type=req.window_type,
            event_type=req.event_type,
            event_subtype=req.event_subtype,
            event_condition=req.event_condition,
            save=req.save,
            event_id=req.event_id,
        )
        return result
    except Exception as e:
        logger.exception("预测失败")
        raise HTTPException(status_code=500, detail=f"预测失败: {e}")
    finally:
        conn.close()


@app.get("/health", response_model=HealthResponse)
def health():
    """健康检查：PostgreSQL 连通性 + Redis 草稿区可用性。"""
    pg_ok = False
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
        pg_ok = True
    except Exception:
        pg_ok = False
    return HealthResponse(
        status="ok" if pg_ok else "degraded",
        postgres=pg_ok,
        redis=is_redis_available(),
    )
