# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-10-02 var/tmp ACL 异常阻塞 basetemp 回归（待用户删除授权）

- **表象**：T3 关联回归 36+20 个 ERROR：`PermissionError: [WinError 5] 拒绝访问: var\tmp\pytest\<uuid>`（tmp_path fixture 创建失败）。
- **根因**：basetemp 按确认方案迁 var/tmp/pytest，而 var/tmp 正是 R2 评审 F20 预警过的 ACL 异常死副本目录（52M 旧 uv 缓存副本），mkdir 被拒绝；同范围用可写 basetemp 复跑 200 passed + 1 存量失败，证明代码改动本身正确。
- **解决**：需用户授权删除 var/tmp（T5 删除清单第 2 项，`cmd //c rmdir /s /q` 兜底）；删除后 tests/run.py 自动重建 var/tmp/pytest。当前按用户"我说删你再删除"边界暂停申请授权。

## 2026-10-02 T1 关联回归暴露存量失败：test_prompts 计数漂移（非本任务引入）

- **表象**：`python -m tests.run --module ai.graph --level unit` 中 test_prompts.py:69 失败：断言 `len(DEFAULT_PROMPTS)==23`，实际 26。
- **根因**：DEFAULT_PROMPTS 注册表已并入 event-study 3 条（Assessment Reviewer/Event Labeler/Novelty Judge，属量化策略/事件研究线的并入），测试断言与 prompts.py:3 docstring 的"23 个"未随迁；本任务未改 prompts.py 与该测试（git status 证实）。
- **解决**：登记待治理（主线优先，不零散修补）；后续按组修正断言/docstring 计数或在 eventStudy 任务内收口。

## 2026-10-02 R3 最终核验 findings（PASS；4 条新发现均 minor/polish，已在收尾修正）

- **表象**：R3 delta 核验 verdict PASS，全维度 ≥8（最低 D4=D6=8），满足收尾门槛；新发现 N1-N4。
- **根因**：N1：daily_job.py 的 FileHandler 为模块级，run_daily.bat 直启不经 app_scheduler，var/logs 缺失即导入期失败；N2：4.1.3/4.1.4 沿用旧 mv 语义的"空壳"措辞；N3：real_graph_factory.py 路径笔误（实为 backend/modules/analysis/infrastructure/）；N4：docstring 文件计数"六"与清单"七"不符。
- **解决**：收尾全部修正——daily_job.py basicConfig 前加 mkdir(parents=True) 一行覆盖直启入口；"空壳"措辞改"根级目录（迁移后删除）"并更新风险/验证描述；两处 real_graph_factory 路径改正；计数改"七文件九处"。方案状态更新为待用户确认。

## 2026-10-02 R2 修复核验 findings（FAIL：1 major / 4 minor / 1 polish；20/22 条 R1 findings 完整落地，不触发重写条款）

- **表象**：R2 delta 核验 verdict FAIL——D7=6（major A：F5×F6 两处修复的交互产物）、D4=7（C 组同类残留扫描不完整）、D6=7（E：mkdir 缺 parents）；另有 B（F22 残留）、D（69→70 计数）与 3 条 polish。
- **根因**：A：F5 把写盘改为 `_PROJECT_ROOT` 绝对路径后，F6 处方仍按 `chdir(tmp_path)` 断言——绝对路径不受 chdir 影响，会写进仓库真实 var/results，处方与设计自相矛盾；C：R1 列举范围已全部落地，但同类表述泛化扫描（注释/调度资产/测试注释/生成物）仍有 7+ 文件残留；E：backfill:757 现码依赖仓库根已存在，改 var/logs 后单级 mkdir 在 var/ 不存在时失败。
- **解决**：plan.md 就地修正——A：测试处方改 `monkeypatch.setattr(trading_graph, "_PROJECT_ROOT", tmp_path)` 并删除错误理由句；B：4.4.3 补"仅限用户手动执行"标注、git status 检查改有效口径（var/ 被忽略恒真检查不做）；C：随迁清单补齐 C-a~C-d 共 14 文件（含 tests/catalog 与测试详情生成物）；D：69→70（含点文件）；E：mkdir 补 `parents=True` 且 eventStudy 同口径建目录；polish 三项（迁移命令措辞、var/pytest 可清注记、35-37 行号）。待 R3 最终核验。

## 2026-10-02 R1 方案评审 findings（FAIL：10 major / 12 minor / 1 polish，各维度 6,6,10,6,8,6,6,6,6,6）

- **表象**：R1 全量评审 verdict FAIL。三组根因：① 代码内 logs 写入方漏迁（trading_graph 回退、eventStudy 调度器、回填/迁移脚本）；② 相对路径未兑现"CWD 无关"原则（chroma 默认值、results 路径）；③ 验证命令不可执行（`bash run.sh start` 不存在、`python tests/run.py --level unit` 入口错、ruff 全量零错不可达）。
- **根因**：初稿只 grep 了配置层与 run.sh 的字面量，未系统枚举"谁写 logs/ 谁写 chroma/ 谁写 results"的代码写入方；路径解析原则只在 backend 侧成立，AI/db 脚本层未落实；验证命令沿用口头习惯而非实测入口。
- **解决**：plan.md 重写修复全部 22 条 findings——补入 9 处代码写入方（F1-F3/F19）、chroma 与 results 改 Path(__file__) 推导 PROJECT_ROOT 的 resolver（F4/F5）、迁移命令改 `cp -a logs/.` 保点文件（F10）、backups/quant_history 拆分落位（F17）、测试断言三文件随迁（F6）、验证命令改实际入口 `./run.sh` 与 `python -m tests.run --module`（F7/F8）、ruff 判定口径改"输出不含 var/ 路径"（F9）、[tool.ruff] 新建与 setuptools exclude 归属更正（F11）、缓存解析语义更正（F12/F13）、行号与计数更正（F14/F15/F16）、文档随迁清单补全（F18）、ACL 兜底推广（F20）、回滚补 .env 手工回改与不可逆说明（F21）、真实 LLM 验证标注仅限用户手动（F22）、run.sh:333 文案修正（P1）。待 R2 delta 核验。

## 2026-10-02 方案事实核查发现 .env 不在 git 跟踪

- **表象**：回滚预案原写"git revert 即可回退"。
- **根因**：`.env` 被 .gitignore 排除，代码回退无法恢复其中的 `LIVEPROFIT_MEMORY_PATH` 原值。
- **解决**：回滚预案补充 .env 手工回改项与删除不可逆说明（plan.md 4.1.1 时序约束第 5 条）。
