# 日志查看器 UI 重构方案

> **状态**：已完成（2026-08-23）
> **进度**：全部完成（实现 + 测试 + Code Review 2 轮 PASS）
> **下一步**：—（已归档 docs/done/）
>
> **Code Review 记录**：第 1 轮 PASS（4 minor 全部修复：节点展开段边界匹配防前缀碰撞、检查点消失时同步全页重渲、首载去掉冗余 rerun、smoke 测试文档补充定时器说明）；第 2 轮 PASS 无新问题。
> **关联文档**：[数据接口日志目录化与日志查看器方案](../done/数据接口日志目录化与日志查看器方案.md) | CLAUDE.md「调试步进模式」节

---

## 一、背景与动机

### 1.1、现状

日志查看器（AI/logviewer/app.py，338 行）当前交互：

- sidebar：三级筛选（run → layer → 节点 selectbox）+ 「刷新目录列表」手动按钮（:281-283），run 目录列表仅靠 `@st.cache_data(ttl=5)` 兜底更新；
- 主区 5 个 tab（:312-313）：节点上下文 / DataProvider / Tools / 运行报告 / 调试步进。要查看某节点必须先在 sidebar 选 layer 再选节点，无法看到"layer → LLM → DP"的完整调用时序全貌。

### 1.2、目标

1. 目录列表**固定 5s 自动刷新**，删除手动刷新按钮；
2. 内容区取消原 tabs，改为嵌套大展开：**layer 大展开 → 内部按调用时序排列 LLM 节点展开 → 节点展开内嵌套 DP/tools 展开**，交互与现有 expander 一致；展开顺序 = 调用时序；
3. **运行报告保留为单独 tab**；调试步进不再是 tab，按钮移到 **sticky 顶栏 tabs 右侧 extra 区**（用户 2026-08-23："放在 tabs 的右侧 extra 区域，并且让 tabs 保持 fixed"）；
4. sidebar 取消 layer/node 筛选，仅保留 run 选择。

## 二、架构设计

```
主区顶栏行（sticky，CSS 注入固定）：st.columns([5,1], vertical_alignment="center")
 ├ 左：st.tabs(["调用时序", "运行报告"])      ← 调用时序为默认 tab（承载 layer 大展开）
 │     ├ 调用时序 tab：_render_timeline(run_dir, cp)   （layer 大展开层级）
 │     └ 运行报告 tab：_render_reports(run_dir)        （原函数复用）
 └ 右：_render_step_bar(root)  @st.fragment(run_every="1s")
       ← 检查点状态 + ✅ 下一步 / ⏭ 跳过全部

sidebar：@st.fragment(run_every="5s") 的 run selectbox（无刷新按钮、无 layer/node 筛选）
```

- 「调用时序」为拟定的默认 tab 名（承载 layer 大展开层级；用户可改名）。
- 宽度取舍：columns [5,1] 使主内容宽约 83%（streamlit 表格自带横向滚动兜底）；不采用按钮 absolute 定位到 tabs 行右上角方案（CSS 侵入深、跨版本脆弱）。
- 时序排序：layer 按固定执行序 market→sector→stock→screening（未知字母序附后，新 `L.sort_layers()`）；layer 内节点按全局 `{seq:03d}` 前缀（= 调用时序）；节点内 DP/tools 按各自 `{seq:03d}` 前缀。

## 三、详细设计

### 3.1 sidebar 5s 自动刷新（app.py）

#### 3.1.1 模块设计

```python
@st.fragment(run_every="5s")
def _sidebar_run_selector(root):
    run_names = [p.name for p in L.list_runs(root)]
    if not run_names:
        st.sidebar.info("logs/ 下暂无运行记录")
        st.stop()   # fragment 内的 st.stop 终止整个脚本，保持空目录提示行为
    selected = st.sidebar.selectbox("运行批次", run_names, index=0, key="run_select")
    if selected != st.session_state.get("run_name"):
        st.session_state["run_name"] = selected
        st.rerun(scope="app")   # 关键：fragment 内交互只重跑 fragment，须显式触发全页重渲
```

