export const REPORT_BLOCK_LABELS: Record<string, string> = { market: '市场分析', sector: '板块分析', stock: '个股分析', decision: '决策建议' };
export function unavailableSummary(blocks?: Array<{ block: string; reason?: string | null }> | null) {
  return blocks?.length ? blocks.map(block => `${REPORT_BLOCK_LABELS[block.block] ?? block.block}${block.reason ? `：${block.reason}` : '暂不可用'}`).join('；') : '报告需要检查，请查看报告详情';
}
