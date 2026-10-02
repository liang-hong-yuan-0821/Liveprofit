# 关键决策记录

按时间**倒序**追加。每条四要素：背景 → 选项 → 拍板结论 → 理由。

## 2026-10-02 var/ 内按性质四层分组 + 启用 PYTHONPYCACHEPREFIX（用户拍板）

- **背景**：评审通过版方案 var/ 下有 9 个子目录（backups、pytest、pytest-cache 各自占顶层，cache 类分居两处）；用户问"能不能把 var 下的文件夹再优化下"。
- **选项**：A. 维持 9 子目录；B. 性质分层 7 子目录（logs/data/cache/tmp + runs/research/results），PYTHONPYCACHEPREFIX 启用/不启用二选一。
- **结论**：选 B + 启用 PYTHONPYCACHEPREFIX。
- **理由**：按性质分组后新增同类数据自然归位（data=持久、cache=可重建、tmp=临时），与 FHS /var 子目录对齐；增量成本仅 tests/run.py 一行、断言一行、backups 目标字符串；PYTHONPYCACHEPREFIX 一行 export 换来根目录彻底干净且风险可忽略。

## 2026-10-02 运行时数据收敛进 var/ 单一运行时卷

- **背景**：根目录散落 `logs/`、`chroma_db/`、`results/` 三个运行时目录与多个缓存目录；`var/` 已是项目定义的"平台产物卷"（.gitignore 注释、settings.py 两个根目录、docker 卷命名），但同类数据分居四处。
- **选项**：A. 全部收敛进 `var/`（logs≈/var/log、chroma≈/var/lib、results/pytest≈/var/tmp）；B. 另立新根级目录（如 `data/`、`runtime/`）；C. 维持现状只删垃圾。
- **结论**：选 A。
- **理由**：`var` 符合 FHS 可变数据语义且项目内已有自洽定义与沉淀（settings/compose/run.sh/tests 五处引用、docker 卷挂载），改名成本高收益低；`data/` 易与源码数据混淆，`tmp/` 语义太窄（与 var/runs 90 天保留审计语义冲突）。

## 2026-10-02 logs/backups 与 logs/quant_history 拆分落位

- **背景**：logs/ 实含 69 项，除日志外还有 backups/（pg 备份审计数据，被 0007 迁移注释与知识文档引用）与 quant_history/（baostock parquet 行情数据）；R1 评审 F17 指出随迁 var/logs/ 会稀释 logs 语义。
- **选项**：A. 整体随迁 var/logs/（引用同步最少）；B. backups → var/backups/、quant_history → var/data/quant_history/（FHS 语义贴切：/var/backups、/var/lib 类数据）。
- **结论**：选 B。
- **理由**：引用同步成本仅 3 处（migrate_legacy.py:440、0007 注释、数据库表结构.md:34），换来 var/logs 纯日志语义与 var/ 内部职责清晰，符合"按业务语义落位"原则。

## 2026-10-02 chroma 不放 db/ 目录

- **背景**：用户询问记忆库能否放 `db/`；`db/` 是被 git 跟踪的源码包（pyproject `include = ["db*"]`），内容全为代码与 schema。
- **选项**：A. 放 `db/chroma_db/`；B. 放 `var/data/chroma_db/`；C. 放 `var/lib/chroma_db/`。
- **结论**：选 B。
- **理由**：运行期可变数据混入源码包会造成 gitignore 例外、打包污染（SOURCES.txt）、docker 往源码目录挂卷冲突；chroma 属 FHS /var/lib 语义归 var/，`data` 子名比 `lib` 更贴近项目已有 `var/research/datasets` 的取名习惯。
