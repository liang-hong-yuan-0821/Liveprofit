"""
LiveProfit 日志查看器
浏览 logs/{时间戳}/ 下的运行日志：layer 大展开 → LLM 节点展开（调用时序）→
节点内嵌套 dataprovider / tools 展开；dataprovider 展开内再嵌套 tushare 端点
子调用展开（tushare/{seq:03d}_{api_name}/，仅 tushare 数据源的 run 有）；
兼容历史旧格式 dataprovider 日志（{接口名}.json，标记"旧格式"）。

页面结构：
- sidebar：仅 run 批次选择，@st.fragment(run_every="5s") 自动刷新目录列表
- 主区 tabs（sticky 固定）：调用时序（layer 大展开层级 + 顶部右侧调试步进按钮区）/ 运行报告
- 节点展开内按调用时间顺序：DP 预取（板块层节点先取数据再组装 prompt）→
  LLM 请求 → 工具调用 → LLM 输出 → meta
- 调试步进按钮区（@st.fragment(run_every="1s") 轮询 .debug_checkpoint.json，
  waiting 时显示【✅ 下一步】/【⏭ 跳过全部】，写回 status 后后端继续，
  见 AI/utils/step_gate.py）；检查点指向的 layer/node/DP 展开自动展开并加「⏸」前缀

运行：streamlit run AI/logviewer/app.py   （项目根目录执行）
"""

import json
import os
from pathlib import Path

import streamlit as st

from AI.logviewer import logs_reader as L

st.set_page_config(page_title="LiveProfit 日志查看器", layout="wide")

# 结果文本超过该字节数时折叠展示（防超大 markdown 拖慢布局）
_LARGE_RES_KB = 100

# 顶栏 tabs 行 sticky 固定；选择器失效仅体验降级为"随页面滚动"，功能不受影响
_CSS_STICKY_HEADER = """
<style>
div[data-testid="stTabs"] {
  position: sticky; top: 0; z-index: 999; background: var(--background-color);
}
</style>
"""
st.markdown(_CSS_STICKY_HEADER, unsafe_allow_html=True)


def _render_res(res, label: str = "res") -> None:
    """按类型渲染调用结果：str → markdown，dict → json，超大内容折叠
    （str 按字符数、dict 按序列化字节数，防大结果拖慢布局）"""
    if isinstance(res, str):
        if len(res) > _LARGE_RES_KB * 1024:
            st.caption(f"{label}（{len(res) // 1024} KB，已折叠）")
            with st.expander("查看完整内容"):
                st.markdown(res)
        else:
            st.markdown(res)
    elif isinstance(res, dict):
        size = len(json.dumps(res, ensure_ascii=False, default=str))
        if size > _LARGE_RES_KB * 1024:
            st.caption(f"{label}（{size // 1024} KB，已折叠）")
            with st.expander("查看完整内容"):
                st.json(res)
        else:
            st.json(res)
    else:
        st.code(str(res))


def _render_dp_calls(node_dir: Path, expand_target=None) -> None:
    """DataProvider 调用列表：新格式目录 + 旧格式平铺 json。

    expand_target = 检查点 payload（kind=dp 且 dir 第三段 == 目录名时展开 + ⏸ 前缀）。
    未发生调用时不渲染占位（配合自动刷新呈现"生长"效果）。
    """
    calls = L.list_dp_calls(node_dir)
    if not calls:
        return

    target_dp = None
    if expand_target and expand_target.get("kind") == "dp":
        target_dp = expand_target.get("dir", "").rsplit("/", 1)[-1]

    for kind, path in calls:
        if kind == "new":
            meta = L.read_json(path / "meta.json") or {}
            name = meta.get("name") or path.name
            desc = meta.get("desc", "")
            title = f"{path.name}　{name}" + (f" — {desc}" if desc else "")
            is_target = target_dp is not None and path.name == target_dp
            with st.expander(f"{'⏸ ' if is_target else ''}{title}", expanded=is_target):
                req_path = path / "req.json"
                if req_path.exists():
                    req = L.read_json(req_path)
                    if req is None:
                        st.warning("req.json 解析失败，以下为原始内容")
                        st.code(L.read_text(req_path) or "(空)")
                    else:
                        st.json(req)
                else:
                    st.info("req.json 未生成")

                res_file = meta.get("res", "res.md")
                res_path = path / res_file
                if res_path.exists():
                    res = L.read_json(res_path) if res_file.endswith(".json") \
                        else L.read_text(res_path)
                    if res is None:
                        st.warning(f"{res_file} 解析失败，以下为原始内容")
                        st.code(L.read_text(res_path) or "(空)")
                    else:
                        _render_res(res, res_file)
                else:
                    st.info(f"{res_file} 未生成（调用可能未完成）")
                st.caption(f"seq={meta.get('seq')}  ts={meta.get('ts', '')}")
                _render_tushare_calls(path)
        else:
            payload = L.parse_legacy(path)
            if payload is None:
                st.warning(f"{path.name}：JSON 解析失败，以下为原始内容")
                st.code(L.read_text(path) or "(空)")
                continue
            title = f"⚠️ 旧格式　{path.name}　{payload.get('name', '')}"
            desc = payload.get("desc", "")
            if desc:
                title += f" — {desc}"
            with st.expander(title):
                req = payload.get("req")
                if req is not None:
                    st.json(req)
                res = payload.get("res")
                if res is None:
                    st.info("无返回结果")
                else:
                    _render_res(res)


