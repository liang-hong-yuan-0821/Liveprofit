"""
日志查看器冒烟测试：AppTest 无头运行 app.py，断言无异常。

LIVEPROFIT_LOGS_DIR 指向 fixture 日志树，保证页面有内容可渲染
（覆盖 layer 时序展开 / DataProvider 新旧格式 / Tools / 运行报告 tab 的渲染路径），
以及调试步进按钮区（sticky 顶栏右侧）的确认/跳过/stale 防护。

注意：AppTest 不触发 fragment 的 run_every 定时器——5s 目录自动刷新与 1s
检查点轮询的定时行为需手动 `streamlit run` 验证；检查点变化靠 at.run() 模拟。
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[2] / "AI" / "logviewer" / "app.py"


@pytest.fixture
def logs_fixture(tmp_path, monkeypatch):
    """最小但完整的日志树：4 个 layer（各 1 节点）+ 新格式 dp + 旧格式 dp +
    tools + reports；另建一个较旧 run 供跨 run 检查点用例。"""
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

    for layer_name in ("sector", "stock", "screening"):
        ln = run / layer_name / f"001_{layer_name.capitalize()}_Analyst"
        ln.mkdir(parents=True)
        (ln / "req.md").write_text("提示词", encoding="utf-8")
        (ln / "res.md").write_text("# 结果", encoding="utf-8")
        (ln / "meta.json").write_text('{"model": "m"}', encoding="utf-8")

    reports = run / "reports"
    reports.mkdir()
    (reports / "01_报告.md").write_text("# 报告", encoding="utf-8")

    # 较旧 run：供跨 run 检查点用例（检查点指向其自身节点）
    run_old = tmp_path / "2026-08-15_194033"
    node_old = run_old / "sector" / "001_Sector_News_Analyst"
    node_old.mkdir(parents=True)
    (node_old / "req.md").write_text("提示词", encoding="utf-8")
    (node_old / "res.md").write_text("# 结果", encoding="utf-8")
    (node_old / "meta.json").write_text('{"model": "m"}', encoding="utf-8")

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


def test_tabs_labels(logs_fixture):
    """主区仅两个 tab：调用时序（默认） + 运行报告"""
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert [t.label for t in at.tabs] == ["调用时序", "运行报告"]


def test_sidebar_only_run_select(logs_fixture):
    """sidebar 仅保留 run 批次选择，无 layer/node 筛选"""
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert len(at.sidebar.selectbox) == 1
    assert at.sidebar.selectbox[0].label == "运行批次"


def test_auto_switch_to_newest_run(logs_fixture):
    """新批次出现 → 自动切换到最新 run（覆盖当前选中）"""
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert at.session_state["run_name"] == "2026-08-19_223929"

    # 模拟分析进程创建新批次
    (logs_fixture / "2026-08-23_123000" / "market").mkdir(parents=True)
    at.run()   # 模拟 5s fragment 定时触发

    assert at.session_state["run_name"] == "2026-08-23_123000"
    assert at.sidebar.selectbox[0].value == "2026-08-23_123000"


def test_timeline_layer_order(logs_fixture):
    """layer 大展开按固定执行序 market→sector→stock→screening"""
    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    labels = [e.label for e in at.expander]
    idx = {name: labels.index(name) for name in
           ("market", "sector", "stock", "screening")}
    assert idx["market"] < idx["sector"] < idx["stock"] < idx["screening"]


def _checkpoint_payload(**overrides):
    """构造调试步进检查点 payload（默认 waiting，指向 fixture 中已有的 dp 内容）"""
    payload = {
        "status": "waiting",
        "seq": 5,
        "kind": "dp",
        "ts": "2026-08-22 10:00:00",
        "layer": "market",
        "node": "International Event Extraction Analyst",
        "name": "get_x",
        "dir": "market/001_International_Event_Extraction_Analyst/001_get_x",
        "show_file": "res.md",
    }
    payload.update(overrides)
    return payload


def test_step_bar_confirm(logs_fixture):
    """调试步进按钮区：waiting 检查点渲染 + 点【✅ 下一步】后 status 变为 confirmed"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload()), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    assert at.button(key="step_confirm")

    at.button(key="step_confirm").click().run()
    payload = json.loads(cp.read_text(encoding="utf-8"))
    assert payload["status"] == "confirmed"


