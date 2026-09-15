# 中国市场新闻分析报告

## 〇、事件日历速览（结论块 — 向下游传递，最先输出）
按下列 JSON 结构输出（只替换尖括号内容，键名与枚举原样保留；无证据的维度填
“信息不足”并把 score 置为 null）：
```json
{
  "short_term": {"level": "<短线压力>", "score": 3, "drivers": ["<驱动项：来源 + 事实 + 数值>"], "key_dates": ["<YYYY-MM-DD>"], "confidence": "<high/medium/low>"},
  "wave": {"level": "<波段压力>", "score": 3, "drivers": ["<驱动项>"], "key_dates": ["<YYYY-MM-DD>"], "confidence": "<high/medium/low>"},
  "long_term": {"level": "<长线压力>", "score": 3, "drivers": ["<驱动项>"], "key_dates": ["<YYYY-MM-DD>"], "confidence": "<high/medium/low>"},
  "as_of_date": "<YYYY-MM-DD>"
}
```
- short_term / wave / long_term 的 level 只能是：高 / 中 / 低 / 信息不足
- score 为 1-5 的整数（数据不足时填 null，不得估分）；drivers 与 key_dates 只能来自特征块
- 三级别压力必须与特征块窗口分级一致；解禁只写“日历事实/潜在供给”，不得写成必然卖压

## 一、短线资金日历（未来 5 交易日）
（IPO 抽血强度（仅募资额/发行规模口径）、解禁抛压、交割日临近、两融异动）

## 二、波段资金日历（未来 20 交易日）
（解禁高峰窗口、募资节奏、交割窗口、两融 1/5/20 日变化）

## 三、长线资金日历（未来 60 交易日）
（解禁供给节奏、长假窗口、两融水位）

## 四、未纳入项与数据质量
（未输入主题：季报窗口/政策会议/宏观数据发布/政策预期 → 显式标注“未纳入（无数据）”；
数据截至日期、缺失清单与降级说明）
请使用中文。
