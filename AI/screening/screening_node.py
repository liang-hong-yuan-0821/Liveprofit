"""
选股层 — 图编排接入（普通函数节点，无 LLM）

`def screening_node(state) -> dict`，由顶层图在 Sector Layer 之后注册。
数据获取走 3.1 数据层接口；逐板块 try/except + 连续 3 板块失败熔断。
"""

import logging

from AI.dataflows import interface as dataflow
from AI.utils.stock_utils import StockUtils
from AI.screening.screener import (
    filter_and_score,
    parse_constituents,
    parse_ranking,
)

logger = logging.getLogger(__name__)


def run_screening(state: dict, max_constituents: int = 50, days: int = 10,
                  min_turnover: float = 50_000_000.0, max_stocks: int = 10) -> list:
    """核心筛选编排：候选板块清单 → 成分股 → 涨幅排名 → 超均值 + 流动性过滤。

    候选清单为空 / 全部板块失败 → 返回空池（安全短路，不抛异常）。
    """
    sectors = state.get("sector_shortlist_structured") or []
    if not sectors:
        logger.info("[选股层] 结构化清单为空，返回空池（等效单票模式兜底）")
        return []

    pool = []
    consecutive_failures = 0
    for sector in sectors:
        try:
            cons = parse_constituents(dataflow.get_sector_constituents(sector))
            if not cons:
                raise ValueError(f"{sector} 成分股为空或接口不可用")
            cons = cons[:max_constituents]
            name_map = {StockUtils.normalize_code(c["code"]): c["name"] for c in cons}
            rank_text = dataflow.get_stocks_performance_ranking(
                [c["code"] for c in cons], days
            )
            rows, avg = parse_ranking(rank_text)
            if not rows:
                raise ValueError(f"{sector} 涨幅排名为空或接口不可用")
            for item in filter_and_score(rows, avg, name_map, min_turnover):
                item["sector"] = sector
                pool.append(item)
            consecutive_failures = 0
            logger.info(f"[选股层] 板块 {sector} 成分股 {len(cons)} 只，入池 {len([i for i in pool if i['sector'] == sector])} 只")
        except Exception as e:
            consecutive_failures += 1
            logger.warning(f"[选股层] 板块 {sector} 处理失败: {e}（连续失败 {consecutive_failures}）")
            if consecutive_failures >= 3:
                logger.warning("[选股层] 连续 3 个板块失败，熔断中止")
                break
            continue

    # 跨板块去重：同一只票可属于多个东财概念 → 保留 vs_avg 最高的条目
    # （避免个股层循环重复 invoke、stock_results 键覆盖、重复项挤占截断名额）
    best = {}
    for item in pool:
        code = item["code"]
        if code not in best or item["vs_avg"] > best[code]["vs_avg"]:
            best[code] = item
    pool = list(best.values())

    pool.sort(key=lambda x: x["vs_avg"], reverse=True)
    if len(pool) > max_stocks:
        logger.info(f"[选股层] 候选池截断: {len(pool)} → {max_stocks}")
        pool = pool[:max_stocks]
    return pool


def make_screening_node(config: dict = None):
    """构造 screening 图节点（普通函数节点）。

    config 为空时从环境变量加载；由顶层图 partial 传入当前配置。
    """
    config = config or {}
    max_constituents = int(config.get("max_constituents", 50))
    days = int(config.get("screening_lookback_days", 10))
    min_turnover = float(config.get("screening_min_turnover", 50_000_000.0))
    max_stocks = int(config.get("max_screened_stocks", 10))

    def screening_node(state: dict) -> dict:
        pool = run_screening(
            state,
            max_constituents=max_constituents,
            days=days,
            min_turnover=min_turnover,
            max_stocks=max_stocks,
        )
        logger.info(f"[选股层] 候选池产出 {len(pool)} 只: "
                    f"{[s['name'] for s in pool]}")
        return {"candidate_stock_pool": pool}

    return screening_node
