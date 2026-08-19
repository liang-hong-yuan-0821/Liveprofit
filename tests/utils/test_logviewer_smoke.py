"""
日志查看器冒烟测试：AppTest 无头运行 app.py，断言无异常。

LIVEPROFIT_LOGS_DIR 指向 fixture 日志树，保证页面有内容可渲染
（覆盖节点上下文 / DataProvider 新旧格式 / Tools / 运行报告四个 tab 的渲染路径）。
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[2] / "AI" / "logviewer" / "app.py"


@pytest.fixture
def logs_fixture(tmp_path, monkeypatch):
    """最小但完整的日志树：新格式 dp + 旧格式 dp + tools + reports"""
    run = tmp_path / "2026-08-19_223929"
    node = run / "market" / "001_International_Event_Extraction_Analyst"
    node.mkdir(parents=True)

    (node / "req.md").write_text("提示词", encoding="utf-8")
    (node / "res.md").write_text("# 结果", encoding="utf-8")
    (node / "meta.json").write_text('{"model": "m", "node": "n"}', encoding="utf-8")

    dp_new = node / "001_get_x"
    dp_new.mkdir(parents=True)
    (dp_new / "req.json").write_text('{"a": 1}', encoding="utf-8")
    (dp_new / "res.md").write_text("# x", encoding="utf-8")
    (dp_new / "meta.json").write_text(
        '{"name": "get_x", "desc": "获取x", "seq": 1, "ts": "1", "res": "res.md"}',
        encoding="utf-8")

    (node / "get_y.json").write_text(json.dumps(
        {"name": "get_y", "desc": "获取y", "req": {}, "res": "# y"},
        ensure_ascii=False), encoding="utf-8")

    tool = node / "tools" / "001_search"
    tool.mkdir(parents=True)
    (tool / "req.json").write_text('{}', encoding="utf-8")
    (tool / "res.txt").write_text("结果", encoding="utf-8")

    reports = run / "reports"
    reports.mkdir()
    (reports / "01_报告.md").write_text("# 报告", encoding="utf-8")

    monkeypatch.setenv("LIVEPROFIT_LOGS_DIR", str(tmp_path))
    yield tmp_path


def test_app_smoke(logs_fixture):
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception


def test_app_smoke_no_logs(tmp_path, monkeypatch):
    """logs 目录为空时页面提示而非报错"""
    monkeypatch.setenv("LIVEPROFIT_LOGS_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
