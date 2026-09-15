"""
事件研究 REST API（方案 3.8，FastAPI）

- POST /predict：接收事件文本 + 资产代码 → 预测（模板匹配 + 向量检索融合）；
  可选 as_of（防前视截止时点，缺省 = 事件自身时间）与 event_scope（同作用域过滤），
  响应携带 sample_metadata（样本数/覆盖状态/污染提示，方案第三章）
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


def _resolve_as_of(conn, req: PredictRequest):
    """as_of 缺省语义（方案第三章）：显式给定优先；未给时取 event_id 对应事件的
    announced_at（预测时点 = 事件自身时间，防前视）；两者均缺省 → None（不做时间过滤，
    兼容旧调用）。

    event_id 存在但库中无对应事件时抛 404：宁可显式失败，也不静默丢弃时间过滤
    （否则历史样本会混入 announced_at 晚于事件自身时间的数据）。
    """
    if req.as_of and str(req.as_of).strip():
        return req.as_of
    if req.event_id is None:
        return None
    row = conn.execute(
        "SELECT announced_at FROM events WHERE event_id = %s", (req.event_id,)
    ).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(
            status_code=404,
            detail=f"event_id={req.event_id} 无对应事件，无法确定 as_of"
                   f"（可显式传 as_of）",
        )
    announced = row[0]
    return announced.isoformat() if hasattr(announced, "isoformat") else str(announced)


@app.post("/predict")
def predict(req: PredictRequest):
    """对新事件输出历史影响统计及预测（3.8.1）。

    响应包含：预测（方向/幅度/置信度）+ 模板统计（平均CAR/胜率/样本数）
    + 补充相似事件列表（相似度/权重，模板与补充分开报告）
    + 样本元数据 sample_metadata（样本数 / 覆盖状态 history_match_status /
    污染提示 / 截止时点 as_of，供预取器降级）。

    防前视与同作用域（方案第三章）：
    - `as_of` 缺省 = 事件自身时间（提供 `event_id` 时按其 `announced_at`）；
      仅检索 announced_at <= as_of 的已审核样本，边界包含
    - `event_scope` 缺省 = 不限定作用域；仅传 event_scope 的 sector/stock 请求
      视为「无目标引用」的空路由（零样本，不跨层借用市场样本）——带目标引用的
      路由由预取器进程内调用 `predictor.predict_impact(..., scope_refs=...)` 完成
    """
    if req.window_type not in WINDOW_TYPES:
        raise HTTPException(status_code=422, detail=f"window_type 非法（可选: {WINDOW_TYPES}）")
    conn = _open_conn()
    try:
        as_of = _resolve_as_of(conn, req)
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
            as_of=as_of,
            event_scope=req.event_scope,
            # 排除事件自身（评审 m14 残留）：event_id 提供时 as_of 由该事件推导
            # （回测场景），不排除会把「事件自身已实现的影响」当作历史样本
            exclude_event_id=req.event_id,
        )
        return result
    except HTTPException:
        raise
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
