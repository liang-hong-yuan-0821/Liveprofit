# Tushare 接口调用日志方案（dataprovider 展开内嵌套 tushare 展开）

> **状态**：已完成（2026-08-25，已归档 docs/done/）
> **进度**：7/7 步骤（实现 + 测试 + Code Review PASS + 6 minor 修复 + 归档）
> **下一步**：—

## 背景动机

`@dataprovider_log`（AI/utils/dataprovider_log.py）在 interface 层记录每次 DP 接口调用
（`{seq:03d}_{接口名}/`），但看不到 provider 内部对 tushare API 端点的实际调用
（`self.api.daily(...)` 等的参数与原始返回 DataFrame）。排查代理端点数据问题时
（如 2026-08-23 日线接口降序踩坑）看不到原始请求/响应。

## 设计决策（已评审：独立 agent 评审 PASS）

### 磁盘格式

```
{node_dir}/{seq:03d}_{接口名}/            ← 现有 DP 调用目录
    req.json / res.md|json / meta.json     ← 现有
    tushare/                               ← 新增（仅 tushare 数据源 + run 内有 DP 上下文）
        {seq:03d}_{api_name}/              ← 每次端点调用一个目录（seq 按 DP 调用独立计数）
            req.json    ← {"api_name", "fields", "params"}
            res.json    ← DataFrame → {"columns","shape","records","row_count","truncated"}
                          异常 → {"error", "api_name"}
            meta.json   ← {"name","seq","ts","res","probe","error?"}
```

### 捕获点：包装 DataApi.query（tushare 库唯一网络出口）

- `wrap_tushare_api(api)`（AI/utils/dataprovider_log.py）：实例属性覆盖 `api.query`
  （tushare 库 `__getattr__` 返回 `partial(self.query, name)`，属性访问时求值，
  包装先于任何 `self.api.xxx` 访问即可全量拦截）
- **幂等/探测标志判定必须查实例 `__dict__`**（2026-08-25 踩坑）：真实 DataApi 的
  `__getattr__` 对任意未知属性返回 truthy 的 `partial`，`getattr` 判定会误判"已包装"
  导致完全不落盘
- **contextvars 不自动传播进 ThreadPoolExecutor worker**（2026-08-25 实测，
  Py3.12.10）：`_run_with_timeout` 改为 `contextvars.copy_context()` + `ctx.run` 显式带入
- 连通性探测（`_connect` 的 stock_basic limit=1）：`api._lp_probe_next` 一次性标志 →
  meta.probe=true → viewer「🔌 连通性探测」角标
- 超时补写：FutureTimeout 后 worker 仍补写日志（ctx 已复制），「DP res 超时、tushare 有记录」
  并存属预期诊断行为
- `LIVEPROFIT_TUSHARE_LOG=0` 关闭子日志；单次结果 records 超 500 行截断
  （truncated + row_count 元数据）
- trading_calendar.py 独立实例同样接线（全仓唯二 `ts.pro_api()` 入口；同步调用无线程问题）

### dataprovider_log.py 改造

- `_current_dp_call` contextvar（`_DpCallContext`：dir + tushare seq 计数器）
- `_write` 拆 `_prepare_call`（fn 前建目录 + req.json）/ `_finalize_call`（res + meta）；
  保留 `_write` 旧签名兼容既有测试与调用方
- wrapper：prepare → set ctx → fn（finally reset）→ finalize → 步进检查点（仅成功路径）
- fn 抛异常：目录保留 req.json + res.json({"error"}) + meta.error，异常原样上抛

### 日志查看器

- logs_reader.py：`list_tushare_calls(dp_dir)`（`tushare/` 下 `_SEQ_DIR_RE` 目录）
- app.py：`_render_dp_calls` "new" 分支末尾 `_render_tushare_calls(path)`：
  `tushare（N 次端点调用）` 展开 → 每调用子展开（标题含 probe/error 角标）→ req + res
- `_render_res` dict 分支补折叠（>100KB 折叠为 caption + 嵌套 expander）

## 文件变更清单

| 文件 | 改动 |
|---|---|
| AI/utils/dataprovider_log.py | `_DpCallContext` + `_current_dp_call`、`_prepare_call`/`_finalize_call`（保留 `_write`）、`wrap_tushare_api`/`_write_tushare_call`/`_norm_tushare_res`、docstring |
| AI/dataflows/providers/tushare_provider.py | `_connect` 内 `wrap_tushare_api` + `_lp_probe_next`；`_run_with_timeout` 显式 copy_context；import contextvars |
| AI/dataflows/utils/trading_calendar.py | `_fetch_tushare_calendar` 内 `wrap_tushare_api(api)` + import |
| AI/logviewer/logs_reader.py | `list_tushare_calls`、docstring |
| AI/logviewer/app.py | `_render_tushare_calls`、`_render_res` dict 折叠、docstring |
| AI/utils/llm_callbacks.py | docstring 目录约定 |
| tests/utils/test_tushare_call_log.py | 新增 15 个测试函数（含 _DataApiLike 回归、超时补写回归） |
| tests/utils/test_logs_reader.py | `test_list_tushare_calls` + fixture tushare 树 |
| tests/utils/test_logviewer_smoke.py | fixture tushare 树 + `test_tushare_expander_inside_dp` |

## Code Review 结论与修复

subagent code review verdict：**PASS**（无 blocker/major），6 条 minor/nit 已修复：

1. 补超时补写/copy_context 回归测试 `test_timeout_late_write_attribution`
   （经 `_api_call` 假实例路径，覆盖「超时返回 None 后 worker 补写、归属正确」）
2. `_DpCallContext.next_tushare` 加 `threading.Lock`（超时 worker 与新 worker 并发递增
   可致 seq 碰撞覆盖，原注释「每 ctx 至多一个存活」不成立）
3. `_norm_tushare_res` 加 `default_handler=str`（±inf 防整批 records 丢失）+ getattr
   兜底（Series）+ 归一失败写 `{"error"}` 保持目录完整
4. 方案文档用例计数 13 → 15（纯文档误差）
5. probe 标志在 run 外被静默消费：行为符合设计（run 外探测不落盘），不改
6. `api.query = logged_query` 赋值与 `_lp_tushare_logged` 同包 try/except

## 验证结果

- 单测：tests/utils + tests/dataflows + tests/graph + tests/templates + tests/event_study
  共 225 passed（无真实 LLM/tushare 依赖）
- 真实集成：真实 TushareProvider + 代理端点，DP 调用目录下 `tushare/001_stock_basic/`
  （probe=true 角标）、`tushare/001_daily/` 落盘，req/res/meta 字段正确
- logviewer AppTest（真实生成日志树）：`tushare（2 次端点调用）` 展开 +
  `001_stock_basic　stock_basic　🔌 连通性探测` 子展开渲染正常

## 向后兼容

- 旧 run 无 `tushare/` 目录 → 查看器不渲染该层（"生长"约定天然兼容）
- `_write(name, doc, req, res)` 旧签名保留；成功路径落盘与步进检查点行为不变
- 非 tushare 数据源（akshare）run 无 tushare/ 目录
- `list_dp_calls` 只扫 node_dir 一层，`tushare/` 子目录不会被误判为 DP 调用
