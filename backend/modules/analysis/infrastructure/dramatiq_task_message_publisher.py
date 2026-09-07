"""TaskMessagePublisherPort 的 Dramatiq 实现（§3.1.4）。

只投递最小消息（task_id、attempt_no）；Broker 不保存业务真相。
延迟导入 actor 避免进程启动时的循环依赖。
"""

from __future__ import annotations


class DramatiqTaskMessagePublisher:
    def publish(self, payload: dict) -> None:
        from backend.workers.analysis_actor import analysis_task_actor

        # dramatiq 1.17 Broker.enqueue 只收 Message 对象；正确入口是 actor.send()
        analysis_task_actor.send(payload["task_id"], payload["attempt_no"])
