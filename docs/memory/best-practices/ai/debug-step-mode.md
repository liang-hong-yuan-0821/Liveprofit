# 调试步进模式（Debug Step Mode）

> 一句话结论：`LIVEPROFIT_DEBUG_STEP=true` 开启，分析进程在 DP 响应/LLM 调用前/节点 res 三检查点暂停，由 streamlit logviewer 页面点【下一步】/【跳过全部】继续，两进程经 `.debug_checkpoint.json` 文件协调。

## 开启与暂停点

- 开启：运行分析前设 `LIVEPROFIT_DEBUG_STEP=true`（default_config → `debug_step`），分析进程在 **DP 响应 / LLM 调用前（on_llm_start，API 请求发出前）/ 节点 res** 三个检查点暂停。
- 页面确认：`streamlit run AI/logviewer/app.py` 的「调用时序」tab 内容区顶部右侧按钮区（1s 轮询）点【✅ 下一步】/【⏭ 跳过全部】后继续；无限等待无超时；检查点指向的 layer/node/DP 展开自动展开并加「⏸」前缀。

## 协调机制与坑

- 两个进程通过 `logs/{ts}/.debug_checkpoint.json` 文件协调（status: waiting → confirmed/skip_all），双方 temp+rename 原子写（tmp 名带 pid 后缀防并发交错）；文件名常量单点定义于 `AI/utils/step_gate.py`。
- 图外 LLM 调用（决策抽取 process_signal / Reflector）不产生检查点（节点名不在 `_NODE_LAYER` 表 → unknown 过滤）。
- 页面按钮有 stale 防护：比对 session_state 记存的渲染 seq 与文件当前 seq，不一致不写（防连点把确认写进下一个检查点）。
