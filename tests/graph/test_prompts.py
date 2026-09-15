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
    return {
        n.id for n in topology.nodes
        if n.id not in ("screening:Screening", "market:Risk Gate")
    }


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
    # langchain partial 数据占位符保留（T6 特征层单槽位）
    assert "{calendar_features}" in rendered


def test_cn_news_default_text_features():
    """抽关键节点默认文案特征（T6 特征层单槽位 + 固定枚举，字节级）。"""
    text = DEFAULT_PROMPTS["market:CN News Analyst"]
    assert text.startswith("你是一位专注 A 股市场微观结构的分析师")
    assert "## 已获取的数据（特征层：资金日历，窗口为交易日口径）\n{calendar_features}" in text
    assert "三时间级别分析框架（每级给出压力等级 + 1-5 分压力评分 + 驱动项 + 关键日期）" in text
    assert "每级压力等级只能是：高 / 中 / 低 / 信息不足" in text
    assert "未纳入（无数据）" in text
    assert text.endswith("输出格式（结论前置，含结构化 JSON 结论块；键名与枚举原样保留）：\n{output_format}")


def test_market_prompt_rules_features():
    """T6 市场提示词关键规则特征（事件证据来源/技术隔离/覆盖不足降级/数据不足模板）。"""
    intl_events = DEFAULT_PROMPTS["market:International Event Extraction Analyst"]
    assert "{event_study_prefetch}" in intl_events
    assert "【事件描述摘要】" in intl_events

    intl_news = DEFAULT_PROMPTS["market:International News Analyst"]
    assert "{international_events}" in intl_news
    assert "{global_risk_features}" in intl_news
    assert "本节点唯一的事件类证据来源" in intl_news
    assert "缺失超过一半时，风险偏好必须为“信息不足”、systemic_risk 必须为 insufficient" in intl_news
    assert "{international_event_report}" not in intl_news  # 全文报告仅展示，不进提示词

    cn_tech = DEFAULT_PROMPTS["market:CN Tech Analyst"]
    assert "{technical_features}" in cn_tech
    assert "适合 / 谨慎 / 回避 / 信息不足" in cn_tech

    for node_id, marker in (
        ("market:US News Analyst", "美国市场数据不足，未纳入 A 股风险判断"),
        ("market:US Tech Analyst", "美国市场数据不足，未纳入 A 股风险判断"),
        ("market:KR News Analyst", "韩国市场状态：未纳入判断"),
        ("market:KR Tech Analyst", "韩国市场状态：未纳入判断"),
    ):
        assert marker in DEFAULT_PROMPTS[node_id], node_id


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


# 输出格式模板含 JSON 结论块的市场节点（`{output_format}` 必须走 partial 值注入：
# 文本替换会把模板内容并入 langchain 模板再解析 → JSON 单花括号 KeyError）
_JSON_FORMAT_NODES = (
    ("market:CN Tech Analyst", "cn_tech_analyst", '"short_term"'),
    ("market:CN News Analyst", "cn_news_analyst", '"key_dates"'),
    ("market:International News Analyst", "international_news_analyst", '"risk_appetite"'),
)


@pytest.mark.parametrize("node_id,template_name,marker", _JSON_FORMAT_NODES)
def test_json_output_format_partial_injection_renders(node_id, template_name, marker):
    """模拟运行时组装：锚点 + partial 注入输出格式（含 JSON）→ 渲染不抛且 JSON 原样。

    锁契约：模板/注册表内保留单花括号（不写 `{{`），显示与运行一致。
    """
    import re

    from langchain_core.messages import HumanMessage
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    from AI.templates import load_output_format

    output_format = load_output_format("market", template_name)
    assert "{{" not in output_format, "模板不得用双花括号转义（partial 值不参与解析）"
    text = DEFAULT_PROMPTS[node_id].replace("{date_line}", "")
    assert "{output_format}" in text

    placeholders = set(re.findall(r"\{(\w+)\}", text)) - {"output_format"}
    prompt = ChatPromptTemplate.from_messages([
        ("system", text),
        MessagesPlaceholder(variable_name="messages"),
    ])
    filled = prompt.partial(**{name: "X" for name in placeholders},
                            output_format=output_format)
    messages = filled.format_messages(messages=[HumanMessage(content="hi")])
    content = messages[0].content
    assert marker in content, f"{node_id}: 输出格式未注入"
    assert "{output_format}" not in content
