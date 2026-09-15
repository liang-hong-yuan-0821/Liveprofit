"""scan_run_status 状态推导单测（纯文件系统构造，无网络/DB）。

夹具对应 docs/requirements/archive/任务拓扑图方案.md §3.4 测试表第 2 行。
"""

from __future__ import annotations

import json

from AI.graph.topology import Topology, TopologyEdge, TopologyNode
from backend.modules.analysis.application.graph_topology import scan_run_status

_CN_NEWS = "stock:CN News Analyst"
_BULL = "stock:Bull Researcher"
_SCREENING = "screening:Screening"


def _topology() -> Topology:
    """三节点 stub：CN News Analyst（stock）、Bull Researcher（stock）、Screening。"""
    return Topology(
        nodes=(
            TopologyNode(id=_CN_NEWS, label="CN News Analyst", layer="stock", row=2, order=0),
            TopologyNode(id=_BULL, label="Bull Researcher", layer="stock", row=2, order=1),
            TopologyNode(id=_SCREENING, label="Screening", layer="screening", row=2, order=0),
        ),
        edges=(),
    )


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _node_dir(run_dir, rel, *, meta=None, res=True, dp_error=None):
    """建节点目录：meta 缺省不写（= 预测目录）；dp_error 非 None 时写一个 DP 调用目录。"""
    d = run_dir / rel
    d.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        _write_json(d / "meta.json", meta)
    if res:
        (d / "res.md").write_text("res", encoding="utf-8")
    if dp_error is not None:
        _write_json(d / "001_get_data" / "meta.json", {"name": "get_data", "error": dp_error})
    return d


def test_missing_run_dir_all_not_executed_via_empty_result(tmp_path):
    result = scan_run_status(tmp_path / "nope", _topology(), terminal=False, failed=False)
    assert result == {}


def test_no_dirs_result_empty(tmp_path):
    result = scan_run_status(tmp_path, _topology(), terminal=False, failed=False)
    assert result == {}


def test_executed_llm_dir(tmp_path):
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={"node": "CN News Analyst"})
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=False)
    assert result[_CN_NEWS]["status"] == "executed"
    assert result[_CN_NEWS]["dirs"] == ["stock/001_CN_News_Analyst"]
    assert result[_CN_NEWS]["invocation_count"] == 1
    assert _BULL not in result


def test_dp_error_marks_error_even_for_prediction_dir(tmp_path):
    # 预测目录（无节点级 meta.json）内 DP error=true → error（规则 3 对两类目录生效）
    _node_dir(tmp_path, "screening/001_Screening", dp_error=True)
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=False)
    assert result[_SCREENING]["status"] == "error"


def test_running_last_llm_dir_non_terminal(tmp_path):
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={})
    _node_dir(tmp_path, "stock/002_Bull_Researcher", meta={})
    result = scan_run_status(tmp_path, _topology(), terminal=False, failed=False)
    assert result[_CN_NEWS]["status"] == "executed"
    assert result[_BULL]["status"] == "running"


def test_seq_tie_prediction_dir_not_running(tmp_path):
    # 并列夹具：screening 预测目录（无 meta）与 stock 首个 LLM 目录（有 meta）同 seq 001
    _node_dir(tmp_path, "screening/001_Screening")
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={})
    result = scan_run_status(tmp_path, _topology(), terminal=False, failed=False)
    assert result[_SCREENING]["status"] == "executed"  # 不假 running
    assert result[_CN_NEWS]["status"] == "running"


def test_seq_tie_failed_terminal_prediction_dir_not_error(tmp_path):
    # FAILED 终态 + 首个 LLM 目录无 res.md → 只判 LLM 目录节点 error，Screening 不误伤
    _node_dir(tmp_path, "screening/001_Screening")
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={}, res=False)
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=True)
    assert result[_SCREENING]["status"] == "executed"
    assert result[_CN_NEWS]["status"] == "error"


def test_failed_terminal_last_llm_with_res_is_executed(tmp_path):
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={}, res=True)
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=True)
    assert result[_CN_NEWS]["status"] == "executed"


def test_multi_instance_invocation_count_and_sorted_dirs(tmp_path):
    # 辩论循环同节点两次调用（seq 全局递增）
    _node_dir(tmp_path, "stock/005_Bull_Researcher", meta={})
    _node_dir(tmp_path, "stock/007_Bull_Researcher", meta={})
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=False)
    assert result[_BULL]["invocation_count"] == 2
    assert result[_BULL]["dirs"] == ["stock/005_Bull_Researcher", "stock/007_Bull_Researcher"]


def test_sanitize_match_cn_news_analyst(tmp_path):
    # 目录 001_CN_News_Analyst ↔ label "CN News Analyst"（空格→下划线）
    _node_dir(tmp_path, "stock/001_CN_News_Analyst", meta={})
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=False)
    assert _CN_NEWS in result


def test_unknown_dir_ignored(tmp_path):
    _node_dir(tmp_path, "stock/003_Removed_Analyst", meta={})
    result = scan_run_status(tmp_path, _topology(), terminal=True, failed=False)
    assert result == {}
