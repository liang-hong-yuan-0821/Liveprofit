"""
单元测试：仓位管理层（mock 持仓加载，验证 5 步分配逻辑与三种 sizing 策略）

覆盖方案 3.6.3：过滤/排序/分配/组合校验/风险计划/未配置总资金跳过。
"""

import pytest

from AI.position.position_manager import (
    _allocate,
    _kelly_fraction,
    build_position_plan,
    build_risk_plan,
)

CONFIG = {
    "total_capital": 1_000_000.0,
    "max_position_pct": 0.8,
    "max_single_stock_pct": 0.1,
    "max_sector_pct": 0.3,
    "position_sizing_strategy": "confidence_weighted",
}


def _stock(code, action="买入", confidence=0.7, risk=0.5, last_close=100.0,
           sector="白酒", stop_loss=None, target_price=None):
    return {
        "name": f"股{code}",
        "sector": sector,
        "last_close": last_close,
        "final_trade_decision": "文本",
        "decision_json": {
            "action": action,
            "confidence": confidence,
            "risk_score": risk,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "reasoning": "",
        },
    }


def _state(stock_results, risk_gate="normal"):
    return {"trade_date": "2026-08-14", "risk_gate": risk_gate,
            "stock_results": stock_results}


@pytest.fixture
def no_holdings(monkeypatch):
    """mock load_portfolio 为空仓"""
    from AI.position import position_manager as mod
    monkeypatch.setattr(mod, "load_portfolio", lambda: [])


@pytest.fixture
def with_holdings(monkeypatch):
    """mock load_portfolio 返回固定持仓"""
    from AI.position import position_manager as mod

    def patch(holdings):
        monkeypatch.setattr(mod, "load_portfolio", lambda: holdings)

    return patch


# ==================== 基础流程 ====================

def test_build_plan_basic_confidence_weighted(no_holdings):
    results = {
        "600519.SH": _stock("600519", confidence=0.8),
        "000858.SZ": _stock("000858", confidence=0.4, sector="白酒"),
        "000001.SZ": _stock("000001", action="持有", confidence=0.9),
    }
    plan = build_position_plan(_state(results), CONFIG)

    assert plan["risk_gate"] == "normal"
    assert plan["target_position_pct"] == pytest.approx(0.8)
    assert len(plan["orders"]) == 2  # 持有信号被过滤
    # 置信度加权：0.8 / (0.8+0.4) * 80万 = 533333 → 单票上限 10 万截断
    order_600519 = plan["orders"][0]
    assert order_600519["code"] == "600519.SH"
    assert order_600519["amount"] == pytest.approx(100_000.0)  # 单票上限 10 万
    assert order_600519["shares"] == 1000  # 100 股整手
    assert order_600519["position_pct"] == pytest.approx(0.1)
    # 单票上限约束下合计不超预算
    assert sum(o["amount"] for o in plan["orders"]) <= 800_000.0
    # 汇总数字
    assert plan["summary"]["existing_position_pct"] == 0.0
    assert plan["summary"]["final_position_pct"] == plan["summary"]["total_new_position_pct"]


def test_build_plan_sorted_confidence_desc(no_holdings):
    results = {
        "000001.SZ": _stock("000001", confidence=0.5, sector="A"),
        "000002.SZ": _stock("000002", confidence=0.9, sector="B"),
        "000003.SZ": _stock("000003", confidence=0.7, sector="C"),
    }
    plan = build_position_plan(_state(results), CONFIG)
    assert [o["code"] for o in plan["orders"]] == ["000002.SZ", "000003.SZ", "000001.SZ"]


# ==================== 组合校验 ====================

def test_sector_cap_trims_lower_ranked(no_holdings):
    """同板块两只票（各 10% 上限），板块上限 30% 不触发；缩到 15% 验证裁剪"""
    results = {
        "000001.SZ": _stock("000001", confidence=0.9, sector="白酒"),
        "000002.SZ": _stock("000002", confidence=0.8, sector="白酒"),
        "000003.SZ": _stock("000003", confidence=0.7, sector="AI"),
    }
    config = {**CONFIG, "max_sector_pct": 0.15}
    plan = build_position_plan(_state(results), config)

    by_sector = {}
    for o in plan["orders"]:
        by_sector[o["sector"]] = by_sector.get(o["sector"], 0.0) + o["amount"]
    # 白酒合计卡在板块上限 15 万：第一只 10 万全额，第二只被裁剪到 5 万
    assert by_sector.get("白酒", 0.0) <= 150_000.0 + 1e-6
    baijiu_orders = sorted([o for o in plan["orders"] if o["sector"] == "白酒"],
                           key=lambda o: o["confidence"], reverse=True)
    assert len(baijiu_orders) == 2
    assert baijiu_orders[0]["amount"] == pytest.approx(100_000.0)
    assert baijiu_orders[1]["amount"] < 100_000.0  # 排名靠后的被裁剪


