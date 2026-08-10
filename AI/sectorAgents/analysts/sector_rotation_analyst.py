"""
板块层 — 板块轮动预测分析师
基于 Tushare 打板专题数据（limit_cpt_list）构造逐日轮动矩阵，
识别强势主线、新晋热点和退潮板块，给出明日题材板块轮动预测。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────

_ROTATION_SYSTEM_PROMPT = """\
你是一位专注 A 股题材板块轮动预测的分析师，
基于逐日的题材板块热度排名 + 涨停家数 + 连板高度数据，
识别强势主线、新晋热点和退潮板块，给出对明日的板块轮动预测。

分析日期：{current_date}

背景上下文（来自板块新闻+技术分析）：
{sector_context}

## 已获取的数据

### 题材板块逐日轮动矩阵
{rotation_matrix}

## 分析框架

### 第一步：主线识别
- 哪些板块连续多天上榜（持续主线）？
- 它们的涨停家数在走扩还是走弱？
- 连板高度在上升还是下降？
- 主线板块之间是否存在产业链关联（如 AI 的上中下游联动）？

### 第二步：新热点评估
- 有没有新晋上榜的板块（新晋异动）？
- 新热点的涨停家数和连板高度是否有扩散效应（从单个龙头扩散到多只跟风）？
- TOP N 之外有没有蓄势上升的板块（排名持续爬升）？

### 第三步：退潮风险
- 哪些板块掉出榜单（退潮）？
- 掉出榜单的板块此前是"龙头独立行情"还是"普涨式轮动"？
  - 龙头独立行情退潮：高位股补跌风险极大，需重点警示
  - 普涨式轮动退潮：可能只是暂时回调，关注是否重新上榜

### 第四步：强度验证
- 强势主线板块的强度是否可持续？（涨停家数≥当日P70 + 连板≥P50）
- 有没有"涨停家数高但连板高度低"的伪主线（普涨无龙头，难以持续）？

## 预测输出要求

基于以上四步分析，输出明日最可能上涨的 3-5 个题材板块，每个预测必须包含：
- 板块名称（THS 题材分类）
- 预测逻辑链（基于逐日矩阵的具体证据，不是泛泛而谈）
- 置信度（高/中/低 + 一句话依据）
- 风险提示（可能推翻预测的因素）

### 置信度参考标准
- **高**：持续主线 + 涨停家数走扩 + 连板高度上升 + 强度分级为"强势主线"
- **中**：新晋异动 + 涨停家数≥当日中位数 + 强度分级为"普涨式轮动"或"龙头独立行情"
- **低**：波动板块 / 仅靠蓄势上升信号 / 数据天数不足

### 风险提示清单（必须逐一检查）
- [ ] 大盘环境是否支持题材炒作？（参考市场层 macro 环境）
- [ ] 主线板块是否已到加速末期？（连板高度极高 + 涨停家数开始下降 = 见顶信号）
- [ ] 新晋异动是否有龙头带动？（缺龙头的新热点容易一日游）
- [ ] 退潮板块中的"龙头独立行情"类型是否有高位补跌风险？
- [ ] 是否存在板块间资金跷跷板效应？（A 板块强 → B 板块被抽血）

## 跨分类体系注意事项

⚠️ **重要**：本节数据来源为同花顺（THS）题材板块分类，
与板块新闻/技术分析报告使用的东方财富（EM）概念板块是**两套独立的分类体系**。
板块名称、数量、颗粒度均不同。

规则：
1. 板块轮动预测**独立使用 THS 分类**输出，不做跨体系名称对齐
2. 在解读"背景上下文"中的 EM 板块结论时，仅做**方向性参考**（如"科技方向整体偏强"），
   **不要**尝试将 EM 板块名称和 THS 板块名称做一一对应
3. 如果需要对同一方向做交叉验证（如 EM 的"ChatGPT概念"和 THS 的"ChatGPT"），
   在输出中**同时注明两个分类体系的名称**，并标注"跨体系比对，内涵可能不完全一致"
4. 禁止自行推断"EM 的 X = THS 的 Y"——这会引入不可靠的对应关系

## 输出格式（结论前置）

# 板块轮动预测报告

## 〇、轮动预测速览（结论块 — 结构化短名单）
```
预测日期: {current_date}
明日主线预测: <板块名> | 置信度:<高/中/低> | 逻辑:<一句话>
明日新晋热点预测: <板块名> | 置信度:<高/中/低> | 逻辑:<一句话>
退潮预警: <板块名> | 原因:<一句话> | 风险等级:<高/中/低>
蓄势关注: <板块名> | 排名变化趋势 | 关注逻辑:<一句话>
大盘适配度: <有利/中性/不利> | <一句话理由>
```

