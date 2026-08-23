"""
板块层 — 板块轮动预测分析师
基于三块数据（Tushare 打板题材轮动矩阵 + 东财概念逐日涨幅 TOP20 +
全市场连板梯队情绪数据），识别强势主线、新晋热点和退潮板块，
判断情绪周期位置，给出明日题材板块轮动预测。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format

logger = logging.getLogger(__name__)

# ── System Prompt ─────────────────────────────────────────────

_ROTATION_SYSTEM_PROMPT = """\
你是一位专注 A 股题材板块轮动预测的分析师，
基于逐日的题材板块热度排名 + 涨停家数 + 连板高度数据、东财概念板块逐日涨幅排名、
以及全市场连板梯队与情绪数据，
识别强势主线、新晋热点和退潮板块，给出对明日的板块轮动预测。

分析日期：{current_date}

背景上下文（来自板块新闻+技术分析）：
{sector_context}

## 已获取的数据

### 题材板块逐日轮动矩阵
{rotation_matrix}

### 东财概念逐日涨幅 TOP20 矩阵（近10日）
{concept_top}

### 全市场连板梯队与情绪数据（近20日）
{ladder}

### 行业基本面（近10日行业涨跌排名 + 近5日行业资金流向）
{industry}

## 分析框架

### 第一步：主线判定（二选一结论）
- **明确主线**：连续多天上榜 + 涨停家数走扩 + 连板高度上升；主线板块之间
  存在产业链关联（如 AI 的上中下游联动）→ 按主线梯队框架输出明日预测
- **轮动期（无明确主线）**：上榜板块分散、无连续上榜主线、涨停家数均值低、
  多板块竞争且连板高度压制 → 必须输出「轮动期战术分组」（见第六步）
- 交叉验证：东财概念逐日涨幅 TOP20 矩阵中上榜持续性强的概念
  （参考"跨日上榜统计"）与 THS 轮动矩阵的主线方向是否一致？
  （沿用跨分类体系规则：仅方向性参考，不做一一对应）

### 第二步：新热点评估
- 有没有新晋上榜的板块（新晋异动）？
- 新热点的涨停家数和连板高度是否有扩散效应（从单个龙头扩散到多只跟风）？
- TOP N 之外有没有蓄势上升的板块（排名持续爬升）？
- 概念涨幅 TOP20 解读规则：换手率极低的迷你概念涨幅虚高、参考价值低，
  不单独作为主线证据。

### 第三步：退潮风险
- 哪些板块掉出榜单（退潮）？
- 掉出榜单的板块此前是"龙头独立行情"还是"普涨式轮动"？
  - 龙头独立行情退潮：高位股补跌风险极大，需重点警示
  - 普涨式轮动退潮：可能只是暂时回调，关注是否重新上榜

### 第四步：强度验证
- 强势主线板块的强度是否可持续？（涨停家数≥当日P70 + 连板≥P50）
- 有没有"涨停家数高但连板高度低"的伪主线（普涨无龙头，难以持续）？

### 第五步：情绪周期判定
- 基于连板梯队数据判断当前市场情绪周期位置（冰点/修复/高潮/退潮）：
  - 涨停总数趋势：持续萎缩=冰点/退潮，持续扩张=修复/高潮
  - 跌停家数：激增=恐慌/退潮，极少=情绪健康
  - 最高板高度与晋级率：高度抬升+晋级率上升=赚钱效应扩散；高度压制+晋级率骤降=退潮
  - 炸板率：低位=封板质量好；持续高位=情绪不稳
- 梯队为全市场混合口径（含 20cm/ST），情绪周期判定优先看主板 10cm 结构。
- 情绪周期结论必须用于"大盘适配度"和明日预测置信度：退潮/冰点期题材炒作适配度低。

### 第六步：轮动期战术分组（仅当主线判定为轮动期时输出）
当主线判定为轮动期（无明确主线）时，输出两组战术分组，每组 2-5 个板块，
每个板块必须包含：板块名（THS 题材分类）、入选逻辑（引用具体数据证据，
如"连续 3 日上榜但涨停家数未走扩"）、操作提示（追高/低吸的具体条件）、
风险、置信度（高/中/低 + 一句话依据）：
- **适合追高组**：涨停家数走扩中、连板高度抬升、连续上榜但未形成唯一主线、
  行业资金仍在流入（行业基本面块参考）
- **适合低吸组**：趋势未破坏的回调（曾上榜、排名回落但未掉出视线）、
  行业主力资金未撤离（行业资金流参考）、蓄势上升（TOP N 外排名持续爬升）、
  情绪周期冰点/修复期的低位板块
主线判定为明确主线时，**必须不输出分组**（避免与主线梯队预测混淆），
在报告对应章节写"主线明确，不输出分组"。

## 预测输出要求

