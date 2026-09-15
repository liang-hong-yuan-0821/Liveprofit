"""单元测试：risk_gate 有序规则表派生（T6 语义变更：fail-open → caution）。

覆盖方案第十二章有序规则表全枚举（短路判定：逐条命中即返回，不进入后续规则）：
  1. `systemic_risk=high` 且 `confidence ∈ {high, medium}` 且核心质量非不足 → block
  2. `market_regime` 短线 = 回避 且核心质量非不足 → block
  3. 核心质量不足（判据 a–d，含结构解析失败/输出级不足枚举）→ caution（全局上限）
  4. `systemic_risk=medium` 或短线 = 谨慎 或波段 = 防御 → caution
  5. 其余 → normal

实现：`AI/dataflows/market_features.derive_risk_gate`（纯代码节点
`MarketLayerGraph._derive_risk_gate` 消费同一函数，见
`tests/graph/test_market_layer_graph.py`）。**关键数据不足不再 fail-open**：
结构解析失败/核心质量不足一律 `caution`。不依赖真实 LLM。
"""

import pytest

from AI.dataflows import market_features as mf

# 最小质量快照（判据 a：degradation_level + core_insufficient）
QUALITY_OK = {"degradation_level": "ok", "core_insufficient": False}
QUALITY_BAD = {"degradation_level": "insufficient", "core_insufficient": True}


def _gra(systemic="low", confidence="medium", dq=None):
    out = {"systemic_risk": systemic, "confidence": confidence}
    if dq is not None:
        out["data_quality"] = dq
    return out


def _regime(short="适合", wave="进攻", long_term="配置窗口", dq=None):
    out = {
        "short_term": {"level": short},
        "wave": {"level": wave},
        "long_term": {"level": long_term},
    }
    if dq is not None:
        out["data_quality"] = dq
    return out


def test_fallback_constant_is_caution():
    """确认决策：关键数据不足由 fail-open(normal) 改为 caution。"""
    assert mf.RISK_GATE_FALLBACK == "caution"
    assert mf.RISK_GATE_ENUM == ("normal", "caution", "block")


@pytest.mark.parametrize("case,expected_gate,expected_rule", [
    # 规则 1：systemic=high + confidence ∈ {high, medium}
    ("rule1_high_high", "block", 1),
    ("rule1_high_medium", "block", 1),
    # 评审 M3：confidence=low / 缺失不再放行 normal → 判据 (c) → caution
    ("rule1_high_low_degrades_to_caution", "caution", 3),
    ("rule1_high_missing_confidence_degrades_to_caution", "caution", 3),
    # 规则 1 优先于规则 2（同命中时短路在规则 1）
    ("rule1_precedes_rule2", "block", 1),
    # 规则 2：短线 = 回避
    ("rule2_short_avoid", "block", 2),
    # 规则 4：systemic=medium / 短线=谨慎 / 波段=防御
    ("rule4_systemic_medium", "caution", 4),
    ("rule4_short_caution", "caution", 4),
    ("rule4_wave_defense", "caution", 4),
    # 规则 5
    ("rule5_all_good", "normal", 5),
])
def test_ordered_rule_table(case, expected_gate, expected_rule):
    gra, regime = _gra(), _regime()
    if case == "rule1_high_high":
        gra = _gra("high", "high")
    elif case == "rule1_high_medium":
        gra = _gra("high", "medium")
    elif case == "rule1_high_low_degrades_to_caution":
        gra = _gra("high", "low")
    elif case == "rule1_high_missing_confidence_degrades_to_caution":
        gra = {"systemic_risk": "high"}
    elif case == "rule1_precedes_rule2":
        gra, regime = _gra("high", "medium"), _regime(short="回避")
    elif case == "rule2_short_avoid":
        regime = _regime(short="回避")
    elif case == "rule4_systemic_medium":
        gra = _gra("medium", "medium")
    elif case == "rule4_short_caution":
        regime = _regime(short="谨慎")
    elif case == "rule4_wave_defense":
        regime = _regime(wave="防御")

    decision = mf.risk_gate_decision(gra, regime, QUALITY_OK)
    assert decision["gate"] == expected_gate
    assert decision["rule"] == expected_rule
    assert decision["reason"]


def test_rule3_quality_insufficient_is_ceiling():
    """规则 3：核心质量不足 → caution 全局上限（即便 systemic=high/短线=回避也不 block）。"""
    assert mf.derive_risk_gate(_gra("high", "high"), _regime(), QUALITY_BAD) == "caution"
    assert mf.derive_risk_gate(_gra("low"), _regime(short="回避"), QUALITY_BAD) == "caution"
    # 规则 3 优先于规则 4（质量不足 + 波段防御 → 命中规则 3）
    decision = mf.risk_gate_decision(_gra("low"), _regime(wave="防御"), QUALITY_BAD)
    assert (decision["gate"], decision["rule"]) == ("caution", 3)


