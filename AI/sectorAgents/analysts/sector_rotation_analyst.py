"""
板块层 — 板块轮动预测分析师
基于三块数据（Tushare 打板题材轮动矩阵 + 东财概念逐日涨幅 TOP20 +
全市场连板梯队情绪数据），识别强势主线、新晋热点和退潮板块，
判断情绪周期位置，给出明日题材板块轮动预测。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.utils.prompts import DEFAULT_PROMPTS, system_message
from AI.templates import load_output_format

logger = logging.getLogger(__name__)


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
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["sector:Sector Rotation Analyst"].replace(
                        "{output_format}", output_format
                    ),
                ),
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
