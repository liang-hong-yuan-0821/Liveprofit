"""T3 单元测试用内存假实现：UoW（分阶段事务语义）、Repository、Stream、Publisher、Clock、Calendar。

事务语义：add/conditional_update 写入 pending 覆盖层，commit 并入 committed，
rollback 丢弃 pending——可真实断言"同一事务写入/回滚"。
"""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from datetime import date, datetime, timedelta

from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.infrastructure.models import AnalysisReport, AnalysisTask, TaskOutbox


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 5, 9, 0, 0)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float = 0) -> None:
        self._now += timedelta(seconds=seconds)


class FakeCalendar:
    """可配置交易日校正：{requested: (effective, correction)}，未配置返回原日期无校正。"""

    def __init__(self, mapping: dict[date, tuple[date, str]] | None = None) -> None:
        self._mapping = mapping or {}

    def correct(self, requested: date) -> tuple[date, str | None]:
        return self._mapping.get(requested, (requested, None))


class _FakeStore:
    """单表分阶段存储：pending 覆盖 committed。"""

    def __init__(self) -> None:
        self.committed: dict[uuid.UUID, object] = {}
        self.pending: dict[uuid.UUID, object] = {}

    def merged(self) -> dict[uuid.UUID, object]:
        return {**self.committed, **self.pending}

    def get(self, key: uuid.UUID):
        if key in self.pending:
            return self.pending[key]
        return self.committed.get(key)

    def stage(self, key: uuid.UUID, record) -> None:
        self.pending[key] = record

    def commit(self) -> None:
        self.committed.update(self.pending)
        self.pending = {}

    def rollback(self) -> None:
        self.pending = {}

    def changesstaged(self, key: uuid.UUID) -> object | None:
        """复制到 pending 并返回可变更副本（已 staged 则直接返回）。"""
        if key in self.pending:
            return self.pending[key]
        source = self.committed.get(key)
        if source is None:
            return None
        staged = copy.copy(source)
        self.pending[key] = staged
        return staged


class FakeTaskRepository:
    def __init__(self, store: _FakeStore, reports_store: _FakeStore | None = None) -> None:
        self._store = store
        self._reports_store = reports_store or store

    def add(self, task: AnalysisTask) -> None:
        self._store.stage(task.id, task)

    def get(self, task_id: uuid.UUID) -> AnalysisTask | None:
        return self._store.get(task_id)

    def get_by_idempotency_key(self, key: str) -> AnalysisTask | None:
        for task in self._store.merged().values():
            if task.idempotency_key == key:
                return task
        return None

    def conditional_update(self, task_id: uuid.UUID, expect: dict, changes: dict) -> bool:
        staged = self._store.changesstaged(task_id)
        if staged is None:
            return False
        if not _matches(staged, expect):
            return False
        for key, value in changes.items():
            setattr(staged, key, value)
        return True

    def list_by_statuses(self, statuses: set[str], *, limit: int, before=None) -> list[AnalysisTask]:
        rows = [t for t in self._store.merged().values() if t.status in statuses]
        if before is not None:
            cu, cid = before
            rows = [t for t in rows if (t.updated_at, t.id) < (cu, cid)]
        rows.sort(key=lambda t: (t.updated_at, t.id), reverse=True)
        return rows[:limit]

    def list_succeeded_with_unavailable_sections(self, *, limit: int) -> list[tuple[AnalysisTask, AnalysisReport]]:
        reports = FakeReportRepository(self._reports_store)
        pairs = []
        for task in self._store.merged().values():
            if task.status != TaskStatus.SUCCEEDED.value:
                continue
            report = reports.get_latest(task.id)
            if report is not None and report.has_unavailable_sections:
                pairs.append((task, report))
        pairs.sort(key=lambda pair: (pair[0].updated_at, pair[0].id), reverse=True)
        return pairs[:limit]

    def list_recent_succeeded(self, *, limit: int) -> list[tuple[AnalysisTask, AnalysisReport]]:
        reports = FakeReportRepository(self._reports_store)
        pairs = []
        for task in self._store.merged().values():
            if task.status != TaskStatus.SUCCEEDED.value:
                continue
            report = reports.get_latest(task.id)
            if report is not None:
                pairs.append((task, report))
        pairs.sort(
            key=lambda pair: (
                pair[1].generated_at or pair[0].finished_at or pair[0].updated_at,
                pair[0].updated_at,
                pair[0].id,
            ),
            reverse=True,
        )
        return pairs[:limit]

    def find_expired_running(self, cutoff: datetime, *, limit: int = 100) -> list[AnalysisTask]:
        rows = [
            t
            for t in self._store.merged().values()
            if t.status == TaskStatus.RUNNING.value
            and t.lease_expires_at is not None
            and t.lease_expires_at < cutoff
        ]
        rows.sort(key=lambda t: t.lease_expires_at)
        return rows[:limit]

    def find_expired_cancel_requested(self, cutoff: datetime, *, limit: int = 100) -> list[AnalysisTask]:
        rows = [
            t
            for t in self._store.merged().values()
            if t.status == TaskStatus.CANCEL_REQUESTED.value
            and t.lease_expires_at is not None
            and t.lease_expires_at < cutoff
        ]
        rows.sort(key=lambda t: t.lease_expires_at)
        return rows[:limit]


