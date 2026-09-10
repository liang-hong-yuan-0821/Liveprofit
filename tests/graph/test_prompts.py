"""AI/utils/prompts.py 提示词注册中心单测（单Agent重跑与提示词编辑方案 3.1）。

覆盖：DEFAULT_PROMPTS 键集合与拓扑 LLM 节点集合一致；覆盖解析三态；
set_overrides 隔离；锚点替换机制；关键节点默认文案字节级特征。
不依赖真实 LLM（无需 -k 排除）。
"""

import pytest

from AI.utils.prompts import (
    DEFAULT_PROMPTS,
    get_override,
    get_system_prompt,
    set_overrides,
)
from AI.graph.topology import build_topology


@pytest.fixture(autouse=True)
def _reset_overrides():
    set_overrides(None)
    yield
    set_overrides(None)


def _topology_llm_node_ids() -> set[str]:
    """全形态拓扑中除纯代码节点外的全部主节点 id。"""
    topology = build_topology(("market", "sector", "screening", "stock"))
    return {n.id for n in topology.nodes if n.id != "screening:Screening"}


def test_default_prompt_keys_cover_topology_llm_nodes():
    """拓扑 LLM 主节点 ⊆ DEFAULT_PROMPTS 键（防新增/改名节点漂移）。

    DEFAULT_PROMPTS 另有 market 的 US/KR 4 个被折叠进 International News
    工具循环的分析师条目（覆盖机制对全部 23 个节点生效，v1 编辑入口仅拓扑主节点）。
    """
    llm_ids = _topology_llm_node_ids()
    assert llm_ids <= set(DEFAULT_PROMPTS.keys()), (
        f"键漂移：拓扑主节点缺少 {llm_ids - set(DEFAULT_PROMPTS)}"
    )
    assert len(DEFAULT_PROMPTS) == 23, f"预期 23 条，实际 {len(DEFAULT_PROMPTS)}"


def test_override_present_returns_verbatim_and_skips_default():
    """覆盖存在且非空 → 原样返回，default callable 不被求值（惰性）。"""
    calls = []
    set_overrides({"market:CN News Analyst": "自定义提示词 {not_an_anchor}"})
    result = get_system_prompt(
        "market:CN News Analyst",
        lambda: calls.append(1) or "默认",
    )
    assert result == "自定义提示词 {not_an_anchor}"
    assert calls == []  # 覆盖场景 default 未求值


def test_override_empty_string_treated_as_absent():
    """覆盖为空串 → 视为无覆盖，回落 default。"""
    set_overrides({"market:CN News Analyst": ""})
    assert get_override("market:CN News Analyst") is None
    assert get_system_prompt("market:CN News Analyst", "默认") == "默认"


def test_no_override_returns_default_str_and_callable():
    assert get_system_prompt(None, "默认串") == "默认串"
    assert get_system_prompt("market:CN News Analyst", lambda: "惰性默认") == "惰性默认"


def test_set_overrides_replaces_whole_registry():
    """set_overrides 整表替换；None 清空。"""
    set_overrides({"a": "1", "b": "2"})
    assert get_override("a") == "1"
    set_overrides({"a": "3"})
    assert get_override("b") is None
    set_overrides(None)
    assert get_override("a") is None


def test_anchor_replace_restores_full_prompt():
    """{date_line}/{output_format} 锚点替换后不再残留锚点，数据占位符保留。"""
    template = DEFAULT_PROMPTS["market:CN News Analyst"]
    assert "{date_line}" in template
    assert "{output_format}" in template
    rendered = template.replace("{date_line}", "分析日期：2026-09-08\n").replace(
        "{output_format}", "【输出格式】"
    )
    assert "{date_line}" not in rendered
    assert "{output_format}" not in rendered
    # langchain partial 数据占位符保留
    for placeholder in ("{ipo_calendar}", "{share_unlock}", "{futures_expiry}", "{margin_balance}"):
        assert placeholder in rendered


def test_cn_news_default_text_features():
    """抽关键节点默认文案特征（与迁移前内联字符串一致，字节级）。"""
    text = DEFAULT_PROMPTS["market:CN News Analyst"]
    assert text.startswith("你是一位专注 A 股市场微观结构的分析师")
    assert "三时间级别分析框架：\n\n短线日历（未来 1-5 交易日）：" in text
    assert "每个级别输出：事件密度（高/中/低）+ 资金面压力评分（1-5）" in text
    assert text.endswith("输出格式（结论前置）：\n{output_format}")


def test_anchor_shape_consistent():
    """锚点形态两类：ChatPromptTemplate 类必含 {output_format} 锚点（{date_line} 按需）；
    f-string 类（Bull/Bear/Managers/Trader/risk_mgmt）两锚点均无（模板原文）。"""
    for node_id, text in DEFAULT_PROMPTS.items():
        has_dl, has_of = "{date_line}" in text, "{output_format}" in text
        assert has_of or not has_dl, (
            f"{node_id}: 锚点形态异常 date_line={has_dl} output_format={has_of}"
        )


def test_system_message_override_returns_static_system_message():
    """system_message 覆盖分支：静态 SystemMessage、花括号原样（元组形式会 KeyError）。

    回归：第 2 轮 Code Review blocker——15 个 A 类工厂曾二次包裹 ("system", ...)，
    覆盖命中时 SystemMessage 被当模板抛 ValueError/KeyError。本测试锁契约。
    """
    from langchain_core.messages import SystemMessage
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    from AI.utils.prompts import system_message

    set_overrides({"market:CN News Analyst": '覆盖含 {花括号} 与 {"json": 1}'})
    entry = system_message("market:CN News Analyst", lambda: "默认 {data}")
    assert isinstance(entry, SystemMessage)
    # from_messages + format_messages 不抛错，花括号原样保留
    prompt = ChatPromptTemplate.from_messages([
        entry,
        MessagesPlaceholder(variable_name="messages"),
    ])
    from langchain_core.messages import HumanMessage

    messages = prompt.format_messages(messages=[HumanMessage(content="hi")])
    assert messages[0].content == '覆盖含 {花括号} 与 {"json": 1}'


def test_system_message_default_returns_template_tuple():
    """system_message 默认分支：("system", 模板) 元组，占位符可被 partial 填充。"""
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.messages import HumanMessage

    from AI.utils.prompts import system_message

    set_overrides(None)
    entry = system_message("market:CN News Analyst", lambda: "默认 {data}")
    assert entry == ("system", "默认 {data}")
    prompt = ChatPromptTemplate.from_messages([
        entry,
        MessagesPlaceholder(variable_name="messages"),
    ])
    messages = prompt.partial(data="注入值").format_messages(
        messages=[HumanMessage(content="hi")])
    assert messages[0].content == "默认 注入值"