## 一、逐日轮动矩阵回顾
（简述过去5天的轮动格局，不重复贴原始数据表——原始表已在 rotation_matrix 中）

## 二、主线持续性分析
（持续主线板块的详细分析：涨停家数/连板高度趋势、产业链关联、持续性判断）

## 三、新热点与蓄势板块
（新晋异动板块的评估 + TOP N 外蓄势上升板块的关注逻辑）

## 四、退潮预警
（掉出榜单板块的退潮原因 + 龙头独立行情的高位股补跌风险警示）

## 五、明日预测 TOP3-5
（每个预测：板块名 + 置信度 + 逻辑链 + 风险提示 + 推翻条件）

## 六、跨体系交叉验证（如有）
（如 EM 体系下的板块新闻/技术结论与 THS 轮动矩阵方向一致/背离，标注差异）

请使用中文。

⚠️ **数据可用性规则**：如果上方的"题材板块逐日轮动矩阵"数据不可用（包含"Tushare 未连接"、"数据不可用"、"不支持"、"无打板专题数据"等提示），不要编造预测。此时仅输出：
```
(数据不可用，跳过板块轮动预测。原因见上方轮动矩阵数据。)
```
然后立即结束，不输出任何分析框架或预测结论。
"""


def create_sector_rotation_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块轮动预测] 开始分析 @ {current_date}")

        # 组装上游板块层上下文
        sector_ctx = _build_sector_context(state)

        count = state.get("rotation_tool_call_count", 0)

        # 直接调用 dataflows 获取逐日轮动矩阵（一次性拉取，不走工具循环）
        rotation_matrix = dataflow.get_concept_rotation_ranking(days=5, top_n=10)

        # 数据不可用时短路：不调 LLM，直接写降级占位
        _unavailable_prefixes = (
            "Tushare 未连接", "数据不可用", "当前数据源不支持",
            "近",  # "近 N 个交易日无打板专题数据"
        )
        if rotation_matrix.startswith(_unavailable_prefixes):
            logger.info(f"[板块轮动预测] 数据不可用，跳过: {rotation_matrix[:80]}")
            placeholder = f"(数据不可用，跳过板块轮动预测。原因：{rotation_matrix})"
            return {
                "messages": [],
                "rotation_prediction_report": placeholder,
                "rotation_top_picks": placeholder,
                "rotation_tool_call_count": count + 1,
            }

        prompt = ChatPromptTemplate.from_messages([
            ("system", _ROTATION_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            current_date=current_date,
            sector_context=sector_ctx,
            rotation_matrix=rotation_matrix,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        top_picks = _extract_rotation_prediction(report)

        logger.info(f"[板块轮动预测] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "rotation_prediction_report": report,
            "rotation_top_picks": top_picks,
            "rotation_tool_call_count": count + 1,
        }

    return node


def _build_sector_context(state) -> str:
    """组装上游板块层上下文摘要，供轮动预测参考。

    优先消费结构化短字段（market_regime），
    板块层完整报告取前 600 字摘要。
    """
    parts = []

    # 优先：大盘环境结构化短字段（完整，不截断）
    market_regime = state.get("market_regime", "")
    if market_regime and len(market_regime) > 10:
        parts.append(f"## 大盘环境\n{market_regime}")

    # 板块新闻报告（截取前 600 字）
    sector_news = state.get("sector_news_report", "")
    if sector_news and len(sector_news) > 20:
        parts.append(f"## 板块新闻摘要\n{sector_news[:600]}")

    # 板块技术报告（截取前 600 字）
    sector_tech = state.get("sector_tech_report", "")
    if sector_tech and len(sector_tech) > 20:
        parts.append(f"## 板块技术摘要\n{sector_tech[:600]}")

    return "\n\n".join(parts) if parts else "（板块层数据暂不可用）"


def _extract_rotation_prediction(report: str) -> str:
    """从完整报告中提取轮动预测速览结构化文本"""
    if not report or len(report) < 50:
        return report or ""

    import re
    # 尝试提取 ``` 代码块内容
    code_block = re.search(r'```\s*\n(.*?)\n```', report, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()[:1200]

    # 尝试提取 ## 〇 段落
    section = re.search(
        r'##\s*〇[、，\s]*轮动预测速览.*?\n(.*?)(?=\n##\s|\Z)',
        report, re.DOTALL
    )
    if section:
        return section.group(1).strip()[:1200]

    # 兜底：返回报告前 1200 字
    return report[:1200]