def test_rule3_structure_failure_degrades_to_caution():
    """判据 (c)：结构解析失败（枚举缺失/非 dict 输入）→ caution（不再 fail-open normal）。"""
    assert mf.derive_risk_gate({}, {}, QUALITY_OK) == "caution"
    assert mf.derive_risk_gate(_gra("low"), {}, QUALITY_OK) == "caution"
    assert mf.derive_risk_gate(_gra("low"), _regime(short="随便"), QUALITY_OK) == "caution"


def test_legacy_text_inputs_no_longer_fail_open():
    """历史自由文本输入（无结构化 dict）→ caution，不得静默放行 normal。"""
    for legacy in ("", "太短", "市场状态标签: 系统性风险\n- 短线: <回避>",
                   "这是一段没有标签的自由文本，完全不符合模板格式"):
        assert mf.derive_risk_gate(legacy, legacy, QUALITY_OK) == "caution"


def test_rule3_output_insufficient_enums():
    """判据 (d)：输出级不足枚举（systemic=insufficient / 短线=信息不足）→ caution。"""
    assert mf.derive_risk_gate(_gra("insufficient", "medium"), _regime(), QUALITY_OK) == "caution"
    assert mf.derive_risk_gate(
        _gra("low"), _regime(short="信息不足"), QUALITY_OK) == "caution"


def test_quality_insufficient_is_union_of_snapshot_and_node():
    """判据 (a)+(b) 取并集（评审 M2）：节点自评 ok 不得清零启动快照的不足。

    修复前 `_core_quality_insufficient` 让节点级 `data_quality=ok` 直接压过
    启动快照的 `insufficient`（判据 a 被绕过）——核心输入缺失时规则 3 的
    caution 全局上限失效，systemic=high 仍可 block。
    """
    gra = _gra("low", "medium", dq=dict(QUALITY_OK))
    regime = _regime(dq=dict(QUALITY_OK))
    # 快照不足 + 节点标注 ok → 仍 caution（修复前的漏洞路径）
    assert mf.derive_risk_gate(gra, regime, QUALITY_BAD) == "caution"
    assert mf.derive_risk_gate(gra, regime, {}) == "caution"  # 无快照 fail-closed
    # 快照 ok + 节点 ok → normal（并集不引入额外升级）
    assert mf.derive_risk_gate(gra, regime, QUALITY_OK) == "normal"
    # 快照不足 + systemic=high + confidence=high → 规则 3 上限，不得 block
    assert mf.derive_risk_gate(_gra("high", "high", dq=dict(QUALITY_OK)),
                               regime, QUALITY_BAD) == "caution"
    # 节点级标注 insufficient → 升级
    bad_gra = _gra("low", "medium", dq=dict(QUALITY_BAD))
    assert mf.derive_risk_gate(bad_gra, _regime(dq=dict(QUALITY_OK)), QUALITY_OK) == "caution"


def test_missing_quality_snapshot_fail_closed():
    """无启动快照且节点未标注 → 视为不足（fail-closed → caution）。"""
    assert mf.derive_risk_gate(_gra("low"), _regime(), {}) == "caution"


def test_gate_enum_always_valid():
    """全枚举输入下返回值恒在 `normal/caution/block`（仓位管理层零改动前提）。"""
    cases = [
        mf.derive_risk_gate(_gra("low"), _regime(), QUALITY_OK),
        mf.derive_risk_gate(_gra("high", "high"), _regime(), QUALITY_OK),
        mf.derive_risk_gate(_gra("medium"), _regime(), QUALITY_OK),
        mf.derive_risk_gate(_gra("insufficient"), _regime(), QUALITY_BAD),
        mf.derive_risk_gate(None, None, None),
        mf.derive_risk_gate(_gra("high", "high"), _regime(short="回避"), QUALITY_BAD),
    ]
    assert set(cases) <= set(mf.RISK_GATE_ENUM)


def test_enum_constants_unchanged():
    """枚举与现状一致（门控/仓位管理零改动）。"""
    assert mf.SYSTEMIC_RISK_ENUM == ("high", "medium", "low", "insufficient")
    assert mf.CONFIDENCE_ENUM == ("high", "medium", "low")
    assert mf.SHORT_TERM_ENUM == ("适合", "谨慎", "回避", "信息不足")
    assert mf.WAVE_ENUM == ("进攻", "平衡", "防御", "信息不足")
    assert mf.LONG_TERM_ENUM == ("配置窗口", "等待窗口", "信息不足")
