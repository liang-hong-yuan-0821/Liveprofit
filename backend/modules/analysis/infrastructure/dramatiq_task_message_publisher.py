"""TaskMessagePublisherPort 的 Dramatiq 实现（§3.1.4）。

投递最小消息（task_id、attempt_no，单Agent重跑另带 rerun_from）；
Broker 不保存业务真相。延迟导入 actor 避免进程启动时的循环依赖。
"""

from __future__ import annotations


class DramatiqTaskMessagePublisher:
    def publish(self, payload: dict) -> None:
        from backend.workers.analysis_actor import analysis_task_actor

        # dramatiq（pyproject 下限 >=1.17，实测 2.2.1）Broker.enqueue 只收 Message
        # 对象；正确入口是 actor.send()（支持 kwargs）
        analysis_task_actor.send(
            payload["task_id"],
            payload["attempt_no"],
            rerun_from=payload.get("rerun_from"),
        )