def test_total_cap_with_existing_holdings(with_holdings):
    """已有持仓市值 60 万 + 新增 ≤ 80 万 → 新增最多 20 万"""
    with_holdings([{"code": "600036.SH", "name": "招商银行", "shares": 20000,
                    "cost_price": 30.0, "last_price": 30.0}])
    results = {
        "000001.SZ": _stock("000001", confidence=0.9),
        "000002.SZ": _stock("000002", confidence=0.8),
    }
    plan = build_position_plan(_state(results), CONFIG)

    total_new = sum(o["amount"] for o in plan["orders"])
    assert total_new <= 200_000.0 + 1e-6
    assert plan["summary"]["existing_position_pct"] == pytest.approx(0.6)
    assert plan["summary"]["final_position_pct"] <= 0.8 + 1e-9


# ==================== sizing 策略 ====================

def test_equal_strategy(no_holdings):
    results = {
        "000001.SZ": _stock("000001", confidence=0.9),
        "000002.SZ": _stock("000002", confidence=0.5),
    }
    plan = build_position_plan(
        _state(results), {**CONFIG, "position_sizing_strategy": "equal"}
    )
    amounts = [o["amount"] for o in plan["orders"]]
    assert amounts[0] == pytest.approx(amounts[1])


def test_kelly_strategy(no_holdings):
    """止损/目标价缺失 → 凯利 f=0 → 不参与分配"""
    results = {
        "000001.SZ": _stock("000001", confidence=0.6, last_close=100.0,
                            stop_loss=90.0, target_price=130.0),
        "000002.SZ": _stock("000002", confidence=0.6, last_close=100.0),
    }
    plan = build_position_plan(
        _state(results), {**CONFIG, "position_sizing_strategy": "kelly"}
    )
    assert len(plan["orders"]) == 1
    assert plan["orders"][0]["code"] == "000001.SZ"


def test_kelly_fraction_formula():
    # p=0.6, b=(130-100)/(100-90)=3 → f = 0.6 - 0.4/3 = 0.4667 → 截断到 0.25
    cand = {"confidence": 0.6, "last_close": 100.0, "stop_loss": 90.0, "target_price": 130.0}
    assert _kelly_fraction(cand) == pytest.approx(0.25)
    # 止损 >= 现价 → 0
    cand_bad = {"confidence": 0.6, "last_close": 100.0, "stop_loss": 110.0, "target_price": 130.0}
    assert _kelly_fraction(cand_bad) == 0.0
    # 目标价缺失 → 0
    cand_notarget = {"confidence": 0.6, "last_close": 100.0, "stop_loss": 90.0, "target_price": None}
    assert _kelly_fraction(cand_notarget) == 0.0


def test_allocate_empty():
    _allocate([], 100.0, 1000.0, "equal", 0.1)
    assert True  # 不抛异常即可


# ==================== 异常与降级 ====================

def test_no_total_capital_skips(no_holdings):
    plan = build_position_plan(
        _state({"000001.SZ": _stock("000001")}), {**CONFIG, "total_capital": 0}
    )
    assert "error" in plan
    assert plan["orders"] == []


def test_last_close_zero_skipped(no_holdings):
    results = {"000001.SZ": _stock("000001", last_close=0.0)}
    plan = build_position_plan(_state(results), CONFIG)
    assert plan["orders"] == []


def test_caution_halves_target_position(no_holdings):
    results = {"000001.SZ": _stock("000001", confidence=0.9)}
    plan = build_position_plan(_state(results, risk_gate="caution"), CONFIG)
    assert plan["target_position_pct"] == pytest.approx(0.4)
    # 预算减半：单票 10 万仍可买，但总预算 = 40 万
    assert sum(o["amount"] for o in plan["orders"]) <= 400_000.0 + 1e-6


def test_caution_kelly_respects_halved_budget(no_holdings):
    """kelly 策略不使用 budget 分配 → 第 4 步收口约束必须兜住 caution 折半预算"""
    results = {}
    for i in range(5):
        # 每只票不同板块，隔离板块上限（0.3）干扰，只测预算收口
        results[f"00000{i}.SZ"] = _stock(
            f"00000{i}", confidence=0.7, last_close=100.0,
            stop_loss=90.0, target_price=130.0, sector=f"板块{i}",
        )
    plan = build_position_plan(
        _state(results, risk_gate="caution"),
        {**CONFIG, "position_sizing_strategy": "kelly"},
    )
    # 每只单票上限 10 万 × 5 只 = 50 万需求 > caution 预算 40 万 → 第 5 只被裁
    total = sum(o["amount"] for o in plan["orders"])
    assert total <= 400_000.0 + 1e-6
    assert len(plan["orders"]) == 4


def test_block_gate_outputs_risk_plan(no_holdings):
    plan = build_position_plan(_state({}, risk_gate="block"), CONFIG)
    assert plan["orders"] == []
    assert "不生成买入订单" in plan["summary"]["advice"]
    assert plan["target_position_pct"] == 0.0


def test_build_risk_plan_empty_pool(no_holdings):
    plan = build_risk_plan(_state({}, risk_gate="normal"), CONFIG)
    assert plan["orders"] == []
    assert "候选池为空" in plan["summary"]["advice"]
