"""
单元测试：SignalProcessor 止损位（stop_loss）抽取扩展
"""

from unittest.mock import MagicMock

import pytest

from AI.graph.signal_processing import SignalProcessor


def _processor(response_text):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content=response_text)
    return SignalProcessor(fake_llm)


def test_stop_loss_from_json():
    proc = _processor(
        '{"action": "买入", "target_price": 1700, "stop_loss": 1450, '
        '"confidence": 0.75, "risk_score": 0.3, "reasoning": "趋势向上"}'
    )
    d = proc.process_signal("建议买入")
    assert d["stop_loss"] == pytest.approx(1450.0)


def test_stop_loss_null_falls_back_to_text():
    proc = _processor(
        '{"action": "买入", "target_price": 1700, "stop_loss": null, '
        '"confidence": 0.75, "risk_score": 0.3, "reasoning": "趋势向上"}'
    )
    d = proc.process_signal("建议买入，止损位：1450")
    assert d["stop_loss"] == pytest.approx(1450.0)


def test_stop_loss_missing_uses_text():
    proc = _processor(
        '{"action": "持有", "target_price": 100, '
        '"confidence": 0.7, "risk_score": 0.5, "reasoning": "观望"}'
    )
    d = proc.process_signal("跌破 45.5 元止损离场")
    assert d["stop_loss"] == pytest.approx(45.5)


def test_stop_loss_absent_everywhere_is_none():
    proc = _processor(
        '{"action": "持有", "target_price": 100, '
        '"confidence": 0.7, "risk_score": 0.5, "reasoning": "观望"}'
    )
    d = proc.process_signal("建议观望")
    assert d["stop_loss"] is None


def test_default_decision_has_stop_loss_none():
    proc = _processor("")
    d = proc.process_signal("")
    assert d["stop_loss"] is None
    assert d["action"] == "持有"


def test_simple_decision_extracts_stop_loss():
    """LLM 返回非 JSON 时走 _extract_simple_decision 兜底路径"""
    proc = _processor("这是一段没有 JSON 的文本")
    d = proc.process_signal("建议买入，目标价 20，止损 18.5")
    assert d["action"] == "买入"
    assert d["stop_loss"] == pytest.approx(18.5)
