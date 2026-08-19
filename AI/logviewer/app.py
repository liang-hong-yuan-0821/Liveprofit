"""
LiveProfit 日志查看器
浏览 logs/{时间戳}/ 下的运行日志：LLM 节点上下文、dataprovider 调用、工具调用、运行报告。
兼容历史旧格式 dataprovider 日志（{接口名}.json，标记"旧格式"）。

运行：streamlit run AI/logviewer/app.py   （项目根目录执行）
"""

from pathlib import Path

import streamlit as st

from AI.logviewer import logs_reader as L

st.set_page_config(page_title="LiveProfit 日志查看器", layout="wide")

# 结果文本超过该字节数时折叠展示（防超大 markdown 拖慢布局）
_LARGE_RES_KB = 100


@st.cache_data(ttl=5)
def _cached_run_names(root: str) -> list:
    """只缓存批次目录名列表（ttl 5s + 手动刷新）；文件内容不缓存，rerun 即最新"""
    return [p.name for p in L.list_runs(Path(root))]


def _render_res(res, label: str = "res") -> None:
    """按类型渲染调用结果：str → markdown，dict → json，超大文本折叠"""
    if isinstance(res, str):
        if len(res) > _LARGE_RES_KB * 1024:
            st.caption(f"{label}（{len(res) // 1024} KB，已折叠）")
            with st.expander("查看完整内容"):
                st.markdown(res)
        else:
            st.markdown(res)
    elif isinstance(res, dict):
        st.json(res)
    else:
        st.code(str(res))


def _render_dp_calls(node_dir: Path) -> None:
    """DataProvider 调用列表：新格式目录 + 旧格式平铺 json"""
    calls = L.list_dp_calls(node_dir)
    if not calls:
        st.info("该节点无 dataprovider 调用记录")
        return

    for kind, path in calls:
        if kind == "new":
            meta = L.read_json(path / "meta.json") or {}
            name = meta.get("name") or path.name
            desc = meta.get("desc", "")
            title = f"{path.name}　{name}" + (f" — {desc}" if desc else "")
            with st.expander(title):
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


def _render_tools(node_dir: Path) -> None:
    """LangChain 工具调用列表（tools/{seq:03d}_{tool}/）"""
    tools = L.list_tools(node_dir)
    if not tools:
        st.info("该节点无工具调用记录")
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


def _render_node_context(node_dir: Path) -> None:
    """节点上下文：meta.json + req.md + res.md"""
    meta = L.read_json(node_dir / "meta.json")
    if meta:
        with st.expander("meta.json"):
            st.json(meta)
    req = L.read_text(node_dir / "req.md")
    if req:
        with st.expander("req.md（提示词）", expanded=False):
            st.markdown(req)
    else:
        st.info("req.md 未生成")
    res = L.read_text(node_dir / "res.md")
    if res:
        with st.expander("res.md（节点输出）", expanded=True):
            st.markdown(res)
    else:
        st.info("res.md 未生成（调用可能未完成）")


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
        else:
            text = L.read_text(f)
            if text is not None:
                with st.expander(f.name):
                    st.code(text)


# ---- Sidebar：run → layer → 节点 ----
root = L.logs_root()
run_names = _cached_run_names(str(root))

st.sidebar.title("LiveProfit 日志")
st.sidebar.caption(str(root))
if st.sidebar.button("刷新目录列表"):
    _cached_run_names.clear()
    st.rerun()

if not run_names:
    st.sidebar.info("logs/ 下暂无运行记录")
    st.info("没有找到运行日志。请先跑一次分析，或通过环境变量 LIVEPROFIT_LOGS_DIR 指定日志目录。")
    st.stop()

run_name = st.sidebar.selectbox("运行批次", run_names, index=0)
run_dir = root / run_name

layers = L.list_layers(run_dir)
layer_names = [p.name for p in layers]
node_dir: Path | None = None
if layer_names:
    # key 带上批次名：切换批次后自动回到默认选中项
    layer_name = st.sidebar.selectbox(
        "层级", layer_names, index=0, key=f"layer_{run_name}")
    layer_dir = run_dir / layer_name
    nodes = L.list_nodes(layer_dir)
    if nodes:
        node_name = st.sidebar.selectbox(
            "节点", [p.name for p in nodes], index=0, key=f"node_{run_name}")
        node_dir = run_dir / layer_name / node_name
    else:
        st.sidebar.info("该层级下暂无节点目录")
else:
    st.sidebar.info("该批次暂无层级目录")

# ---- 主区 ----
tab_ctx, tab_dp, tab_tools, tab_reports = st.tabs(
    ["节点上下文", "DataProvider", "Tools", "运行报告"])

with tab_ctx:
    if node_dir:
        _render_node_context(node_dir)
    else:
        st.info("请先在左侧选择节点")

with tab_dp:
    if node_dir:
        _render_dp_calls(node_dir)
    else:
        st.info("请先在左侧选择节点")

with tab_tools:
    if node_dir:
        _render_tools(node_dir)
    else:
        st.info("请先在左侧选择节点")

with tab_reports:
    _render_reports(run_dir)
