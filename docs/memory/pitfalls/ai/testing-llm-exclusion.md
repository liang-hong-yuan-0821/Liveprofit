# 自测排除真实 LLM 测试

> 一句话结论：`-k "not integration"` 按名排除有漏洞；全量自测前先 `grep -rl "real_llm\|real_toolkit" tests/` 按文件名排除，或只跑相关测试文件 + 无真实依赖目录。

## `-k "not integration"` 排除有漏洞（2026-08-24）

- **表象**：Claude 全量自测挂起 10 分钟以上（真实 LLM 调用 timeout=180s + Tushare 代理拉取）。
- **根因**：部分用真实依赖 fixture 的测试名不含 "integration"（实测 `test_sector_news_analyst.py`、`test_sector_tech_analyst.py` 的全部用例、`test_sector_rotation_analyst.py` 的单元测试以外的集成用例），`-k` 按名排除不掉。
- **正确姿势**：先 `grep -rl "real_llm\|real_toolkit" tests/` 列出含真实依赖的文件，逐个按文件名排除，或只跑与本任务相关的测试文件 + 无真实依赖的目录（position/screening/risk_gate/graph/utils/templates/event_study/dataflows）。

## 配套强制规则（CLAUDE.md「测试规则」）

- 依赖真实 LLM 的测试（`real_llm` / `real_toolkit` fixture）**只能由用户手动调用**，Claude 不得自动运行。
