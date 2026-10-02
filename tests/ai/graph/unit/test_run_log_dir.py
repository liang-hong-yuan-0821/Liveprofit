# test-catalog-begin
# {
#   "purpose": "单元测试：resolve_run_log_dir 日志目录选择（不 invoke、不消耗 LLM）",
#   "keywords": [
#     "分析图",
#     "run_log_dir"
#   ],
#   "covers": [
#     "AI/graph/trading_graph.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""
单元测试：resolve_run_log_dir 日志目录选择（不 invoke、不消耗 LLM）

平台任务注入 platform_log_dir → 写确定性任务目录；CLI/既有测试不含该 key
→ 回退仓库根下 var/logs/{时间戳}（绝对路径，与进程 CWD 无关）。
"""

from datetime import datetime
from pathlib import Path

import pytest

from AI.graph.trading_graph import resolve_run_log_dir

# 独立基准（不引用被测模块的 _PROJECT_ROOT，防 parents[N] 深度写错时测试自洽通过）
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


class TestPlatformLogDirProvided:
    def test_relative_path(self):
        """相对路径原样返回（调用方负责 mkdir 多级目录）"""
        assert resolve_run_log_dir(
            {"platform_log_dir": "var/logs/tasks/abc/2"}
        ) == Path("var/logs/tasks/abc/2")

    def test_absolute_path(self):
        """绝对路径原样返回"""
        abs_dir = Path("D:/data/task_logs/abc/2")
        assert resolve_run_log_dir({"platform_log_dir": str(abs_dir)}) == abs_dir


class TestFallbackTimestampDir:
    FIXED_NOW = datetime(2026, 9, 6, 12, 34, 56)

    def test_missing_key(self):
        """无 platform_log_dir → var/logs/{时间戳}，now 注入时时间戳确定"""
        assert resolve_run_log_dir({}, now=self.FIXED_NOW) == (
            _PROJECT_ROOT / "var" / "logs" / "2026-09-06_123456")

    def test_empty_string(self):
        """空串按缺失处理 → 时间戳目录"""
        assert resolve_run_log_dir(
            {"platform_log_dir": ""}, now=self.FIXED_NOW
        ) == (_PROJECT_ROOT / "var" / "logs" / "2026-09-06_123456")

    def test_whitespace_only(self):
        """纯空白字符串 strip 后按缺失处理"""
        assert resolve_run_log_dir(
            {"platform_log_dir": "   "}, now=self.FIXED_NOW
        ) == (_PROJECT_ROOT / "var" / "logs" / "2026-09-06_123456")

    def test_unknown_keys_ignored(self):
        """既有 init_state 的其它 key 不影响目录选择"""
        assert resolve_run_log_dir(
            {"trade_date": "2026-09-05", "selected_layers": ["market"]},
            now=self.FIXED_NOW,
        ) == (_PROJECT_ROOT / "var" / "logs" / "2026-09-06_123456")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
