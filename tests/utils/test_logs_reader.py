"""
AI/logviewer/logs_reader.py 纯函数测试：
run 排序与噪音排除、新旧 dataprovider 判别、缺失文件/坏 JSON 安全通道、legacy 解析。
"""

import json

import pytest

from AI.logviewer import logs_reader as L


@pytest.fixture
def logs_fixture(tmp_path, monkeypatch):
    """搭一个含新旧格式、tools、reports、噪音目录的 logs 树"""
    logs = tmp_path / "logs"
    run_old = logs / "2026-08-15_194033"
    run_old.mkdir(parents=True)
    run_new = logs / "2026-08-19_223929"
    node = run_new / "market" / "001_International_Event_Extraction_Analyst"
    node.mkdir(parents=True)

    (node / "req.md").write_text("提示词", encoding="utf-8")
    (node / "res.md").write_text("# 结果", encoding="utf-8")
    (node / "meta.json").write_text('{"model": "m"}', encoding="utf-8")

    # 新格式 dataprovider
    dp_new = node / "001_get_central_bank_calendar"
    dp_new.mkdir(parents=True)
    (dp_new / "req.json").write_text('{"curr_date": "2026-08-20"}', encoding="utf-8")
    (dp_new / "res.md").write_text("# 央行日历", encoding="utf-8")
    (dp_new / "meta.json").write_text(
        '{"name": "get_central_bank_calendar", "desc": "获取日历", "seq": 1, "ts": "1", "res": "res.md"}',
        encoding="utf-8")

    # 旧格式 dataprovider（平铺 json）
    (node / "get_macro_indicators.json").write_text(json.dumps({
        "name": "get_macro_indicators",
        "desc": "宏观指标",
        "req": {"kind": "cpi"},
        "res": "# CPI\n- 3.4",
    }, ensure_ascii=False), encoding="utf-8")

    # tools
    tool = node / "tools" / "001_search"
    tool.mkdir(parents=True)
    (tool / "req.json").write_text('{"name": "search", "input": "x"}', encoding="utf-8")
    (tool / "res.txt").write_text("结果", encoding="utf-8")

    # reports + 噪音
    reports = run_new / "reports"
    reports.mkdir()
    (reports / "01_市场层报告.md").write_text("# 市场层", encoding="utf-8")
    (reports / "02_final_position_plan.json").write_text('{"a": 1}', encoding="utf-8")

    (logs / "backups").mkdir()
    (logs / "event_study_daily.log").write_text("", encoding="utf-8")
    (logs / "not_a_run").mkdir()

    monkeypatch.setenv("LIVEPROFIT_LOGS_DIR", str(logs))
    yield logs


def test_logs_root_env(logs_fixture):
    assert L.logs_root() == logs_fixture


def test_list_runs_excludes_noise_newest_first(logs_fixture):
    names = [p.name for p in L.list_runs()]
    assert names == ["2026-08-19_223929", "2026-08-15_194033"]


def test_list_runs_missing_root(tmp_path):
    assert L.list_runs(tmp_path / "nope") == []


def test_list_layers_excludes_reports(logs_fixture):
    run = logs_fixture / "2026-08-19_223929"
    assert [p.name for p in L.list_layers(run)] == ["market"]


def test_list_nodes(logs_fixture):
    layer = logs_fixture / "2026-08-19_223929" / "market"
    assert [p.name for p in L.list_nodes(layer)] == ["001_International_Event_Extraction_Analyst"]


def test_list_dp_calls_new_and_legacy(logs_fixture):
    node = logs_fixture / "2026-08-19_223929" / "market" / "001_International_Event_Extraction_Analyst"
    calls = L.list_dp_calls(node)
    kinds = [k for k, _ in calls]
    paths = [p.name for _, p in calls]
    assert kinds == ["new", "legacy"]
    assert paths == ["001_get_central_bank_calendar", "get_macro_indicators.json"]


def test_list_tools(logs_fixture):
    node = logs_fixture / "2026-08-19_223929" / "market" / "001_International_Event_Extraction_Analyst"
    assert [p.name for p in L.list_tools(node)] == ["001_search"]


def test_list_report_files(logs_fixture):
    run = logs_fixture / "2026-08-19_223929"
    assert [p.name for p in L.list_report_files(run)] == [
        "01_市场层报告.md", "02_final_position_plan.json"]


def test_read_missing_and_bad_json(logs_fixture):
    assert L.read_json(logs_fixture / "nope.json") is None
    assert L.read_text(logs_fixture / "nope.md") is None
    bad = logs_fixture / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert L.read_json(bad) is None


def test_parse_legacy(logs_fixture):
    node = logs_fixture / "2026-08-19_223929" / "market" / "001_International_Event_Extraction_Analyst"
    payload = L.parse_legacy(node / "get_macro_indicators.json")
    assert payload["name"] == "get_macro_indicators"
    assert payload["desc"] == "宏观指标"
    assert payload["req"] == {"kind": "cpi"}
    assert payload["res"] == "# CPI\n- 3.4"
