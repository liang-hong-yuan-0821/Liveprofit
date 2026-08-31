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

    # tushare 端点子日志（嵌套在 DP 调用目录内）
    ts_call = dp_new / "tushare" / "001_trade_cal"
    ts_call.mkdir(parents=True)
    (ts_call / "req.json").write_text(
        '{"api_name": "trade_cal", "fields": "", "params": {"exchange": "SSE"}}',
        encoding="utf-8")
    (ts_call / "res.json").write_text('{"columns": [], "shape": [0, 0]}',
                                      encoding="utf-8")
    (ts_call / "meta.json").write_text(
        '{"name": "trade_cal", "seq": 1, "ts": "1", "res": "res.json", "probe": false}',
        encoding="utf-8")
    (dp_new / "tushare" / "noise.txt").write_text("噪音文件", encoding="utf-8")

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
    # charts 子目录（板块层热力图 HTML）
    charts_dir = reports / "charts"
    charts_dir.mkdir()
    (charts_dir / "sector_daily_heatmaps.html").write_text("<html>plotly</html>", encoding="utf-8")

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


def test_sort_layers_order(logs_fixture):
    """已知 layer 按固定执行序 market→sector→stock→screening，未知字母序附后"""
    run = logs_fixture / "2026-08-19_223929"
    for name in ["screening", "stock", "sector", "unknown_x", "unknown_a"]:
        (run / name).mkdir()
    layers = L.list_layers(run)
    assert [p.name for p in L.sort_layers(layers)] == [
        "market", "sector", "stock", "screening", "unknown_a", "unknown_x"]


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


def test_list_tushare_calls(logs_fixture):
    """DP 调用目录下的 tushare/ 端点调用目录；噪音文件排除；无 tushare/ → 空"""
    node = logs_fixture / "2026-08-19_223929" / "market" / "001_International_Event_Extraction_Analyst"
    dp_new = node / "001_get_central_bank_calendar"
    assert [p.name for p in L.list_tushare_calls(dp_new)] == ["001_trade_cal"]

    # 无 tushare/ 子目录（旧 run / 非 tushare 数据源）→ 空列表
    legacy_node_dir = dp_new.parent
    assert L.list_tushare_calls(legacy_node_dir) == []


def test_list_report_files(logs_fixture):
    run = logs_fixture / "2026-08-19_223929"
    assert [p.name for p in L.list_report_files(run)] == [
        "01_市场层报告.md", "02_final_position_plan.json",
        "sector_daily_heatmaps.html"]


def test_list_report_files_recurses_charts_subdir(logs_fixture):
    """reports/ 递归收集：charts/ 子目录下的 HTML 图表文件纳入，排序键为相对路径"""
    run = logs_fixture / "2026-08-19_223929"
    rel = [p.relative_to(run / "reports").as_posix()
           for p in L.list_report_files(run)]
    assert rel == ["01_市场层报告.md", "02_final_position_plan.json",
                   "charts/sector_daily_heatmaps.html"]


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


# ---- 调试步进检查点 ----

def test_find_checkpoint(logs_fixture):
    """缺失 → None；正常 JSON → payload；坏 JSON → None"""
    run = logs_fixture / "2026-08-19_223929"
    assert L.find_checkpoint(run) is None
    (run / L.CHECKPOINT_FILE).write_text('{"status": "waiting", "seq": 1}',
                                         encoding="utf-8")
    assert L.find_checkpoint(run)["status"] == "waiting"
    (run / L.CHECKPOINT_FILE).write_text("not-json", encoding="utf-8")
    assert L.find_checkpoint(run) is None


def test_find_active_checkpoint_waiting_first(logs_fixture):
    """无检查点 → None；waiting 优先（并发 run 不被遮蔽）；无 waiting 退回最新"""
    run_old = logs_fixture / "2026-08-15_194033"
    run_new = logs_fixture / "2026-08-19_223929"

    assert L.find_active_checkpoint() is None

    # 仅较新 run 有 confirmed → 返回它（历史状态展示）
    (run_new / L.CHECKPOINT_FILE).write_text(
        '{"status": "confirmed", "seq": 2}', encoding="utf-8")
    run_dir, payload = L.find_active_checkpoint()
    assert run_dir == run_new and payload["status"] == "confirmed"

    # 较旧 run 出现 waiting → 优先返回 waiting（不被较新 run 的 confirmed 遮蔽）
    (run_old / L.CHECKPOINT_FILE).write_text(
        '{"status": "waiting", "seq": 1}', encoding="utf-8")
    run_dir, payload = L.find_active_checkpoint()
    assert run_dir == run_old and payload["status"] == "waiting"
