"""
仓位管理层 — 核心分配逻辑（纯函数，无 LLM）

流水线收口环节：把多只个股各自独立的决策汇总成一份受总资金和
仓位规则约束的可执行交易计划。全部确定性数值计算，可回溯、可复现。

规则（LIVEPROFIT_* 环境变量，见 default_config.py / .env.example）：
- LIVEPROFIT_TOTAL_CAPITAL：总资金，未配置时整层跳过并告警
- LIVEPROFIT_MAX_POSITION_PCT：总仓位上限（默认 0.8；risk_gate=caution 时 ×0.5）
- LIVEPROFIT_MAX_SINGLE_STOCK_PCT：单票仓位上限（默认 0.1）
- LIVEPROFIT_MAX_SECTOR_PCT：同板块合计上限（默认 0.3）
- LIVEPROFIT_POSITION_SIZING_STRATEGY：equal / confidence_weighted（默认）/ kelly
"""

import logging

from AI.position.portfolio_loader import load_portfolio

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "max_position_pct": 0.8,
    "max_single_stock_pct": 0.1,
    "max_sector_pct": 0.3,
    "position_sizing_strategy": "confidence_weighted",
}

# 凯利公式仓位系数上限（防重仓）
_KELLY_MAX_FRACTION = 0.25

# A 股一手 = 100 股
_LOT_SIZE = 100