- 删除 `_cached_run_names`（:26-29）与「刷新目录列表」按钮（:281-283）；主脚本读 `st.session_state["run_name"]`，未设置回退最新批次（`L.list_runs` 最新在前，index=0 即最新）。
- selectbox key 固定：选项变化时按选中值保持（值仍在则不变，被删则回落 index=0=最新）。
- 定时器触发不整页闪烁（仅 diff 更新 fragment 块）；空目录行为与现有 test_app_smoke_no_logs 一致。

#### 3.1.2 三方依赖能力评估

- streamlit 1.61.1（uv.lock 锁定，pyproject `>=1.40`）：`@st.fragment(run_every=...)`（≥1.37）与 fragment 内 `st.rerun(scope="app")` 均受支持；app.py:204 已有 fragment 先例、:286-288 已有 info+st.stop 先例。
- AppTest 不触发 run_every 定时器——测试用 `at.run()` 手动再跑模拟。

#### 3.1.3 风险与验证方式

- 风险：sidebar 内 fragment 异常 → fallback：退化为整个 sidebar 非 fragment 模式（每次全页 rerun）。
- 验证：smoke 测试 + 手动观察 5s 刷新。

#### 3.1.4 文件变更清单

| 路径 | 改动 |
|------|------|
| `AI/logviewer/app.py` | sidebar 改为 fragment 自动刷新；删 `_cached_run_names` 与刷新按钮、layer/node selectbox |

### 3.2 sticky 顶栏（CSS 注入）

#### 3.2.1 模块设计

`st.markdown(unsafe_allow_html=True)` 注入：

```css
/* 主方案：整行（tabs + 按钮列）sticky */
div[data-testid="stHorizontalBlock"]:has([data-testid="stTabs"]) {
  position: sticky; top: 0; z-index: 1000;
  background: var(--background-color);
  padding-top: 4px; padding-bottom: 4px;
  border-bottom: 1px solid rgba(128,128,128,0.25);
}
/* 兜底：tabs 组件自身也 sticky（若整行选择器未命中） */
div[data-testid="stTabs"] { position: sticky; top: 0; z-index: 999; background: var(--background-color); }
```

#### 3.2.2 三方依赖能力评估

- Streamlit 1.61 DOM：`st.columns` → `div[data-testid="stHorizontalBlock"]`，`st.tabs` → `div[data-testid="stTabs"]`；主滚动容器内 sticky 正常工作。
- `:has()` 选择器：Chrome 105+/Edge 105+/Firefox 121+/Safari 15.4+ 均支持；保留无 `:has()` 的兜底选择器。
- 背景必须用 `var(--background-color)`（1.61 主题变量），否则滚动内容透出。

#### 3.2.3 风险与验证方式

- 风险：streamlit 版本升级导致 DOM testid 变化 → 选择器失效仅体验降级（顶栏随滚动），功能不受影响。实现期用浏览器 devtools 实测 DOM 后固化选择器。
- 验证：AppTest 无法验证 CSS → 手动 `streamlit run` 滚动验证。

#### 3.2.4 文件变更清单

| 路径 | 改动 |
|------|------|
| `AI/logviewer/app.py` | 注入 sticky CSS；顶栏行 columns 布局 |

### 3.3 时序渲染 `_render_timeline(run_dir, cp)`（app.py）

#### 3.3.1 模块设计

```
for layer_dir in L.sort_layers(L.list_layers(run_dir)):
    with st.expander("⏸ " + layer_dir.name if 检查点目标层 else layer_dir.name, expanded=目标层):
        for node_dir in L.list_nodes(layer_dir):          # {seq:03d} 序 = 调用时序
            with st.expander("⏸ " + node_dir.name if 目标节点 else node_dir.name, expanded=目标节点):
                # node expander 标签 = 目录名 {seq:03d}_{Node}（与 checkpoint dir 第二段字符串匹配）
                _render_node_context(node_dir, expand_target=cp)   # meta 收起 / req 收起 / res 展开
                _render_dp_calls(node_dir, expand_target=cp)       # 每个调用一个 expander（新旧格式兼容）
                _render_tools(node_dir)                            # 不变
```

