"""
单元测试：结构化清单提取 + 东财概念名单过滤（不依赖真实 LLM / 网络）

覆盖方案 3.2.3 的 5 类场景：正常清单 / 缺行 / 坏 JSON /
含申万行业名与 THS 独有概念名（过滤落选）/ 名单接口不可用。
"""

import pytest

from AI.sectorAgents.analysts.structured_list import (
    extract_sector_structured_list,
    merge_sector_structured_lists,
)

_NAMES = "白酒\n人工智能\n半导体概念\n人形机器人"


@pytest.fixture
def fake_names(monkeypatch):
    """monkeypatch dataflow.get_concept_board_names，返回给定名单文本"""
    from AI.sectorAgents.analysts import structured_list as mod

    def patch(names_text):
        monkeypatch.setattr(mod.dataflow, "get_concept_board_names", lambda: names_text)

    return patch


def _report(line):
    return (
        "## 〇、候选板块速览\n"
        "```\n"
        "主线状态: 有主线(白酒)\n"
        f"{line}\n"
        "```\n"
        "## 一、行业涨跌排名\n...\n"
    )


def test_normal_list_kept(fake_names):
    fake_names(_NAMES)
    report = _report('结构化清单: ["白酒", "人工智能"]')
    assert extract_sector_structured_list(report) == ["白酒", "人工智能"]


def test_non_em_names_dropped(fake_names):
    """申万行业名（汽车）与 THS 独有概念名（光模块）过滤落选"""
    fake_names(_NAMES)
    report = _report('结构化清单: ["白酒", "光模块", "汽车", "人工智能"]')
    assert extract_sector_structured_list(report) == ["白酒", "人工智能"]


def test_missing_line_returns_empty(fake_names):
    fake_names(_NAMES)
    report = _report("短线候选TOP3: [白酒, 逻辑, 提示, 风险, 0.8]")
    assert extract_sector_structured_list(report) == []


def test_bad_json_returns_empty(fake_names):
    fake_names(_NAMES)
    report = _report('结构化清单: ["白酒", "未闭合')
    assert extract_sector_structured_list(report) == []


def test_non_array_json_returns_empty(fake_names):
    fake_names(_NAMES)
    report = _report('结构化清单: "白酒"')
    assert extract_sector_structured_list(report) == []


def test_names_api_failure_skips_filter(fake_names):
    """名单接口不可用 → 跳过过滤，候选名原样返回（选股层兜底）"""
    fake_names("未获取到东财概念板块名单。")
    report = _report('结构化清单: ["白酒", "光模块"]')
    assert extract_sector_structured_list(report) == ["白酒", "光模块"]


def test_duplicates_removed(fake_names):
    fake_names(_NAMES)
    report = _report('结构化清单: ["白酒", "白酒", "人工智能"]')
    assert extract_sector_structured_list(report) == ["白酒", "人工智能"]


def test_short_report_returns_empty(fake_names):
    fake_names(_NAMES)
    assert extract_sector_structured_list("") == []
    assert extract_sector_structured_list("太短") == []


def test_merge_sector_structured_lists():
    assert merge_sector_structured_lists([], ["白酒"]) == ["白酒"]
    assert merge_sector_structured_lists(["白酒"], ["白酒", "人工智能"]) == ["白酒", "人工智能"]
    assert merge_sector_structured_lists(None, []) == []
    assert merge_sector_structured_lists([], None) == []
