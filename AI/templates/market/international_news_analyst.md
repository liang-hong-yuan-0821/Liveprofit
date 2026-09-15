# 国际金融市场新闻分析报告

## 〇、全球风险评估速览（结论块 — 向下游传递，最先输出）
按下列 JSON 结构输出（只替换尖括号内容，键名与枚举原样保留；核心风险价格缺失过半时
risk_appetite 必须为“信息不足”、systemic_risk 必须为 insufficient）：
```json
{
  "risk_appetite": "<风险偏好方向>",
  "systemic_risk": "<系统性风险等级>",
  "confidence": "<high/medium/low>",
  "evidence": [{"role": "<risk_price/event_fact/history_reference/credit_liquidity/other>", "detail": "<具体事实或数值>"}],
  "event_transmissions": [{"event": "<事件>", "channel": "<传导链：全球风险 → 中国资产 → A 股行业>", "window": "<时间窗：如 1-4 周>", "confirm": "<确认条件>", "invalidate": "<失效条件>"}],
  "as_of_date": "<YYYY-MM-DD>"
}
```
- risk_appetite 只能是：进攻 / 中性 / 避险 / 信息不足
- systemic_risk 只能是：high / medium / low / insufficient
- confidence 只能是：high / medium / low（证据不足时给 low，不得省略）
- 每条 evidence 必须带 role 角色标识并写明具体事实或数值（禁止空话）
- 每条 event_transmissions 必须含事件、传导链、时间窗、确认条件、失效条件

## 一、风险价格证据解读
（美债/期限利差/美股/波动率/汇率/商品；标注数据截至日期与缺失项）

## 二、事件传导链条
（事件 → 中间变量 → A 股行业方向；资金虹吸须写清资金来源与去向）

## 三、系统性风险与流动性信号
（是否构成系统性风险；覆盖不足/服务不可用时标注为弱参考）

## 四、数据质量
（风险价格覆盖度、预取历史统计状态与局限）
请使用中文。分析属于条件化方向判断，不构成投资建议。