def build_position_plan(state: dict, config: dict) -> dict:
    """汇总 stock_results → final_position_plan。

    risk_gate == "block" 时转 risk 计划；未配置总资金时整层跳过。
    """
    risk_gate = state.get("risk_gate", "normal")
    if risk_gate == "block":
        return build_risk_plan(state, config)

    total_capital = config.get("total_capital") or 0
    plan_date = state.get("trade_date", "")
    if not total_capital or total_capital <= 0:
        logger.warning("[仓位管理层] 未配置 LIVEPROFIT_TOTAL_CAPITAL，整层跳过（不阻塞前序流程）")
        return {
            "plan_date": plan_date,
            "total_capital": 0,
            "risk_gate": risk_gate,
            "error": "未配置 LIVEPROFIT_TOTAL_CAPITAL，仓位管理层跳过",
            "orders": [],
            "summary": {"advice": "配置 LIVEPROFIT_TOTAL_CAPITAL 后自动生成交易计划"},
        }

    max_position_pct = float(config.get("max_position_pct", _DEFAULTS["max_position_pct"]))
    max_single_stock_pct = float(config.get("max_single_stock_pct", _DEFAULTS["max_single_stock_pct"]))
    max_sector_pct = float(config.get("max_sector_pct", _DEFAULTS["max_sector_pct"]))
    strategy = config.get("position_sizing_strategy", _DEFAULTS["position_sizing_strategy"])

    # risk_gate=caution → 整体目标仓位上限打 5 折
    risk_factor = 0.5 if risk_gate == "caution" else 1.0
    target_position_pct = round(max_position_pct * risk_factor, 4)

    # ---- 1. 过滤 action == "买入" 的候选股 ----
    stock_results = state.get("stock_results") or {}
    buy_candidates = []
    for code, res in stock_results.items():
        decision = res.get("decision_json") or {}
        if decision.get("action") != "买入":
            continue
        last_close = float(res.get("last_close") or 0)
        if last_close <= 0:
            logger.warning(f"[仓位管理层] {code} last_close 异常，跳过（无法换算股数）")
            continue
        try:
            confidence = float(decision.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        try:
            risk_score = float(decision.get("risk_score", 0.5))
        except (TypeError, ValueError):
            risk_score = 0.5
        stop_loss = decision.get("stop_loss")
        target_price = decision.get("target_price")
        buy_candidates.append({
            "code": code,
            "name": res.get("name", ""),
            "sector": res.get("sector", ""),
            "confidence": max(0.0, min(confidence, 1.0)),
            "risk_score": risk_score,
            "target_price": float(target_price) if target_price is not None else None,
            "stop_loss": float(stop_loss) if stop_loss is not None else None,
            "last_close": last_close,
        })

    # ---- 2. 排序：置信度降序，风险评分升序为次要键 ----
    buy_candidates.sort(key=lambda x: (-x["confidence"], x["risk_score"]))

    # ---- 3. 分配建议金额（sizing 策略） ----
    budget = total_capital * target_position_pct
    _allocate(buy_candidates, budget, total_capital, strategy, max_single_stock_pct)

    # ---- 4. 组合校验：同板块上限 + 总仓位上限（含已有持仓市值） ----
    holdings = load_portfolio()
    existing_value = sum(
        h["shares"] * (h["last_price"] if h["last_price"] is not None else h["cost_price"])
        for h in holdings
    )
    orders = []
    sector_value = {}
    for cand in buy_candidates:
        amount = cand["amount"]
        # 同板块合计仓位超上限 → 按排序裁剪排名靠后的候选
        sector_room = total_capital * max_sector_pct - sector_value.get(cand["sector"], 0.0)
        amount = min(amount, max(sector_room, 0.0))
        # 已有持仓市值 + 新增买入总金额超总仓位上限 → 后续候选裁剪
        total_room = total_capital * max_position_pct - existing_value - sum(
            o["amount"] for o in orders
        )
        amount = min(amount, max(total_room, 0.0))
        # 目标预算收口（kelly 策略分配阶段不使用 budget，caution 折半须在此约束）
        budget_room = budget - sum(o["amount"] for o in orders)
        amount = min(amount, max(budget_room, 0.0))
        # A 股整手换算（向下取整到 100 股）
        shares = int(amount // cand["last_close"] // _LOT_SIZE * _LOT_SIZE)
        if shares < _LOT_SIZE:
            continue
        amount = shares * cand["last_close"]
        sector_value[cand["sector"]] = sector_value.get(cand["sector"], 0.0) + amount
        orders.append({
            "code": cand["code"],
            "name": cand["name"],
            "sector": cand["sector"],
            "action": "买入",
            "amount": round(amount, 2),
            "shares": shares,
            "position_pct": round(amount / total_capital, 4),
            "confidence": cand["confidence"],
            "stop_loss": cand["stop_loss"],
            "target_price": cand["target_price"],
        })

    total_new = sum(o["amount"] for o in orders)
    existing_pct = existing_value / total_capital if total_capital > 0 else 0.0
    final_pct = (existing_value + total_new) / total_capital if total_capital > 0 else 0.0

    plan = {
        "plan_date": plan_date,
        "total_capital": total_capital,
        "target_position_pct": target_position_pct,
        "risk_gate": risk_gate,
        "sizing_strategy": strategy,
        "orders": orders,
        "summary": {
            "total_new_position_pct": round(total_new / total_capital, 4),
            "existing_position_pct": round(existing_pct, 4),
            "final_position_pct": round(final_pct, 4),
            "cash_reserve_pct": round(1 - final_pct, 4),
            "existing_market_value": round(existing_value, 2),
            "screened_out": _screened_out_note(stock_results, buy_candidates, orders),
        },
    }
    logger.info(
        f"[仓位管理层] 产出 {len(orders)} 条买入订单，"
        f"新增仓位 {plan['summary']['total_new_position_pct']:.1%}，"
        f"最终仓位 {plan['summary']['final_position_pct']:.1%}"
    )
    return plan


def _screened_out_note(stock_results: dict, buy_candidates: list, orders: list) -> str:
    """汇总被过滤/裁剪的票数说明（供人阅读 summary）"""
    total = len(stock_results)
    in_orders = len(orders)
    note = f"共 {total} 只候选，买入 {in_orders} 只"
    if len(buy_candidates) > in_orders:
        note += f"，{len(buy_candidates) - in_orders} 只因金额/板块/总仓位上限被裁剪"
    if total > 0 and len([r for r in stock_results.values() if (r.get('decision_json') or {}).get('action') != '买入']) > 0:
        note += "，其余为非买入信号或数据异常"
    return note


def build_risk_plan(state: dict, config: dict) -> dict:
    """risk_gate=block 时的风险提示计划（不生成买入订单）。"""
    risk_gate = state.get("risk_gate", "normal")
    total_capital = config.get("total_capital") or 0
    holdings = load_portfolio()
    existing_value = sum(
        h["shares"] * (h["last_price"] if h["last_price"] is not None else h["cost_price"])
        for h in holdings
    )
    existing_pct = existing_value / total_capital if total_capital > 0 else 0.0
    advice = {
        "block": "系统性风险（risk_gate=block）：建议清仓/减仓/观望，不生成买入订单",
        "normal": "候选池为空：保持观望，不生成买入订单",
    }.get(risk_gate, "风险提示：不生成买入订单")

    logger.warning(f"[仓位管理层] risk_gate={risk_gate}，输出风险提示计划")
    return {
        "plan_date": state.get("trade_date", ""),
        "total_capital": total_capital,
        "target_position_pct": 0.0,
        "risk_gate": risk_gate,
        "orders": [],
        "summary": {
            "total_new_position_pct": 0.0,
            "existing_position_pct": round(existing_pct, 4),
            "final_position_pct": round(existing_pct, 4),
            "cash_reserve_pct": round(1 - existing_pct, 4),
            "advice": advice,
        },
    }


def _allocate(candidates: list, budget: float, total_capital: float,
              strategy: str, max_single_stock_pct: float) -> None:
    """按策略为每只候选分配建议金额（就地修改 candidates 的 amount 字段）。

    - equal: 预算均分，单票上限截断
    - confidence_weighted（默认）: 置信度加权 + 单票上限截断
    - kelly: 简化凯利 f = p - (1-p)/b，赔率 b = (target-last)/(last-stop)
    """
    n = len(candidates)
    if n == 0:
        return
    cap = total_capital * max_single_stock_pct
    if strategy == "equal":
        base = min(budget / n, cap)
        for cand in candidates:
            cand["amount"] = base
    elif strategy == "kelly":
        for cand in candidates:
            cand["amount"] = min(cap, total_capital * _kelly_fraction(cand))
    else:  # confidence_weighted（默认）
        total_conf = sum(c["confidence"] for c in candidates) or 1.0
        for cand in candidates:
            weight = cand["confidence"] / total_conf
            cand["amount"] = min(cap, budget * weight)


def _kelly_fraction(cand: dict) -> float:
    """简化凯利公式：f = p - (1-p)/b，b = (target-last)/(last-stop)。

    止损/目标价缺失或非法 → 0（不参与分配）。
    """
    p = cand.get("confidence") or 0.0
    last = cand["last_close"]
    stop = cand.get("stop_loss")
    target = cand.get("target_price")
    if not stop or stop <= 0 or stop >= last:
        return 0.0
    if not target or target <= last:
        return 0.0
    b = (target - last) / (last - stop)
    if b <= 0:
        return 0.0
    f = p - (1 - p) / b
    return max(0.0, min(f, _KELLY_MAX_FRACTION))