class FakeTaskOutboxRepository:
    def __init__(self, store: _FakeStore) -> None:
        self._store = store

    def add(self, record: TaskOutbox) -> None:
        self._store.stage(record.id, record)

    def get(self, outbox_id: uuid.UUID) -> TaskOutbox | None:
        return self._store.get(outbox_id)

    def get_by_task_attempt(self, task_id: uuid.UUID, attempt_no: int) -> TaskOutbox | None:
        for record in self._store.merged().values():
            if record.task_id == task_id and record.attempt_no == attempt_no:
                return record
        return None

    def claim_due(self, now: datetime, *, limit: int, lease_seconds: int, lease_token: str) -> list[TaskOutbox]:
        candidates = []
        for record in self._store.merged().values():
            due = (
                record.status == "PENDING"
                and (record.next_attempt_at is None or record.next_attempt_at <= now)
            ) or (
                record.status == "DISPATCHING"
                and record.dispatch_lease_expires_at is not None
                and record.dispatch_lease_expires_at < now
            )
            if due:
                candidates.append(record)
        candidates.sort(key=lambda r: r.created_at)
        claimed = []
        for record in candidates[:limit]:
            staged = self._store.changesstaged(record.id)
            staged.status = "DISPATCHING"
            staged.dispatch_lease_token = lease_token
            staged.dispatch_lease_expires_at = now + timedelta(seconds=lease_seconds)
            staged.updated_at = now
            claimed.append(staged)
        return claimed

    def conditional_update(self, outbox_id: uuid.UUID, expect: dict, changes: dict) -> bool:
        staged = self._store.changesstaged(outbox_id)
        if staged is None:
            return False
        if not _matches(staged, expect):
            return False
        for key, value in changes.items():
            setattr(staged, key, value)
        return True

    def cancel_pending_for_task(self, task_id: uuid.UUID) -> int:
        count = 0
        for record in self._store.merged().values():
            if record.task_id == task_id and record.status in ("PENDING", "DISPATCHING"):
                staged = self._store.changesstaged(record.id)
                staged.status = "CANCELLED"
                count += 1
        return count


class FakeReportRepository:
    def __init__(self, store: _FakeStore) -> None:
        self._store = store

    def add(self, report: AnalysisReport) -> None:
        self._store.stage(report.id, report)

    def get_latest(self, task_id: uuid.UUID) -> AnalysisReport | None:
        candidates = [r for r in self._store.merged().values() if r.task_id == task_id]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.report_version)

    def next_version(self, task_id: uuid.UUID) -> int:
        latest = self.get_latest(task_id)
        return (latest.report_version + 1) if latest else 1


class FakePromptOverrideRepository:
    """Agent 提示词覆盖内存仓库（与真实 SqlAlchemyPromptOverrideRepository 同接口）。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, node_id: str):
        return self.rows.get(node_id)

    def list_as_map(self) -> dict[str, str]:
        return {k: v["prompt_text"] for k, v in self.rows.items()}

    def upsert(self, node_id: str, prompt_text: str, now) -> dict:
        row = self.rows.get(node_id)
        if row is None:
            row = {"node_id": node_id, "prompt_text": prompt_text,
                   "created_at": now, "updated_at": now}
            self.rows[node_id] = row
        else:
            row["prompt_text"] = prompt_text
            row["updated_at"] = now
        return SimpleNamespace(**row)

    def delete(self, node_id: str) -> bool:
        return self.rows.pop(node_id, None) is not None


class FakeUnitOfWork:
    """三个实体各自独立 store，保证事务边界按实体隔离（与真实表结构一致）。"""

    def __init__(self) -> None:
        self.tasks_store = _FakeStore()
        self.outbox_store = _FakeStore()
        self.reports_store = _FakeStore()
        self.tasks = FakeTaskRepository(self.tasks_store, reports_store=self.reports_store)
        self.outbox = FakeTaskOutboxRepository(self.outbox_store)
        self.reports = FakeReportRepository(self.reports_store)
        self.prompts = FakePromptOverrideRepository()
        self.commit_count = 0
        self.rollback_count = 0

    @property
    def store(self) -> _FakeStore:
        """测试常用别名：任务存储。"""
        return self.tasks_store

    def commit(self) -> None:
        for store in (self.tasks_store, self.outbox_store, self.reports_store):
            store.commit()
        self.commit_count += 1

    def rollback(self) -> None:
        for store in (self.tasks_store, self.outbox_store, self.reports_store):
            store.rollback()
        self.rollback_count += 1


class FakeStream:
    def __init__(self) -> None:
        self.events: dict[uuid.UUID, list[tuple[str, str, dict]]] = {}
        self._seq = 0
        self.fail_append = False

    def append(self, task_id: uuid.UUID, event_type: str, payload: dict) -> str:
        if self.fail_append:
            raise RuntimeError("redis down")
        self._seq += 1
        stream_id = f"{self._seq}-0"
        self.events.setdefault(task_id, []).append((stream_id, event_type, payload))
        return stream_id

    def read(self, task_id: uuid.UUID, after: str | None) -> list[tuple[str, str, dict]]:
        return self.events.get(task_id, [])

    def trim(self, task_id: uuid.UUID) -> None:
        self.events.pop(task_id, None)


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.published.append(payload)


def _matches(record, expect: dict) -> bool:
    for key, value in expect.items():
        actual = getattr(record, key)
        if isinstance(value, (set, frozenset)):
            if actual not in value:
                return False
        elif isinstance(value, tuple) and len(value) == 2 and value[0] == "<":
            if actual is None:
                return False
            try:
                if not actual < value[1]:
                    return False
            except TypeError:
                # naive/aware 混用（FakeClock 与 PG timestamptz）：naive 侧按 UTC 归一
                from datetime import timezone

                reference = value[1]
                if actual.tzinfo is None and reference.tzinfo is not None:
                    actual = actual.replace(tzinfo=timezone.utc)
                elif actual.tzinfo is not None and reference.tzinfo is None:
                    reference = reference.replace(tzinfo=timezone.utc)
                if not actual < reference:
                    return False
        elif actual != value:
            return False
    return True
