# 前端日期加减月：setMonth 溢出与负号方向

> 一句话结论："近 N 月"类区间选择器**不得用 `setMonth` 裸调**（1/31 会滚成 3/3）；目标月同日 + 日溢出夹取到目标月最后一天；且注意常量表正负方向与调用点一致（漏负号会算出未来日期）。

## 表象 → 根因 → 正确姿势

1. **表象**（2026-09-19 趋势对比面板实现期实测）：区间选择器返回 `from=2027-09-19`（未来日期）——RANGES 表存的是正数 `months: 3/12/36`，映射时漏了 `-months`；首轮单测纯函数断言第一遍即抓出。
   - **正确姿势**：「近 N 月」语义是减法，常量表与调用点二选一处显式声明方向：要么表里存负值，要么映射处写 `addMonthsClamped(today, -months)` 并加注释。

2. **日溢出**：`new Date().setMonth(month - 1)` 对 1/31 会溢出滚到 3/3（JS Date 自动进位）。
   - **正确姿势**（已实现于 TrendComparisonPanel 的 `addMonthsClamped`，纯函数导出供单测）：

   ```ts
   const [year, month, day] = dateStr.split('-').map(Number);
   const total = year * 12 + (month - 1) + months;
   const targetYear = Math.floor(total / 12);
   const targetMonth = total % 12; // 0–11
   const lastDay = new Date(targetYear, targetMonth + 1, 0).getDate();
   return `${targetYear}-${pad(targetMonth + 1)}-${pad(Math.min(day, lastDay))}`;
   ```

   - 断言样例：`2026-03-31` 减 1 月 = `2026-02-28`；`2026-01-31` 减 1 月 = `2025-12-31`；`2026-03-31` 加 1 月 = `2026-04-30`。
   - 口径余量：区间边界只决定取多少历史（服务端 BETWEEN、无上限校验），几天级的日期漂移不影响曲线正确性——夹取语义下无需过度追求"自然月"精确定义。
