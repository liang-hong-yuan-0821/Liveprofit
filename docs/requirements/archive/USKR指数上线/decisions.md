# 关键决策记录

按时间**倒序**追加。每条四要素：背景 → 选项 → 拍板结论 → 理由。

## 2026-09-14 韩国只展示 KS11

- **背景**：KOSDAQ 无可用源（tushare index_global 无 KQ11、东财本机空表、新浪环球表无此品种），韩国组原目录 KOSPI/KOSDAQ 两项无法全部上线。
- **选项**：A 只展示 KOSPI（symbol 用 KOSPI）；B 只展示 KS11（symbol 用 tushare 原生代码）；C 两项都保留、KOSDAQ 继续"暂不可用"卡片。
- **结论**：B——韩国组只保留韩国综合指数一项，symbol 直接用 KS11，KOSDAQ 从目录移除。
- **理由**：用户拍板"前端先只展示 KS11"；symbol 用 tushare 原生代码省掉 KOSPI→KS11 映射层。

## 2026-09-14 tushare index_global 作主源

- **背景**：原方案设计以新浪为唯一取数源（实测可用），用户在方案评审时指正 tushare 有 `index_global` 端点且正覆盖 SPX/DJI/IXIC/KS11。
- **选项**：A 新浪唯一源；B tushare index_global 主源 + 新浪兜底；C tushare 唯一源。
- **结论**：B。
- **理由**：实测确认 index_global 全通且上游自带 pre_close/change/pct_chg（不自算）、历史 26 年远超新浪 KS11 的 4 年；保留新浪兜底与 CN 双源架构对称。
