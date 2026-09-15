"""
市场层 — Layer 0a: 国际事件提取分析师
从宏观数据中识别重大事件，检索历史案例。
拆自原 international_news_analyst，专注事件识别 + 历史案例检索。
不依赖 ticker，仅使用 trade_date。

T4 改造（方案第五章）：LLM 调用**前**执行事件研究预取（固定候选/资产/窗口，
`as_of=event_time` 防前视），预取结果作 `event_study_prefetch` Prompt 变量注入；
节点返回值新增 `international_events`（结构化事件，历史统计只引用预取结果）。
系统提示词文本规则属 T6（`AI/utils/prompts.py`），本模块只做变量组装与注入。

评审 M8：两次 LLM 调用共用同一系统提示词（第二次调用此前无系统提示词），
并删除非预取历史检索（`get_event_calendar_history`）——历史统计只有预取一条通道。
"""

import logging
import re
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
from AI.dataflows import interface as dataflow
from AI.marketAgents.analysts.international_event_prefetch import (
    build_market_confirmation, prefetch_event_study_evidence,
)
from AI.templates import load_output_format
from AI.utils.event_prefetch_core import (
    SCOPE_MARKET, STATUS_UNMATCHED, candidate_to_event, degraded_result,
    match_candidate, normalize_title, render_prefetch_block,
)
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)

# 预取异常时结构化事件的原因（不得由 LLM 补写统计）
_UNMATCHED_REASON = "未匹配到预取候选（本轮无该事件的预取历史统计）"
_NO_CONFIRMATION_NOTE = "无价格或预期差证据，不宣称市场已定价"


