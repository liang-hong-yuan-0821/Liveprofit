# 时间线日志

按时间**倒序**追加（最新在上）。每条 = 日期 + 动作 + 结果/备注；一句话能说清就不写长段。

| 日期 | 动作 | 结果/备注 |
|------|------|----------|
| 2026-09-14 | Code review findings 修复（minor-1×2/2 + polish-1~4）+ 遗留 KOSPI/KOSDAQ 孤儿行清理 + knowledge 同步（产品需求分析 v1.5、API契约 §8.1）+ result/retrospective 填写 | 复验：118 + 273 + 6 passed；verdict PASS，文件夹归档 |
| 2026-09-14 | subagent code review（13 文件，akshare 157 行平移 AST+字节级双核对） | verdict PASS：0 blocker / 0 major / 3 minor / 5 polish |
| 2026-09-14 | T6 真实库验证：定向回填 26723 行 + SQL/API/增量验收 | 4 码全通 source=tushare；API 200；增量 bars=36 factors=27；前端目检待用户确认 |
| 2026-09-14 | T1-T5 实现完成：tushare index_global 主源分支、akshare 新浪兜底分支、采集链 13 目标+白名单守卫、CLI 门控删除、前端 4 项 AVAILABLE+KOSDAQ 移除 | 单测：tushare 新 10 passed、akshare 新 8 passed、tests/dataflows 273 passed、tests/db/instrument 100 passed、前端 6 passed |
| 2026-09-14 | 存量 bug 修复：AKShareProvider 结构化接口整块嵌套在 predict_tomorrow_trend 内为死代码（get_index_data_df/get_trade_cal/get_macro_context 从未注册）；CN 分支日期过滤对 datetime.date 上游 TypeError | 整块移回类内 + 日期归一修复；详情见 issues.md |
| 2026-09-14 | T6 真实库回填启动（2000-01-01 起，仅指数分项 skip_concepts+skip_daily） | 后台运行中 |
| 2026-09-14 | 实测验收：tushare index_global（SPX/DJI/IXIC/KS11 全通、26 年历史、上游自带 pre_close/change/pct_chg）；新浪源可用（US ~5712 行、KS11 ~1000 行）；东财 KS11/KQ11 空表；KOSDAQ 三源均不可用 | 用户拍板：tushare 主源 + 上线 US 3 + KS11、KOSDAQ 从目录移除；方案起草完成待审阅 |