def _render_tushare_calls(dp_dir: Path) -> None:
    """DP 调用内的 tushare 端点子调用（tushare/{seq:03d}_{api_name}/），
    嵌套在 dataprovider 展开内。未发生调用时不渲染占位（"生长"约定）。"""
    calls = L.list_tushare_calls(dp_dir)
    if not calls:
        return

    with st.expander(f"tushare（{len(calls)} 次端点调用）"):
        for tdir in calls:
            meta = L.read_json(tdir / "meta.json") or {}
            name = meta.get("name") or tdir.name
            title = f"{tdir.name}　{name}"
            if meta.get("probe"):
                title += "　🔌 连通性探测"
            if meta.get("error"):
                title += "　⚠️ 调用异常"
            with st.expander(title):
                req_path = tdir / "req.json"
                if req_path.exists():
                    req = L.read_json(req_path)
                    if req is None:
                        st.warning("req.json 解析失败，以下为原始内容")
                        st.code(L.read_text(req_path) or "(空)")
                    else:
                        st.json(req)
                else:
                    st.info("req.json 未生成")

                res_path = tdir / "res.json"
                if res_path.exists():
                    res = L.read_json(res_path)
                    if res is None:
                        st.warning("res.json 解析失败，以下为原始内容")
                        st.code(L.read_text(res_path) or "(空)")
                    else:
                        _render_res(res, "res.json")
                else:
                    st.info("res.json 未生成（调用可能未完成）")
                st.caption(f"seq={meta.get('seq')}  ts={meta.get('ts', '')}")


def _render_tools(node_dir: Path) -> None:
    """LangChain 工具调用列表（tools/{seq:03d}_{tool}/）。未发生调用时不渲染占位。"""
    tools = L.list_tools(node_dir)
    if not tools:
        return

    for tdir in tools:
        with st.expander(tdir.name):
            req = L.read_json(tdir / "req.json")
            if req is not None:
                st.json(req)
            out = L.read_text(tdir / "res.txt")
            if out is None:
                st.info("res.txt 未生成（调用可能未完成）")
            else:
                st.code(out)


def _render_llm_req(node_dir: Path, expand_target=None) -> None:
    """LLM 请求上下文（时间顺序第二段，DP 预取之后）。未生成时不渲染占位。

    expand_target 的 kind=llm_req 时展开 + ⏸。
    """
    kind = expand_target.get("kind") if expand_target else None
    req = L.read_text(node_dir / "req.md")
    if req:
        with st.expander("⏸ req.md（提示词）" if kind == "llm_req" else "req.md（提示词）",
                         expanded=(kind == "llm_req")):
            st.markdown(req)


def _render_llm_res(node_dir: Path) -> None:
    """LLM 输出（时间顺序末段前）。未生成时不渲染占位（调用未完成）。"""
    res = L.read_text(node_dir / "res.md")
    if res:
        with st.expander("res.md（节点输出）", expanded=True):
            st.markdown(res)


def _render_node_meta(node_dir: Path) -> None:
    """节点 meta.json（on_llm_end 最后写入，放在时序最末）。"""
    meta = L.read_json(node_dir / "meta.json")
    if meta:
        with st.expander("meta.json"):
            st.json(meta)


def _render_reports(run_dir: Path) -> None:
    """run 级 reports/ 报告文件"""
    files = L.list_report_files(run_dir)
    if not files:
        st.info("该批次无运行报告")
        return

    for f in files:
        if f.suffix == ".md":
            text = L.read_text(f)
            if text is not None:
                with st.expander(f.name):
                    st.markdown(text)
        elif f.suffix == ".json":
            data = L.read_json(f)
            if data is not None:
                with st.expander(f.name):
                    st.json(data)
        elif f.suffix == ".html":
            # 图表类 HTML（如板块层热力图）：plotly 自包含文件，iframe 内嵌渲染
            text = L.read_text(f)
            if text is not None:
                with st.expander(f.name):
                    st.components.v1.html(text, height=720, scrolling=True)
        else:
            text = L.read_text(f)
            if text is not None:
                with st.expander(f.name):
                    st.code(text)


