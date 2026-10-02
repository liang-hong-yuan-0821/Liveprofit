# test-catalog-begin
# {
#   "purpose": "信号 cursor 分页压力测试（plan 4.3.3/4.5.4）：6,000 标的 attempt 隔离分页无漏项。",
#   "keywords": [
#     "量化策略",
#     "交易信号",
#     "signals_pagination",
#     "signals"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""信号 cursor 分页压力测试（plan 4.3.3/4.5.4）：6,000 标的 attempt 隔离分页无漏项。"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignalRepository


def _seed_task(env) -> uuid.UUID:
    task_id = uuid.uuid4()
    with env["session_factory"]() as session:
        session.execute(
            text(
                "INSERT INTO analysis_tasks (id, task_type, status, request_params, selected_layers, input_hash, attempt_no) "
                "VALUES (:id, 'MARKET_WIDE', 'SUCCEEDED', '{}'::jsonb, '[\"position\"]'::jsonb, 'h', 1)"
            ),
            {"id": task_id},
        )
        session.commit()
    return task_id


def _bulk_signals(env, task_id: uuid.UUID, attempt: int, count: int, kind: str = "BUY"):
    with env["session_factory"]() as session:
        for i in range(count):
            session.execute(
                text(
                    "INSERT INTO quant_execution_signals (task_id, attempt_no, signal_kind, ts_code, action, score, reason, "
                    "entry_price, stop_loss, take_profit) VALUES (:tid, :a, :kind, :ts, 'BUY', :score, 'r', 10, 9, 12)"
                ),
                {
                    "tid": task_id,
                    "a": attempt,
                    "kind": kind,
                    "ts": f"{i:06d}.SZ",
                    "score": 100 - (i % 100),
                },
            )
        session.commit()


def test_6000_signals_pagination_no_gaps(env):
    task_id = _seed_task(env)
    _bulk_signals(env, task_id, attempt=1, count=6000)
    _bulk_signals(env, task_id, attempt=2, count=10, kind="BUY")

    with env["session_factory"]() as session:
        repo = QuantExecutionSignalRepository(session)
        seen: set[int] = set()
        after = None
        pages = 0
        while True:
            rows = repo.page_by_kind(task_id, 1, "buy", after=after, limit=200 + 1)
            pages += 1
            has_more = len(rows) > 200
            page_rows = rows[:200]
            assert not (seen & {r.id for r in page_rows}), "分页出现重复行"
            seen.update(r.id for r in page_rows)
            if not has_more:
                break
            last = rows[-2] if has_more else rows[-1]
            after = {"score": format(last.score, "f"), "ts_code": last.ts_code, "id": last.id}
        assert len(seen) == 6000, f"分页漏项：{len(seen)}/6000"
        assert pages == 30

        # attempt 隔离：显式 attempt 2 只返回 10 行
        rows2 = repo.page_by_kind(task_id, 2, "buy", after=None, limit=100)
        assert len(rows2) == 10
