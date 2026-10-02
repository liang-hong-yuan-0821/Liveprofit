# 复盘

## 做对了什么（可复用）

- **R1 评审把"代码写入方全量枚举"逼出来是本次最大价值点**：初稿只 grep 配置层字面量，R1 用数据流闭环维度找出 9 处直接写根 logs/ 的代码（trading_graph 回退、eventStudy 调度器、回填/迁移脚本）——若无评审，迁移后根目录会被这些写入方静默重建，方案即失败。评审循环 R1→R3 的 delta 口径（只核 findings 落地、只重打涉及维度）让三轮收敛可控。
- **CWD 无关原则落实为两种机制**：backend 走既有 resolve_execution_logs_root 单一出口；AI/db 脚本层用 `Path(__file__).resolve().parents[N]` 绝对化。R2 抓住 F4/F5 两处"嘴上说 CWD 无关、方案仍是相对路径"的矛盾，F5×F6 交互（绝对路径 vs chdir 断言）在 R2 被识别而非留到实现期。
- **验收命令全部实测可执行**（R1 F7/F8 教训）：`./run.sh` 无参默认 all、`python -m tests.run --module`、ruff 判定口径改为"不扫 var/"而非不可达的"全量零错"。
- **迁移前停服 + cp -a 保点文件 + 逐项 ls 复核落点**：当场抓到 backups/quant_history 嵌套（mkdir -p 预创建目标 + mv），T4 验收没放行假路径。

## 踩了什么坑（教训）

- **ACL 破坏目录**：rm/attrib/icacls/takeown/cmd rmdir 全部失败（连枚举都失败），最终靠 rename 移出仓库隔离、管理员权限收尾——R2 F20 已预警这类目录，但实现期低估了"删除失败"的全部后果（basetemp 直接落在坏目录下导致 36+20 个测试 ERROR）。已沉淀 [目录迁移与坏ACL清理](../../../../docs/experience/pitfalls/workspace/目录迁移与坏ACL清理.md)。
- **run.sh 顶部 export 顺序**：PYTHONPYCACHEPREFIX 首版插在 SCRIPT_DIR 定义之前，变量为空——shell 脚本 export 依赖先前赋值，改动后必须实测展开值。
- **pytest cache_dir 与 no:cacheprovider 组合**产生 PytestConfigWarning 噪音，code review 抓出后加 `-W ignore` 消解。

## 下次改进

- 方案阶段就应把"删除失败/ACL 异常的兜底与验收口径"写成可执行步骤（本次 R2 预警了风险，但实现期才补 rename 隔离姿势）；对 Windows 目录操作类任务，方案中直接预置 `cmd //c rmdir` 与 rename 隔离两步兜底。
- 迁移类任务的验收标准应显式包含"逐项目标落点 ls 复核 + 嵌套检查"，而不是只验证 mv 命令成功。
- 评审发现"测试断言以被测模块常量自身为基准"（test_run_log_dir 自引用）——未来写路径断言测试应使用独立基准（`Path(__file__).resolve().parents[N]`），避免深度写错时测试自洽通过。