def _render_timeline(run_dir: Path) -> None:
    """layer 大展开层级：layer（固定执行序）→ LLM 节点（seq 序 = 调用时序）
    → 节点内嵌 DP/tools 展开。检查点目标链自动展开并加「⏸」前缀。"""
    cp = None
    found = st.session_state.get("step_checkpoint")
    if found:
        cp_run, cp_payload = found
        if cp_run.name == run_dir.name:
            cp = cp_payload

    target_layer = cp.get("layer") if cp else None
    target_dir = cp.get("dir", "") if cp else ""

    for layer_dir in L.sort_layers(L.list_layers(run_dir)):
        is_layer_target = bool(cp) and layer_dir.name == target_layer
        with st.expander(f"{'⏸ ' if is_layer_target else ''}{layer_dir.name}",
                         expanded=is_layer_target):
            for node_dir in L.list_nodes(layer_dir):
                # 段边界匹配：防节点名互为字符串前缀时的误展开
                node_rel = f"{layer_dir.name}/{node_dir.name}"
                is_node_target = bool(cp) and (
                    target_dir == node_rel or target_dir.startswith(node_rel + "/"))
                with st.expander(f"{'⏸ ' if is_node_target else ''}{node_dir.name}",
                                 expanded=is_node_target):
                    node_cp = cp if is_node_target else None
                    # 节点展开内按调用时间顺序：DP 预取（板块层节点先取数据再组装
                    # prompt）→ LLM 请求 → 工具调用 → LLM 输出 → meta
                    #（on_llm_end 最后写入）
                    _render_dp_calls(node_dir, expand_target=node_cp)
                    _render_llm_req(node_dir, expand_target=node_cp)
                    _render_tools(node_dir)
                    _render_llm_res(node_dir)
                    _render_node_meta(node_dir)


# ---- 调试步进 ----

_KIND_LABELS = {"dp": "DP 响应", "llm_req": "LLM 调用前", "llm_res": "节点 res"}
_STATUS_LABELS = {
    "waiting": "⏸ 等待页面确认",
    "confirmed": "✅ 已确认",
    "skip_all": "⏭ 已跳过全部（后续检查点自动放行）",
}


