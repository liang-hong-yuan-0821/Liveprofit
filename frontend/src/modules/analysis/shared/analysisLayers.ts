// 分析层级展示（analysis 三个工作区复用）：层 key → 中文标签 + 任务名称拼接。
// 层 key 定义见 AnalysisTaskForm 的 AnalysisLayer（market/sector/stock/screening/position）；
// DTO 层字段为 string[]，展示层按字符串值处理，未知值原样兜底（同 taskStatus 策略）。

export const LAYER_LABELS: Record<string, string> = {
  market: '市场',
  sector: '板块',
  stock: '个股',
  screening: '筛选',
  position: '仓位',
};

/** 展示用规范层序（存储层序 = 用户勾选顺序，展示统一按执行层序）。 */
const LAYER_ORDER = ['market', 'sector', 'stock', 'screening', 'position'] as const;

/** 任务名称 = 实行的分析层级（如「市场·板块·筛选」，按执行层序）；空列表兜底为 '—'。 */
export function layersName(layers: string[] | null | undefined): string {
  const order = LAYER_ORDER as readonly string[];
  const names = (layers ?? [])
    .slice()
    .sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map((layer) => LAYER_LABELS[layer] ?? layer);
  return names.length > 0 ? names.join('·') : '—';
}
