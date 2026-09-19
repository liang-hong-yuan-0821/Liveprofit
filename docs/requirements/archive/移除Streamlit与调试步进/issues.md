# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-19 T2 验证遇 2 个预存失败（test_task_service）

- **表象**：`.venv/Scripts/python.exe -m pytest backend/tests/unit -q` 报 `test_task_service.py::test_idempotent_replay_same_key_same_input_returns_original_task`（commit_count 2 != 1）与 `::test_layer_validation_rules`（position 层级组合未拦截）失败。
- **根因**：与本任务文件零交集——两用例走 TaskService 创建链；`test_task_service.py` 与 `task_lifecycle.py` 相对 HEAD 均未修改（Code Review 核验），失败为仓库既有问题（HEAD 基线）；`execution_control.py` 有用户并发改动（M，+17/-5）但其归属由并发任务核实。本任务只改了 execution_logs.py/graph_topology.py/routers 的 logs_reader import。判定为预存失败，非本任务引入。
- **解决**：不代修；本任务相关测试单独验证全绿——`test_logs_reader.py` + `test_execution_logs.py` + `test_graph_topology.py` 37 passed，契约 `test_execution_logs_api.py`/`test_graph_topology_api.py` 15 passed。T7 全量回归复核仍为同一 2 个失败（其余 358 passed），未变。
