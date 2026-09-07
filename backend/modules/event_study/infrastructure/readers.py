"""事件研究资产读取与宏观信息读模型（同步；经 API 线程池执行）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import sessionmaker

from backend.modules.event_study.infrastructure.models import MacroInformation


class EventStudyAssetReader:
    """读取既有事件研究 assets 表（不动其结构/语义）。"""

    def list_assets(self) -> list[tuple[str, str, str]]:
        from AI.eventStudy.db.connection import get_connection

        conn = get_connection()
        try:
            return conn.execute(
                "SELECT ticker, name, market FROM assets ORDER BY asset_id"
            ).fetchall()
        finally:
            conn.close()


class MacroInformationReader:
    """宏观信息读模型：仅 review_status=APPROVED 的投影，按 occurred_at DESC。"""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def list_approved(
        self,
        *,
        limit: int,
        before: tuple[datetime | None, uuid.UUID] | None = None,
        market: str | None = None,
        topic: str | None = None,
    ) -> list[MacroInformation]:
        with self._session_factory() as session:
            stmt = select(MacroInformation).where(MacroInformation.review_status == "APPROVED")
            if market is not None:
                stmt = stmt.where(MacroInformation.market_tags.contains([market]))
            if topic is not None:
                stmt = stmt.where(MacroInformation.macro_topic == topic)
            if before is not None:
                cursor_occurred, cursor_id = before
                if cursor_occurred is None:
                    stmt = stmt.where(MacroInformation.occurred_at.is_(None), MacroInformation.id < cursor_id)
                else:
                    # DESC + nulls last：后续页 = 更早的非空 occurred_at 或空 occurred_at
                    stmt = stmt.where(
                        or_(
                            MacroInformation.occurred_at.is_(None),
                            MacroInformation.occurred_at < cursor_occurred,
                            and_(MacroInformation.occurred_at == cursor_occurred, MacroInformation.id < cursor_id),
                        )
                    )
            stmt = stmt.order_by(
                MacroInformation.occurred_at.desc().nulls_last(), MacroInformation.id.desc()
            ).limit(limit)
            return list(session.execute(stmt).scalars())