def test_step_bar_skip_all(logs_fixture):
    """点【⏭ 跳过全部】后 status 变为 skip_all"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload()), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert at.button(key="step_skip_all")

    at.button(key="step_skip_all").click().run()
    payload = json.loads(cp.read_text(encoding="utf-8"))
    assert payload["status"] == "skip_all"


def test_step_bar_stale_seq_no_write(logs_fixture):
    """stale 防护：文件已推进到下一个检查点（seq 不一致）时点确认不写文件。

    双机制兜底：点击触发 fragment 重跑时 sig 变化会提前 st.rerun 中断点击，
    即使点击到达写入路径也有 seen_seq 与文件 seq 的比对阻断。"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload(seq=5)), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    # 模拟后端已推进到下一个检查点（页面尚未刷新看到）
    cp.write_text(json.dumps(_checkpoint_payload(seq=6)), encoding="utf-8")

    at.button(key="step_confirm").click().run()
    payload = json.loads(cp.read_text(encoding="utf-8"))
    assert payload["status"] == "waiting"  # 未被写入 confirmed
    assert payload["seq"] == 6


def test_checkpoint_expands_target(logs_fixture):
    """检查点（kind=dp）指向的 layer/node 展开自动展开并加「⏸」前缀"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload()), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert "⏸ market" in labels
    assert "⏸ 001_International_Event_Extraction_Analyst" in labels


def test_checkpoint_expands_dp(logs_fixture):
    """kind=dp 检查点 → 对应 DP expander（dir 第三段 == 目录名）加「⏸」"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload()), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    labels = [e.label for e in at.expander]
    assert "⏸ 001_get_x　get_x — 获取x" in labels


def test_checkpoint_expands_llm_req(logs_fixture):
    """kind=llm_req 检查点 → node 内 req expander 加「⏸」"""
    run = logs_fixture / "2026-08-19_223929"
    cp = run / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload(
        kind="llm_req", name="International Event Extraction Analyst",
        dir="market/001_International_Event_Extraction_Analyst",
        show_file="req.md")), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    labels = [e.label for e in at.expander]
    assert "⏸ market" in labels
    assert "⏸ 001_International_Event_Extraction_Analyst" in labels
    assert "⏸ req.md（提示词）" in labels


def test_checkpoint_other_run_hint(logs_fixture):
    """检查点属于另一批次：按钮仍可用且写回该批次，提示所属批次"""
    run_old = logs_fixture / "2026-08-15_194033"
    cp = run_old / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload(
        layer="sector", node="Sector News Analyst", name="get_z",
        dir="sector/001_Sector_News_Analyst/001_get_z")), encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    # 按钮仍渲染（写回 found 的 run_dir = 较旧批次）
    assert at.button(key="step_confirm")
    captions = [c.value for c in at.caption]
    assert any("检查点属于批次" in c for c in captions)

    at.button(key="step_confirm").click().run()
    payload = json.loads(cp.read_text(encoding="utf-8"))
    assert payload["status"] == "confirmed"


def test_stale_confirmed_checkpoint_other_run_hidden(logs_fixture):
    """非当前批次的已完成检查点（confirmed 残留）不展示——
    新批次自动切换后、到达首个检查点前不应出现"检查点属于其他批次"的误导提示"""
    run_old = logs_fixture / "2026-08-15_194033"
    cp = run_old / ".debug_checkpoint.json"
    cp.write_text(json.dumps(_checkpoint_payload(
        status="confirmed", layer="sector", node="Sector News Analyst",
        name="get_z", dir="sector/001_Sector_News_Analyst/001_get_z")),
        encoding="utf-8")

    at = AppTest.from_file(str(APP_PATH), default_timeout=15)
    at.run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert not any("检查点属于批次" in c for c in captions)
    assert not any("已确认" in c for c in captions)
    # 按钮不渲染（无等待中的检查点）
    assert not [b for b in at.button if b.key == "step_confirm"]
