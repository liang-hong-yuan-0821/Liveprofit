# 独立 Code Review

范围：本任务测试布局、共享辅助模块、fixture 继承、路径随迁、模块运行与资源门禁、前端配置。review agent 为 layout_code_review，只读，不执行真实服务。

## R1：FAIL

1. major：bound 兼容性场景改为导入 support 后仍调用其中不存在的 test_* 函数。
2. major：entry_batch 的 DAY from-import 快照与 helper fixture 更新的日期错位。
3. major：AI 事件研究混合文件中的 predict_db/routing_db 没有进入默认 DB 门禁。
4. minor：手动脚本在初始化根目录前导入 tests.support，直接按路径启动失效。
5. major／阻塞验收的存量风险：instrument CLI 子进程未继承父进程模块注入的隔离 DSN，可能访问默认库。

## R2 delta：PASS，无待修项

- 三个共享断言场景抽为 assert_* helper，原测试保留入口。辅助函数的断言 AST 与迁移前一致，bound 的 _setup 替换与 helper 指向同一模块。
- entry_batch 测试动态读取所属模块 DAY，构造与断言共享日期。
- 两个 AI 混合文件原 29／33 个测试全部保留且无重复，函数 AST 无变化；真实 DB 场景进入 integration、显式 requires_db，收集与动态 fixture 门禁同步补齐。
- CLI 子进程显式传入经解析及固定隔离库名核验的环境。
- 两个手动工具先初始化路径；parents[4] 指向项目根。

### R2 收尾定向补充：PASS，无待修项

实际入口验证发现 pytest 临时目录父路径尚未创建。runner 在启动 pytest 前创建 var/pytest，随机子目录继续隔离；新增 mock 回归验证首次创建、cwd 及默认真实 E2E 禁用。benchmark 新输出归 var/test-benchmarks，写入前建目录，不污染测试 fixture。未执行真实工具、数据库、LLM 或 E2E。
