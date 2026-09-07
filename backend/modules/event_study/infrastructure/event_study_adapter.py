"""predict_impact() 防腐适配器（§3.1.5：复用既有算法，不复制、不改既有表结构）。

- 同步阻塞：只能在独立有界 ThreadPoolExecutor 内执行（EventStudyService 保证）。
- 连接复用 AI.eventStudy.db.connection.get_connection()（既有事件研究连接工具）。
"""

from __future__ import annotations

from backend.modules.event_study.application.contracts import EventStudyPredictionCommand


class EventStudyAdapter:
    def predict(self, command: EventStudyPredictionCommand) -> dict:
        from AI.eventStudy.db.connection import get_connection
        from AI.eventStudy.prediction.predictor import predict_impact

        conn = get_connection()
        try:
            return predict_impact(
                conn,
                new_event_text=command.event_text,
                asset_ticker=command.asset_ticker,
                window_type=command.window_type,
                event_type=command.event_type,
                event_subtype=command.event_subtype,
                event_condition=command.event_condition,
                save=command.save,
                event_id=command.event_id,
            )
        finally:
            conn.close()