def _write_json_atomic(path: Path, payload: dict) -> None:
    """原子写：temp + rename，防后端轮询读到半截文件。

    tmp 名带进程号后缀：与后端进程（及多标签页）共用同一检查点文件，
    共用同一 tmp 名会并发交错，各写各的 tmp 再 rename 保证原子性。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _set_checkpoint_status(run_dir: Path, status: str, seen_seq) -> None:
    """写回确认状态。stale 防护：文件 seq 与用户看到的渲染不一致时不写
    （防双击/多标签页把确认写进下一个检查点，未看内容即被放行）"""
    payload = L.find_checkpoint(run_dir)
    if payload is None or payload.get("status") != "waiting":
        return
    if seen_seq is not None and payload.get("seq") != seen_seq:
        return
    payload["status"] = status
    _write_json_atomic(run_dir / L.CHECKPOINT_FILE, payload)


def _logs_fingerprint(run_dir: Path) -> str:
    """日志树指纹（文件总数 + 最新文件 mtime_ns）：分析运行中内容增长时变化。"""
    newest_ns = 0
    count = 0
    if run_dir.is_dir():
        for p in run_dir.rglob("*"):
            if p.is_file():
                count += 1
                try:
                    newest_ns = max(newest_ns, p.stat().st_mtime_ns)
                except OSError:
                    pass
    return f"{count}:{newest_ns}"


@st.fragment(run_every="1s")
def _render_step_bar(root: Path) -> None:
    """调试步进按钮区 + 日志生长观察者（1s 轮询）：

    - 检查点文件 waiting 时显示【✅ 下一步】/【⏭ 跳过全部】；仅当
      (run, seq, status) 变化时更新 session_state 并触发全页 rerun（防循环）；
      stale 防护沿用 session_state["step_seen_seq"] 比对。
    - 当前 run 日志树指纹变化（分析进程正在写入）→ 全页刷新，
      主区展开内容随之"生长"，未发生的内容不占位。"""
    run_name = st.session_state.get("run_name")
    if run_name:
        fp = _logs_fingerprint(root / run_name)
        if fp != st.session_state.get("logs_fingerprint"):
            st.session_state["logs_fingerprint"] = fp
            st.rerun(scope="app")

    found = L.find_active_checkpoint(root)
    sig = (found[0].name, found[1].get("seq"), found[1].get("status")) if found else None
    if sig != st.session_state.get("step_checkpoint_sig"):
        st.session_state["step_checkpoint"] = found
        st.session_state["step_checkpoint_sig"] = sig
        # 任何 sig 变化（含检查点消失 → None）都全页重渲，让时序展开态同步；
        # 首载无检查点时 sig(None) == 初始(None)，不会触发 rerun
        st.rerun(scope="app")

    if found is None:
        st.caption(
            "无调试步进检查点。开启方式：运行分析前设置环境变量 "
            "`LIVEPROFIT_DEBUG_STEP=true`，CLI 分析进程将在每个 DP 响应 / "
            "LLM 调用前 / 节点 res 处暂停，等待本页确认后继续。"
        )
        return

    run_dir, payload = found
    status = payload.get("status", "waiting")
    selected_run = st.session_state.get("run_name", "")
    if status != "waiting" and run_dir.name != selected_run:
        # 非当前批次的已完成检查点（confirmed/skip_all 残留）：历史状态，
        # 不展示——否则新批次自动切换后、到达首个检查点前会短暂出现
        # "检查点属于其他批次"的误导提示
        return
    kind = payload.get("kind", "?")
    seq = payload.get("seq")
    seen_seq = st.session_state.get("step_seen_seq")

    # 左列状态信息、右列按钮（紧贴 tabs 下方右侧，两个按钮横排）
    info_col, btn_col = st.columns([0.62, 0.38], vertical_alignment="center")
    with info_col:
        st.markdown(
            f"**{_STATUS_LABELS.get(status, status)}**　"
            f"`#{seq if seq is not None else '?'}`　"
            f"**{_KIND_LABELS.get(kind, kind)}**　"
            f"{payload.get('ts', '')}"
        )
        st.caption(
            f"layer={payload.get('layer', '?')}　node={payload.get('node', '?')}　"
            f"name={payload.get('name', '?')}"
        )
        if run_dir.name != selected_run:
            st.caption(
                f"⚠️ 检查点属于批次 {run_dir.name}（当前查看 {selected_run}），"
                "确认/跳过仍对该批次生效"
            )
        if status == "skip_all":
            st.caption("后端后续检查点将自动放行，不再写入本文件。")
    with btn_col:
        if status == "waiting":
            b1, b2 = st.columns(2)
            with b1:
                if st.button("✅ 下一步", key="step_confirm"):
                    _set_checkpoint_status(run_dir, "confirmed", seen_seq)
            with b2:
                if st.button("⏭ 跳过全部", key="step_skip_all"):
                    _set_checkpoint_status(run_dir, "skip_all", seen_seq)

    # 记录本次渲染的 seq，作为下一次点击的 stale 比对基准
    st.session_state["step_seen_seq"] = seq


# ---- Sidebar：run 选择（5s 自动刷新） ----

@st.fragment(run_every="5s")
def _sidebar_run_selector(root: Path) -> None:
    """run 批次选择：每 5s 自动刷新目录列表（无手动刷新按钮）。

    - 新批次出现 → 自动切换到最新 run（覆盖当前选中）；
    - 空目录时 st.stop() 终止整个脚本（fragment 内的 st.stop 同样终止全脚本）；
    - 用户手动改选中时 st.rerun(scope="app") 触发全页重渲
      （fragment 内交互只重跑 fragment，必须显式全页刷新）。"""
    run_names = [p.name for p in L.list_runs(root)]
    if not run_names:
        st.sidebar.info("logs/ 下暂无运行记录")
        st.stop()
    newest = run_names[0]   # list_runs 最新在前
    seen = st.session_state.get("run_newest_seen")
    if seen is None:
        st.session_state["run_newest_seen"] = newest
    elif newest != seen:
        # 新批次出现：程序化把 selectbox 与选中值都切到最新
        st.session_state["run_select"] = newest
        st.session_state["run_name"] = newest
        st.session_state["run_newest_seen"] = newest
        st.rerun(scope="app")
    selected = st.sidebar.selectbox("运行批次", run_names, index=0, key="run_select")
    prev = st.session_state.get("run_name")
    if selected != prev:
        st.session_state["run_name"] = selected
        # 首次加载（prev 为 None）无需 rerun：主 body 在 fragment 之后读
        # session_state 即拿到最新值；交互改选中才需要全页重渲
        if prev is not None:
            st.rerun(scope="app")


root = L.logs_root()

st.sidebar.title("LiveProfit 日志")
st.sidebar.caption(str(root))
_sidebar_run_selector(root)

run_name = st.session_state.get("run_name")
if not run_name:
    st.info("没有找到运行日志。请先跑一次分析，或通过环境变量 LIVEPROFIT_LOGS_DIR 指定日志目录。")
    st.stop()
run_dir = root / run_name

# ---- 主区：sticky tabs + 内容 ----
tab_seq, tab_reports = st.tabs(["调用时序", "运行报告"])
with tab_seq:
    # 调试步进按钮区：调用时序 tab 内容顶部右侧（1s 轮询）
    _render_step_bar(root)
    _render_timeline(run_dir)
with tab_reports:
    _render_reports(run_dir)
