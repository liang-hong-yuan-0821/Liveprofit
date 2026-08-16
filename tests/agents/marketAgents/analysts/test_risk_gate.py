"""
单元测试：risk_gate 熔断开关规则派生（fail-open 默认 normal）

覆盖方案 3.3.1 判定表：系统性风险/短线回避 → block；
短线谨慎/波段防御 → caution；其余（含提取失败）→ normal。
"""

from AI.marketAgents.analysts.cn_tech_analyst import derive_risk_gate


def _regime(status="结构性行情", short="适合", wave="进攻"):
    return (
        f"市场状态标签: <{status}>\n"
        "情绪周期位置: <修复>\n"
        "三级别判定:\n"
        f"- 短线: <{short}> 建议仓位<6成> — 理由\n"
        f"- 波段: <{wave}> 主线容纳性<是> — 理由\n"
        "- 长线: <配置窗口> 风格方向<大盘>+<价值> — 理由"
    )


def test_block_by_systemic_risk():
    assert derive_risk_gate(_regime(status="系统性风险")) == "block"


def test_block_by_short_term_avoid():
    assert derive_risk_gate(_regime(short="回避")) == "block"


def test_caution_by_short_term_careful():
    assert derive_risk_gate(_regime(short="谨慎")) == "caution"


def test_caution_by_wave_defensive():
    assert derive_risk_gate(_regime(wave="防御")) == "caution"


def test_normal_when_all_good():
    assert derive_risk_gate(_regime()) == "normal"


def test_normal_fail_open():
    """市场层未运行/提取失败/标签缺失 → fail-open 退化为 normal"""
    assert derive_risk_gate("") == "normal"
    assert derive_risk_gate("太短") == "normal"
    assert derive_risk_gate("这是一段没有标签的自由文本，完全不符合模板格式，也不该触发熔断") == "normal"


def test_normal_on_unknown_labels():
    """同义表述漏判 → 退化为 normal（宁漏勿错）"""
    regime = "市场状态标签: <系统性回调>\n- 短线: <规避>\n- 波段: <防守>"
    assert derive_risk_gate(regime) == "normal"
