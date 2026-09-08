"""事件研究审核防腐适配层（复用 AI 侧 review_dao 全部函数，零改动）。

- 同步阻塞：只能经 API 分析服务线程池执行（EventStudyReviewService 由 Router 经
  analysis_services.run() 调用）。
- PG 连接复用 AI.eventStudy.db.connection.get_connection()（EventStudyAdapter 先例）。
- Redis 走 AI 侧 collectors.config 全局单例——刻意与草稿生产者（collector/daily_job）
  同配置，保证「审核读到 == 采集写出」。
"""

from __future__ import annotations


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

    def approve(self, conn, draft_id: int, fields: dict, operator: str) -> int:
        from AI.eventStudy.review import review_dao

        return review_dao.approve_event(conn, draft_id, fields, operator)

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