- `_render_node_context` / `_render_dp_calls` 增加可选参数 `expand_target`（None 时行为与现状完全一致）。
- `_render_tools`、`_render_reports`、`_write_json_atomic`、`_set_checkpoint_status`、`_KIND_LABELS`/`_STATUS_LABELS` 原样保留。

#### 3.3.2 三方依赖能力评估

本模块不依赖外部库/API（streamlit 原生 expander，无自定义组件）。

#### 3.3.3 风险与验证方式

- 风险：中断 run 缺 res.md/req.md → 现有 `st.info` 提示路径保留。
- 验证：smoke 测试（layer 顺序、检查点展开目标）。

#### 3.3.4 文件变更清单

| 路径 | 改动 |
|------|------|
| `AI/logviewer/app.py` | 新增 `_render_timeline`；`_render_node_context`/`_render_dp_calls` 加 `expand_target` 参数；删除原 5 tab 结构（运行报告 tab 保留） |

### 3.4 检查点驱动的展开链 + sticky 按钮区（替换原「调试步进」tab）

#### 3.4.1 模块设计

**`_render_step_bar(root)`**（`@st.fragment(run_every="1s")`，替换 `_render_debug_step` :204-272）：

- `L.find_active_checkpoint(root)` 逻辑不变；仅当 sig=(run_name, seq, status) 变化时更新 `st.session_state["step_checkpoint"]` 并 `st.rerun(scope="app")`（防循环：等待期间 sig 不变，仅 backend 写入下一个检查点时触发一次全页 rerun）。
- 显示状态行 + 所属批次 caption + 「✅ 下一步」/「⏭ 跳过全部」（**「✅ 下一步」由现「✅ 确认下一步」改名**，按用户 2026-08-23 要求；key=`step_confirm`/`step_skip_all` 不变；stale 防护沿用 `step_seen_seq` 比对与 `_set_checkpoint_status`；收尾时 CLAUDE.md:145 同步用新标签）。
- 写回目标 run_dir 来自 found（跨 run 时自动写对批次）。

**展开目标映射**（时序 tab 侧读 `st.session_state["step_checkpoint"]`）：payload.dir = `"{layer}/{seq:03d}_{Node}[/{seq:03d}_{接口}]"`，其中 layer = 目录名 market/sector/stock/screening，**非字面 "layer/" 前缀**（与 dataprovider_log.py:82 `rel.split("/")[0]` 取 layer、step_gate 协议、既有测试 fixture `market/001_.../001_get_x` 两段/三段格式兼容）：

- dir 首段匹配的 layer expander、第二段匹配的 node expander 加「⏸」前缀且 expanded=True；
- kind=dp → dir 第三段字符串 == DP 目录名 `{seq:03d}_{接口}`（DP expander 标签沿用现有 `_render_dp_calls` 的 title，含目录名，按字符串匹配）的 DP expander 展开，其 res.md 内容即原「检查点内容」展示；
- kind=llm_req → node 内 req expander 展开；kind=llm_res → res 已默认展开。

**跨 run**：检查点所属 run ≠ 选中 run → 展开目标全空，按钮区 caption 显示「⚠️ 检查点属于批次 {X}（当前查看 {Y}），确认/跳过仍对该批次生效」，按钮保持可用。

#### 3.4.2 三方依赖能力评估

streamlit 1.61.1：fragment 内 `st.rerun(scope="app")` 受支持（1.37 引入）；fragments 在 columns 内可用。

#### 3.4.3 风险与验证方式

- 风险：fragment 内 rerun 循环 → sig 变化才 rerun 的防循环已设计；AppTest 不触发定时器 → 检查点变化模拟靠 `at.run()` 手动重跑。
- 验证：smoke 测试（按钮 key 不变、stale 防护、kind=dp 展开目标）+ 手动开 `LIVEPROFIT_DEBUG_STEP=true` 走一遍检查点链。

