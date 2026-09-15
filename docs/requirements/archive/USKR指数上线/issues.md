# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-14 Code Review findings（PASS，3 minor + 5 polish 已修，1 minor 遗留）

- **表象**：review 提出 3 minor + 5 polish（非 CN 方法构帧段逃逸异常、断点缺列探测误伤 akshare 行、AI 面 tushare 海外指数占位、日志文案/身份判断/风格项）。
- **根因**：见 result.md Code Review 节；minor-2 根因 = akshare 兜底首次真正可写后，其 pre_close 恒 NULL 行撞上断点"缺列重跑"探测条件。
- **解决**：minor-1×2 / minor-2 / polish-1~4 已修并复验（118 passed）；minor-3（AI 面 tushare `get_global_index` 海外指数）与 polish-5（前端 UNAVAILABLE 死分支保留）记为遗留，见 result.md。

## 2026-09-14 AKShareProvider 结构化接口整块嵌套在模块级函数内（死代码）

- **表象**：新增 US/KR 兜底分支单测全挂——`AKShareProvider.get_index_data_df` 落到基类默认实现（`'AKShareProvider' object has no attribute 'name'`）。
- **根因**：存量 bug（2026-09-13 扩列重构引入）：`calculate_correlation`/`predict_tomorrow_trend` 两个模块级函数插在类体中间（col 0 def 终结类体），其后"事件研究系统 — 结构化接口"整块（get_index_data_df/_index_symbol/get_trade_cal/get_macro_context，约 157 行）保持 4 空格缩进，嵌套在 `predict_tomorrow_trend` 的 return 之后——从未注册为类方法，AKShare 兜底取数/日历/宏观一直走基类默认返回 None。
- **解决**：整块平移到类尾（相关性分析注释之前，缩进不变即类方法层级）；顺带修复块内 CN 分支日期过滤——上游 date 实测为 `datetime.date` 对象，原字符串比较对真实数据 TypeError → None。验证：`hasattr` 5 方法全 True + `tests/dataflows` 273 passed。
