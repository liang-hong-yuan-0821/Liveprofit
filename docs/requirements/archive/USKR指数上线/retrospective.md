# 复盘

归档前写。任务级复盘；**跨任务可复用的主题沉淀进 docs/memory/**（按「经验沉淀规则」），本文件只留本任务的视角。

## 做对了什么（可复用）

- **实测验收先行**：动手前把三个候选数据源（tushare index_global / 新浪 / 东财）全部 live 实测（权限、列集、历史深度、单次窗口上限、超时行为），东财空表与 KOSDAQ 无源的结论全来自实测而非文档——避免了"按文档写代码、上线才炸"。
- **用户指正引入 index_global 主源**：原设计以新浪为唯一源（需自算 pre_close/change/pct_chg、KOSPI 仅 4 年历史），用户一句"为什么不用 tushare index_global"换来了上游自带三列 + 26 年历史 + 无需自算的更优方案——数据源选型时先问"主源 SDK 还有没有更贴合的端点"。

## 踩了什么坑（教训）

- **结构性新增方法后必须跑冒烟**：AKShareProvider 的"事件研究系统 — 结构化接口"整块（约 157 行）自 2026-09-13 扩列重构起嵌套在模块级函数 `predict_tomorrow_trend` 的 return 之后——从未注册为类方法，AKShare 兜底取数/日历/宏观**从未真正生效过**（含 CN 兜底：块内日期过滤用字符串比较，对真实上游的 `datetime.date` 对象必抛 TypeError → None）。单测全是 MagicMock provider，绕过了真实类。教训：provider 层新增/平移方法后，`hasattr(Class, 'method')` + 全量单测 + 一次真实数据冒烟缺一不可。
- **断点探测条件要考虑新写入方的字段语义**：akshare 兜底行 pre_close 恒 NULL 属事实，但回填断点的"缺列重跑"探测以 `pre_close IS NULL` 判缺列——兜底首次真正可写后这些行会被永久判缺列、每次回填重拉重写（幂等但耗配额）。修法：探测加 `source='tushare'`。

## 下次改进

- 涉 AI 面（非采集面）的对称性要前置评估：本次只修了 akshare 的 AI 面 kospi 分支，tushare 默认主源下 AI 分析师仍拿不到海外指数（review minor-3），留了尾巴。方案期应把"AI 面/平台面双链路"作为默认检查项（评审维度 9 跨链路覆盖）。
- 回填验证脚本化：本次为避开 CN 码 810 次子调用走了"进程内缩 INDEX_TARGETS"的定向回填，下次可直接内置一个 `--codes` 过滤参数。
