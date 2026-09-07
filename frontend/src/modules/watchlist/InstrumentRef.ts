// 两个 UI 聚合（自选分组/标的 与 组合/持仓）共享的纯标的引用类型：
// 不承载 DTO、Query 或持久化状态；不与 AI 建议（final_position_plan）自动合并。
export interface InstrumentRef {
  market: 'US' | 'KR' | 'CN';
  symbol: string;
}

export function instrumentRefKey(ref: InstrumentRef): string {
  return `${ref.market}:${ref.symbol}`;
}
