"""事件研究审核防腐适配层（复用 AI 侧 review_dao 全部函数，零改动）。

- 同步阻塞：只能经 API 分析服务线程池执行（EventStudyReviewService 由 Router 经
  analysis_services.run() 调用）。
- PG 连接复用 AI.eventStudy.db.connection.get_connection()（EventStudyAdapter 先例）。
- Redis 走 AI 侧 collectors.config 全局单例——刻意与草稿生产者（collector/daily_job）
  同配置，保证「审核读到 == 采集写出」。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EventStudyReviewAdapter:
    def open_connection(self):
        from AI.eventStudy.db.connection import get_connection

        return get_connection()

    def redis_available(self) -> bool:
        from AI.eventStudy.review import review_dao

        return review_dao.is_redis_available()

    def list_pending_events(self) -> list[dict]:
        from AI.eventStudy.review import review_dao

        return review_dao.get_pending_events()

    def fetch_latest_events(self) -> list[dict]:
        from AI.eventStudy.collectors import event_crawler

        return event_crawler.fetch_events_from_crawler()

    def save_pending_events(self, events: list[dict], conn) -> list[int]:
        from AI.eventStudy.collectors import event_crawler

        return event_crawler.save_pending_events(events, conn=conn)

    def try_acquire_refresh_lock(self, key: str, token: str, ttl_ms: int) -> bool:
        from AI.eventStudy.collectors.config import get_redis_client

        return bool(get_redis_client().set(key, token, nx=True, px=ttl_ms))

    def release_refresh_lock(self, key: str, token: str) -> None:
        from AI.eventStudy.collectors.config import get_redis_client

        try:
            # Lua 比对删除：仅锁值仍为本方 token 时释放，防 TTL 过期后被后继请求占用时误删
            get_redis_client().eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1, key, token,
            )
        except Exception as e:
            logger.warning(f"拉取锁释放失败: {e}")

    def approve(self, conn, draft_id: int, fields: dict, operator: str) -> int:
        from AI.eventStudy.review import review_dao

        return review_dao.approve_event(conn, draft_id, fields, operator)

    def validate_scope(self, conn, fields: dict) -> str | None:
        """作用域/引用提交前前置校验（透传）：合法返回 None，失败返回错误信息。

        AI 侧 EventScopeValidationError（"行级失败"语义）与存在性数据源异常
        都在 AI 侧翻译为错误信息，异常类型不跨防腐边界；服务层据此映射
        REVIEW_ROW_FAILED，不得映射 REVIEW_DRAFT_NOT_FOUND。
        """
        from AI.eventStudy.review import review_dao

        return review_dao.check_scope_fields(fields, conn)

    def ignore(self, conn, draft_id: int, operator: str) -> int:
        from AI.eventStudy.review import review_dao

        return review_dao.ignore_event(conn, draft_id, operator)

    def compute_windows(self, conn, event_id: int) -> dict:
        """执行影响窗口计算并返回草稿 dict（含各窗口 error 状态）。"""
        from AI.eventStudy.processing.event_study import compute_all_windows

        return compute_all_windows(conn, event_id)

    def prelabel(self, drafts: list[dict]) -> int:
        from AI.eventStudy.review import ai_prelabel

        return ai_prelabel.prelabel_events(drafts)

    def list_impact_drafts(self) -> list[dict]:
        from AI.eventStudy.review import review_dao

        return review_dao.get_impact_drafts()

    def event_title(self, conn, event_id: int) -> str:
        from AI.eventStudy.review import review_dao

        return review_dao.get_event_title(conn, event_id)

    def confirm_impacts(self, conn, event_id: int, tickers: list, operator: str) -> int:
        from AI.eventStudy.review import review_dao

        return review_dao.confirm_impacts(conn, event_id, tickers, operator)
