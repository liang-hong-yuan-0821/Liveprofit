"""build_real_initial_state 平台日志目录注入 + resolve_execution_logs_root 单测（无网络）。"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.bootstrap.settings import (
    PROJECT_ROOT,
    CoreSettings,
    resolve_execution_logs_root,
)
from backend.modules.analysis.application.contracts import ClaimedTask
from backend.modules.analysis.domain.enums import TaskType
from backend.modules.analysis.infrastructure.real_graph_factory import (
    build_real_initial_state,
)


def _task(**kwargs) -> ClaimedTask:
    defaults = dict(
        task_id=uuid.uuid4(),
        attempt_no=2,
        lease_token="lease-token",
        task_type=TaskType.SINGLE_STOCK,
        ticker="000001.SZ",
        selected_layers=("market", "sector", "stock"),
        effective_trade_date=None,
        request_params={},
    )
    defaults.update(kwargs)
    return ClaimedTask(**defaults)


class TestBuildRealInitialStateInjection:
    def test_injects_platform_log_dir_when_root_given(self):
        root = Path("D:/data/execution_logs")
        task = _task()
        state = build_real_initial_state(task, execution_logs_root=root)
        assert state["platform_log_dir"] == str(
            root / "tasks" / str(task.task_id) / str(task.attempt_no))

    def test_no_injection_when_root_is_none(self):
        """fake/测试路径不传 root → 不注入，内核回退 logs/{时间戳}。"""
        state = build_real_initial_state(_task())
        assert "platform_log_dir" not in state

    def test_existing_metadata_keys_preserved(self):
        """task_id/attempt_no/selected_layers 等既有 key 与注入并存。"""
        task = _task()
        state = build_real_initial_state(task, execution_logs_root=Path("logs"))
        assert state["task_id"] == str(task.task_id)
        assert state["attempt_no"] == task.attempt_no
        assert state["selected_layers"] == ["market", "sector", "stock"]
        assert state["company_of_interest"] == "000001.SZ"


class TestResolveExecutionLogsRoot:
    def test_relative_path_resolves_against_project_root(self):
        """相对路径按 PROJECT_ROOT（仓库根）解析，而非进程 CWD。"""
        core = CoreSettings(execution_logs_root=Path("logs"))
        assert resolve_execution_logs_root(core) == PROJECT_ROOT / "logs"

    def test_absolute_path_kept(self):
        abs_dir = Path("D:/data/execution_logs")
        core = CoreSettings(execution_logs_root=abs_dir)
        assert resolve_execution_logs_root(core) == abs_dir

    def test_two_processes_resolve_identically(self):
        """worker（写）与 API（读）两进程各自构造 settings，解析结果必须一致。"""
        worker_root = resolve_execution_logs_root(CoreSettings())
        api_root = resolve_execution_logs_root(CoreSettings())
        assert worker_root == api_root == PROJECT_ROOT / "logs"


class TestRerunCheckpointInjection:
    """单Agent重跑：checkpoint_state 注入 + prompt_overrides 注入（3.4.1）。"""

    def test_injects_prompt_overrides(self):
        state = build_real_initial_state(
            _task(), prompt_overrides={"market:CN News Analyst": "自定义"})
        assert state["prompt_overrides"] == {"market:CN News Analyst": "自定义"}

    def test_no_overrides_key_when_not_passed(self):
        state = build_real_initial_state(_task())
        assert "prompt_overrides" not in state

    def test_rerun_injects_checkpoint_state_from_attempt_chain(self, tmp_path):
        """attempt 链回溯：仅 complete.json 标记目录参与；checkpoint 反序列化注入。"""
        task = _task(attempt_no=2)  # claim 后已递增，链起点 = attempt_no-1 = 1
        run_dir = tmp_path / "tasks" / str(task.task_id) / "1"
        (run_dir / "checkpoints" / "market").mkdir(parents=True)
        (run_dir / "complete.json").write_text('{"completed_at": "x"}', encoding="utf-8")
        # 目标 CN Tech 的前驱 CN News checkpoint
        (run_dir / "checkpoints" / "market" / "CN_News_Analyst.json").write_text(
            '{"saved_at": "x", "node_id": "market:CN News Analyst", "state": {"messages": []}}',
            encoding="utf-8",
        )
        state = build_real_initial_state(
            task, execution_logs_root=tmp_path, rerun_from_node_id="market:CN Tech Analyst")
        assert "checkpoint_state" in state
        assert state["checkpoint_state"]["messages"] == []

    def test_rerun_missing_checkpoint_raises_fatal(self, tmp_path):
        task = _task(attempt_no=2)
        from backend.modules.analysis.application.errors import FatalAnalysisError
        import pytest

        with pytest.raises(FatalAnalysisError) as exc_info:
            build_real_initial_state(
                task, execution_logs_root=tmp_path,
                rerun_from_node_id="market:CN Tech Analyst")
        assert exc_info.value.code == "RERUN_CHECKPOINT_MISSING"

    def test_rerun_bad_json_raises_fatal(self, tmp_path):
        task = _task(attempt_no=2)
        from backend.modules.analysis.application.errors import FatalAnalysisError
        import pytest

        run_dir = tmp_path / "tasks" / str(task.task_id) / "1"
        (run_dir / "checkpoints" / "market").mkdir(parents=True)
        (run_dir / "complete.json").write_text('{"completed_at": "x"}', encoding="utf-8")
        (run_dir / "checkpoints" / "market" / "CN_News_Analyst.json").write_text(
            '{"state": {"messages": [{"type": "human"', encoding="utf-8")
        with pytest.raises(FatalAnalysisError) as exc_info:
            build_real_initial_state(
                task, execution_logs_root=tmp_path,
                rerun_from_node_id="market:CN Tech Analyst")
        assert exc_info.value.code == "RERUN_CHECKPOINT_MISSING"

    def test_rerun_skips_incomplete_attempt_dirs(self, tmp_path):
        """失败/取消 attempt 目录（无 complete.json）不参与 resolve（环中态剔除）。"""
        task = _task(attempt_no=3)  # 链 = [2, 1]
        # attempt 2：无 complete.json 但伪造前驱 cp（环中态陷阱）
        bad_dir = tmp_path / "tasks" / str(task.task_id) / "2"
        (bad_dir / "checkpoints" / "market").mkdir(parents=True)
        (bad_dir / "checkpoints" / "market" / "CN_News_Analyst.json").write_text(
            '{"saved_at": "x", "node_id": "market:CN News Analyst", "state": {"messages": []}}',
            encoding="utf-8",
        )
        # attempt 1：完整目录 + 正确 cp
        run_dir = tmp_path / "tasks" / str(task.task_id) / "1"
        (run_dir / "checkpoints" / "market").mkdir(parents=True)
        (run_dir / "complete.json").write_text('{"completed_at": "x"}', encoding="utf-8")
        (run_dir / "checkpoints" / "market" / "CN_News_Analyst.json").write_text(
            '{"saved_at": "x", "node_id": "market:CN News Analyst", "state": {"messages": [{"type": "human", "data": {"content": "ok"}}]}}',
            encoding="utf-8",
        )
        state = build_real_initial_state(
            task, execution_logs_root=tmp_path, rerun_from_node_id="market:CN Tech Analyst")
        assert len(state["checkpoint_state"]["messages"]) == 1  # 命中 attempt 1 的完整目录