def create_international_event_extraction(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[国际事件提取] 开始分析 @ {current_date}")

        count = state.get("international_event_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 1. 直接调用 dataflows 函数获取宏观数据（使用纯日期）
        macro_news = dataflow.get_global_macro_news(current_date)
        central_bank = dataflow.get_central_bank_calendar(current_date)
        macro_indicators = dataflow.get_macro_indicators(current_date)
        commodity_fx = dataflow.get_commodity_fx_overview(days=10)

        # 2. 事件研究预取（LLM 前固定候选/资产/窗口；失败降级不阻塞）
        try:
            prefetch = prefetch_event_study_evidence(
                raw_news=macro_news,
                central_bank_calendar=central_bank,
                macro_indicators=macro_indicators,
                trade_date=current_date,
            )
            # 渲染纳入 try（评审 M17 残留）：渲染异常同样不得中断节点
            prefetch_block = render_prefetch_block(prefetch)
        except Exception as e:  # 预取/渲染异常兜底：仅缺历史统计，不影响事件识别
            logger.warning(f"[国际事件提取] 事件研究预取异常（跳过历史统计）: {e}")
            prefetch = degraded_result(
                event_scope=SCOPE_MARKET, trade_date=current_date,
                reason=f"预取异常: {e}",
            )
            prefetch_block = ""
        logger.info(
            f"[国际事件提取] 预取完成：状态={prefetch.get('status')} "
            f"候选={prefetch.get('candidate_count')}"
        )

        # 两次 LLM 调用各自的输出格式模板
        fmt1 = load_output_format("market", "international_event_extraction")
        fmt2 = load_output_format("market", "international_event_extraction_final")

        def _build_prompt(output_format):
            """两次 LLM 调用共用的提示词组装（同系统提示词 + 同数据块）。

            `{output_format}` 以 partial 值注入（与其余市场节点同口径；值不参与
            模板解析）；`{event_study_prefetch}` 只含预取统计块。
            """
            template = ChatPromptTemplate.from_messages([
                    system_message(
                        state.get("_current_node_id"),
                        lambda: DEFAULT_PROMPTS["market:International Event Extraction Analyst"]
                        .replace("{date_line}", date_line),
                    ),
                MessagesPlaceholder(variable_name="messages"),
            ])
            return template.partial(
                macro_news=macro_news,
                central_bank=central_bank,
                macro_indicators=macro_indicators,
                commodity_fx=commodity_fx,
                # 历史统计块（T6 在系统提示词中引用该变量；块内只有预取统计）
                event_study_prefetch=prefetch_block,
                output_format=output_format,
            )

        # 3. 第一次 LLM：识别重大事件
        prompt = _build_prompt(fmt1)
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        event_identification = result.content

        # 4. 结构化事件（历史统计只引用预取结果）
        try:
            international_events = build_international_events(
                event_identification, prefetch, current_date,
            )
        except Exception as e:  # 组装异常兜底（评审 M17 残留）：按空列表降级，不阻塞报告
            logger.warning(f"[国际事件提取] 结构化事件组装异常（按空列表降级）: {e}")
            international_events = []

        # 5. 第二次 LLM：输出最终事件提取报告
        # 系统提示词与第一次调用**同源**（评审 M8）：历史统计只能引用
        # `{event_study_prefetch}` 块的约束必须对两次调用同时生效（此前第二次
        # 调用无系统提示词）；非预取历史检索（`get_event_calendar_history`）
        # 已删除——历史统计只来自预取，无第二条检索通道。
        final_prompt = (
            "请基于以下事件识别结果，生成完整的国际事件提取报告。\n\n"
            f"## 事件识别结果\n{event_identification}\n\n"
            "历史统计只引用系统提示词中的「事件研究历史统计（预取）」块，"
            "不得另行检索、更换资产或窗口、凭常识补写数值。\n\n"
            "输出格式：\n"
            + fmt2
        )
        final_template = _build_prompt(fmt2)
        final_messages = final_template.format_messages(
            messages=state["messages"] + [result, HumanMessage(content=final_prompt)]
        )
        final_result = llm.invoke(final_messages)
        final_report = final_result.content

        logger.info(
            f"[国际事件提取] 报告完成，长度: {len(final_report)}，"
            f"结构化事件: {len(international_events)} 条"
        )
        return {
            "messages": [result] + [final_result],
            "international_event_report": final_report,
            "international_events": international_events,
            "international_event_tool_call_count": count + 1,
        }

    return node


# ==================== 结构化事件（方案第三章三字段同构 / 第五章字段） ====================

_EVENT_SECTION = re.compile(r"^#{1,6}\s*[〇一二三四五六七八九十\d]*[、.，\s]*.*(?:已识别|重大事件)")
_SEPARATOR_CELL = re.compile(r"^[:\-\s]+$")
_DIGEST_BLOCK = re.compile(r"【事件描述摘要】\s*\n?(.*?)(?=\n#|\Z)", re.DOTALL)
_BULLET_MARKER = re.compile(r"^\s*(?:[-*•·]|\d+[.、)])\s*")
_STARS = re.compile(r"[★☆]+\s*")
_CONFIDENCE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_CONFIDENCE_PLAIN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _parse_confidence(value) -> float | None:
    """置信度文本 → 0-1 浮点；无法判定返回 None（不臆造数值）。"""
    text = str(value or "").strip()
    if not text:
        return None
    percent = _CONFIDENCE_PERCENT.search(text)
    if percent:
        return max(0.0, min(1.0, float(percent.group(1)) / 100.0))
    plain = _CONFIDENCE_PLAIN.match(text)
    if plain:
        number = float(plain.group(1))
        return max(0.0, min(1.0, number if number <= 1 else number / 100.0))
    return None


def _clean_event_title(text: str) -> str:
    text = _STARS.sub("", _BULLET_MARKER.sub("", str(text or "").strip())).strip()
    book = re.match(r"^《(.+?)》", text)
    if book:
        text = book.group(1).strip()
    return text.strip(" |:：-—·")[:120]


def _parse_events_table(report: str) -> list[dict]:
    """解析「已识别的重大事件」小节内的 Markdown 表格（表头列名优先，退化按列序）。"""
    events, header, in_section = [], None, False
    for raw_line in str(report or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            in_section = bool(_EVENT_SECTION.search(line))
            header = None
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(not c or _SEPARATOR_CELL.match(c) for c in cells):
            continue
        if header is None:
            # 表头识别：同时含「事件」与列名特征词（数据行极少两者兼有）
            is_header = (
                any("事件" in c for c in cells)
                and any(("类型" in c or "方向" in c or "置信" in c or "判断" in c)
                        for c in cells)
            )
            if is_header:
                header = cells
                continue
            header = None  # 无表头（首行即数据）→ 按列序解析，并继续解析本行
        if header is not None:
            index = {name: i for i, name in enumerate(header)}

            def cell(*names, default=None):
                for name in names:
                    for key, position in index.items():
                        if name in key and position < len(cells):
                            return cells[position]
                return default

            title = _clean_event_title(cell("事件"))
            event_type = cell("类型") or None
            judgement = cell("判断") or None
            direction = cell("方向") or None
            confidence = _parse_confidence(cell("置信"))
        else:
            title = _clean_event_title(cells[0]) if cells else ""
            event_type = cells[1] if len(cells) > 1 else None
            judgement = cells[2] if len(cells) > 2 else None
            direction = cells[3] if len(cells) > 3 else None
            confidence = _parse_confidence(cells[4]) if len(cells) > 4 else None
        if len(normalize_title(title)) < 3:
            continue
        events.append({
            "title": title,
            "event_type": event_type,
            "judgement": judgement,
            "direction": direction,
            "confidence": confidence,
        })
    return events


def _parse_events_digest(report: str) -> list[dict]:
    """解析【事件描述摘要】块（每行一个事件，仅标题）。"""
    match = _DIGEST_BLOCK.search(str(report or ""))
    if not match:
        return []
    events = []
    for raw_line in match.group(1).splitlines():
        title = _clean_event_title(raw_line)
        if len(normalize_title(title)) < 3:
            continue
        events.append({
            "title": title, "event_type": None, "judgement": None,
            "direction": None, "confidence": None,
        })
    return events


def parse_identified_events(report: str) -> list[dict]:
    """事件识别报告 → 事件条目列表（容错纯函数，不依赖 LLM）。

    形态 1：「已识别的重大事件」小节内的 Markdown 表格
            （事件 | 类型 | 判断 | 影响方向 | 置信度）
    形态 2：【事件描述摘要】块（每行一个事件）
    都取不到时返回 []（按 0 条事件降级，不臆造事件）。
    """
    events = _parse_events_table(report)
    if events:
        return events
    return _parse_events_digest(report)


def build_international_events(report, prefetch=None, trade_date=None,
                               max_events: int = 5) -> list[dict]:
    """事件识别报告 + 预取结果 → 结构化市场事件（0-5 条）。

    - 事实只来自报告解析；匹配到预取候选时引用其历史统计（`historical_impact`）
    - 未匹配到候选 → `history_match_status="unmatched"`、`historical_impact=None`
      + 原因（不得由 LLM 补写统计）
    - `market_confirmation` 仅来自预取提取的实际/预期差；无证据时为 None 并记原因
    """
    prefetch = prefetch or {}
    candidates = prefetch.get("candidates") or []
    releases = prefetch.get("macro_releases") or []
    used: set = set()
    events = []
    for index, item in enumerate(parse_identified_events(report)[:max_events], 1):
        title = item.get("title") or ""
        candidate = match_candidate(title, candidates, used=used)
        if candidate is None:
            candidate = {
                "candidate_id": f"market:{index}",
                "source": "事件识别",
                "title": title,
                "event_time": None,
                "time_precision": "none",
            }
            event = candidate_to_event(
                candidate, event_scope=SCOPE_MARKET, trade_date=trade_date, fact=title,
                confidence=item.get("confidence"),
                history_match_status=STATUS_UNMATCHED, historical_impact=None,
                history_reason=_UNMATCHED_REASON,
            )
        else:
            confirmation = build_market_confirmation(title, releases) or \
                build_market_confirmation(candidate.get("title"), releases)
            event = candidate_to_event(
                candidate, event_scope=SCOPE_MARKET, trade_date=trade_date, fact=title,
                confidence=item.get("confidence"), market_confirmation=confirmation,
            )
        event["event_type"] = item.get("event_type") or event.get("event_type")
        event["judgement"] = item.get("judgement")
        event["direction"] = item.get("direction")
        if event.get("market_confirmation") is None:
            event["data_quality"]["notes"].append(_NO_CONFIRMATION_NOTE)
        events.append(event)
    return events