#### 3.4.4 文件变更清单

| 路径 | 改动 |
|------|------|
| `AI/logviewer/app.py` | 删除 `_render_debug_step`，新增 `_render_step_bar`；时序渲染接入展开目标 |

### 3.5 logs_reader.py 新增 `sort_layers`

#### 3.5.1 模块设计

```python
LAYER_ORDER = ["market", "sector", "stock", "screening"]   # 真实执行序

def sort_layers(layers):
    """已知 layer 按固定执行序（market→sector→stock→screening），未知 layer 字母序附后"""
    return sorted(layers, key=lambda p: (LAYER_ORDER.index(p.name)
                  if p.name in LAYER_ORDER else len(LAYER_ORDER), p.name))
```

`list_layers` 保持字母序不动（排序职责放 UI 层，纯函数可单测）；node/DP/tools 的 `{seq:03d}` 前缀排序已满足调用时序，不变。

#### 3.5.2 文件变更清单

| 路径 | 改动 |
|------|------|
| `AI/logviewer/logs_reader.py` | 新增 `LAYER_ORDER` + `sort_layers` |

## 四、已确认决策

- 目录 5s 固定自动刷新、删手动刷新按钮——用户 2026-08-23
- 内容区取消原 tabs（节点上下文/DataProvider/Tools/调试步进），改 layer→LLM→DP 大展开嵌套，顺序=调用时序——用户 2026-08-23
- 运行报告保留单独 tab——用户 2026-08-23
- 调试步进按钮放 sticky 顶栏 tabs 右侧 extra 区、顶栏 fixed——用户 2026-08-23
- sidebar 取消 layer/node 筛选，仅留 run 选择——用户 2026-08-23
- 按钮文案「✅ 下一步」（由「✅ 确认下一步」改名，key 不变）

## 五、测试与验证

### 测试清单

**`tests/utils/test_logviewer_smoke.py`**（改造）：

- fixture 增补：sector/stock/screening 三个 layer 目录各含一个节点；**另建一个较旧 run 目录**（含其自身节点的 waiting 检查点 payload，dir 指向该 run 自身节点）供跨 run 用例。
- 保留：`test_app_smoke`、`test_app_smoke_no_logs`、`test_step_tab_confirm` / `test_step_tab_skip_all` / `test_step_tab_stale_seq_no_write`（按钮 key 不变，AppTest 按 key 定位不受位置影响）。
- 新增：`test_tabs_labels`（== ["调用时序", "运行报告"]）、`test_sidebar_only_run_select`（无 layer/node selectbox）、`test_timeline_layer_order`（expander 顺序 == market→sector→stock→screening）、`test_checkpoint_expands_target`（waiting 检查点 → 对应 layer/node expander 标题含「⏸」）、`test_checkpoint_expands_dp`（kind=dp 检查点 → 对应 DP expander（fixture 001_get_x）展开）、`test_checkpoint_other_run_hint`（跨 run 提示 + 按钮仍可用）。

**`tests/utils/test_logs_reader.py`**（增补）：`test_sort_layers_order`（固定序 + 未知 layer 字母序附后）。

### 验证方式

- `pytest tests/utils` 全绿
- 手动验证（AppTest 无法验证 CSS/定时器）：`streamlit run AI/logviewer/app.py` — devtools 固化 sticky CSS 选择器、滚动验证 fixed、观察 5s 目录刷新、开 `LIVEPROFIT_DEBUG_STEP=true` 走一遍检查点展开链

## 六、主干文档同步（实现完成后）

| 文件 | 改动 |
|------|------|
| `CLAUDE.md`（:145） | 「调试步进」tab 描述改为「sticky 顶栏右侧按钮区（1s 轮询，固定在顶栏 tabs 右侧）点【✅ 下一步】/【⏭ 跳过全部】」 |
| `docs/板块层.md` | 由后端方案同步（见《板块层接口逐日数据增强方案》） |
| 本方案 | 状态「已完成」后移入 `docs/done/` 归档 |
