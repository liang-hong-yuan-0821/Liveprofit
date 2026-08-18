"""
AI 预填写模块测试（2026-08-18）

覆盖：LLM 输出容错解析（JSON/代码块/杂质/坏输出）、字段清洗（值域收敛/
非法类型置 None）、LLM 不可用降级。
"""

import pytest

from AI.eventStudy.review import ai_prelabel


def test_parse_plain_json():
    text = '{"event_type": "央行", "event_subtype": "降准", "event_condition": "利好", "importance": 5, "expected_value": null, "actual_value": null, "previous_value": null}'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "央行"


def test_parse_json_code_block():
    text = '```json\n{"event_type": "宏观数据", "importance": 4}\n```'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "宏观数据"


def test_parse_json_with_noise():
    text = '分析如下：\n{"event_type": "地缘", "importance": 4}\n以上仅供参考。'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "地缘"


def test_parse_bad_output_returns_none():
    assert ai_prelabel._parse_json_output("这不是 JSON") is None
    assert ai_prelabel._parse_json_output("") is None
    assert ai_prelabel._parse_json_output(None) is None


def test_sanitize_clamps_and_nullifies():
    # importance 越界收敛到 1-5
    s = ai_prelabel._sanitize({"event_type": "央行", "importance": 99,
                               "expected_value": "abc", "actual_value": "2.1",
                               "previous_value": None, "event_condition": 123})
    assert s["importance"] == 5
    assert s["expected_value"] is None        # 非数字 → None
    assert s["actual_value"] == pytest.approx(2.1)
    assert s["previous_value"] is None
    assert s["event_condition"] == "123"      # 转字符串


def test_prelabel_one_llm_unavailable(monkeypatch):
    """LLM 不可用 → 返回空建议，不阻塞。"""
    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: None)
    assert ai_prelabel.prelabel_one({"title": "测试", "content": ""}) == {}


def test_prelabel_one_bad_output(monkeypatch):
    """LLM 输出不可解析 → 返回空建议。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content": "随便什么文本"})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    assert ai_prelabel.prelabel_one({"title": "测试", "content": ""}) == {}


def test_prelabel_one_success(monkeypatch):
    """正常路径：LLM 输出经解析 + 清洗后返回建议。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content":
                '{"event_type": "央行", "event_subtype": "降准", '
                '"event_condition": "利好", "importance": 5, '
                '"expected_value": null, "actual_value": null, '
                '"previous_value": null}'})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    s = ai_prelabel.prelabel_one({"title": "央行宣布降准", "content": ""})
    assert s["event_type"] == "央行"
    assert s["event_subtype"] == "降准"
    assert s["importance"] == 5
    assert s["actual_value"] is None