基于以上分析，输出明日最可能上涨的 3-5 个题材板块，每个预测必须包含：
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
- [ ] 情绪周期是否支持新题材启动？（退潮期/冰点期新题材存活率低）

## 跨分类体系注意事项

⚠️ **重要**：本节"题材板块逐日轮动矩阵"来自同花顺（THS）题材板块分类，
"东财概念逐日涨幅 TOP20 矩阵"来自东方财富（EM）概念板块，
与板块新闻/技术分析报告使用的 EM 概念板块也是**两套独立的分类体系**。
板块名称、数量、颗粒度均不同。

规则：
1. 板块轮动预测**独立使用 THS 分类**输出，不做跨体系名称对齐
2. 在解读"背景上下文"中的 EM 板块结论时，仅做**方向性参考**（如"科技方向整体偏强"），
   **不要**尝试将 EM 板块名称和 THS 板块名称做一一对应
3. 如果需要对同一方向做交叉验证（如 EM 的"ChatGPT概念"和 THS 的"ChatGPT"），
   在输出中**同时注明两个分类体系的名称**，并标注"跨体系比对，内涵可能不完全一致"
4. 禁止自行推断"EM 的 X = THS 的 Y"——这会引入不可靠的对应关系
"""


def create_sector_rotation_analyst(llm, toolkit):

    # 各数据块的标签（门控判定/占位原因展示共用）
    _BLOCK_LABELS = (
        ("rotation", "轮动矩阵"),
        ("concept_top", "概念涨幅"),
        ("ladder", "连板梯队"),
        ("industry", "行业基本面"),
    )

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块轮动预测] 开始分析 @ {current_date}")

        # 组装上游板块层上下文
        sector_ctx = _build_sector_context(state)

        count = state.get("rotation_tool_call_count", 0)

        # 各块 dataflow 调用（一次性拉取，不走工具循环）；
        # 单块异常视为该块不可用，原因串记异常信息，不传播导致 node 崩溃
        results = {}
        calls = {
            "rotation": lambda: dataflow.get_concept_rotation_ranking(days=10, top_n=10),
            "concept_top": lambda: dataflow.get_concept_daily_top_gains(days=10, top_n=20),
            "ladder": lambda: dataflow.get_limit_up_ladder(days=20),
            # 行业基本面 = 行业涨跌排名 + 资金流（拼接后首块 "#" 开头即整块可用；
            # 两函数同 provider 家族实际同可用/同不可用，首块不可用而次块可用的
            # 组合实际不会发生，不做特殊处理）
            "industry": lambda: "\n\n".join([
                dataflow.get_industry_sector_performance(days=10),
                dataflow.get_sector_fund_flow(days=5),
            ]),
        }
        for key, fn in calls.items():
            try:
                results[key] = fn()
            except Exception as e:
                logger.warning(f"[板块轮动预测] {key} 数据调用异常: {e}")
                results[key] = f"获取数据异常: {e}"

        # 可用性判定契约：各块正常输出均以 "#" 开头；
        # 所有不可用/异常返回串（含空串/None）一律不以 "#" 开头
        def _block_content(key: str) -> str:
            v = results.get(key)
            if v and str(v).startswith("#"):
                return str(v)
            reason = v if v else "返回为空"
            return f"(数据不可用：{reason})"

        if not any(str(results[k]).startswith("#") for k, _ in _BLOCK_LABELS):
            # 各块全不可用 → 占位短路（行为同现状），原因按块分行拼接
            reasons = "\n".join(
                f"- {display}：{results[key]}"
                for key, display in _BLOCK_LABELS
            )
            placeholder = f"(数据不可用，跳过板块轮动预测。原因：\n{reasons})"
            logger.info("[板块轮动预测] 数据块均不可用，跳过")
            return {
                "messages": [],
                "rotation_prediction_report": placeholder,
                "rotation_top_picks": placeholder,
                "rotation_tool_call_count": count + 1,
            }

        output_format = load_output_format("sector", "sector_rotation_analyst")

        # 热力图生成（与主线判定无关；数据/日志目录不可用时静默跳过，异常不阻塞分析）
        try:
            from AI.utils.llm_callbacks import _run
            from AI.sectorAgents import charts
            charts.generate_sector_heatmaps(getattr(_run, "log_dir", None))
        except Exception as e:
            logger.warning(f"[板块轮动预测] 热力图生成失败（不阻塞分析）: {e}")

        prompt = ChatPromptTemplate.from_messages([
            ("system", _ROTATION_SYSTEM_PROMPT + "\n## 输出格式（结论前置）\n\n" + output_format + "\n"),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            current_date=current_date,
            sector_context=sector_ctx,
            rotation_matrix=_block_content("rotation"),
            concept_top=_block_content("concept_top"),
            ladder=_block_content("ladder"),
            industry=_block_content("industry"),
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
