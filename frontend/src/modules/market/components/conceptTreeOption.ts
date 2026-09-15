// 概念 treemap echarts option 共享构造器（板块区块两层矩形树图）。
// 颜色在生成 option 时静态写入每个节点（含 children 两层）——实测 echarts 6.1
// 的 itemStyle.color 回调返回值被丢弃（node SSR 验证：回调被调用但 SVG 无红绿色），
// 必须静态逐节点着色；levels 仅承担边框/间距（colorSaturation 依赖视觉映射配色，
// 与 per-node 静态色互斥，不放）。
// 矩形大小与排序均只按当日涨跌幅（用户拍板 2026-09-14 修订：删除热度算法）：
// 大小=|pct|（面积保底 PCT_FLOOR）、排序=pct 降序（涨前跌后、跌少在前、灰最后）；
// 热度分 heat_score 仅存在于 API 契约，前端展示不消费。
// tooltip 用 treePathInfo 拼接路径（用户示例同款）：概念/个股均显示当日涨跌幅。

import type { ConceptTreeNodeDTO } from '../../../api/generated';

export const PCT_UP_COLOR = '#ef4444';   // 红涨（与 CandlestickChart 项目惯例一致）
export const PCT_DOWN_COLOR = '#22c55e'; // 绿跌
export const PCT_NULL_COLOR = '#64748b'; // 停牌/无行情中性灰
export const MIN_TILE_VALUE = 0.01;      // treemap value 必须非负：0/负值矩形最小块兜底
export const PCT_FLOOR = 0.5;            // 矩形面积保底（概念/个股两层共用）：|涨跌幅| < 0.5 的按 0.5 计——
                                         // 避免涨跌小的矩形趋零被 visibleMin 滤掉（用户验收反馈）

export interface TreemapNodeClick {
  kind: 'concept' | 'stock';
  code: string;
  name: string;
}

interface TreemapClickParams {
  data?: unknown;
  treePathInfo?: { name: string }[];
}

interface TreemapNodeData {
  sector_code?: string;
  ts_code?: string;
  name?: string;
  pct_chg?: number | null;
}

export function pctColor(pct: number | null): string {
  if (pct == null) return PCT_NULL_COLOR;
  return pct > 0 ? PCT_UP_COLOR : PCT_DOWN_COLOR;
}

/** 涨跌幅文案（tooltip/label 共用）：停牌/无行情 → "—"，否则带符号两位小数。 */
export function pctText(pct: number | null): string {
  if (pct == null) return '—';
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

/** 展示层排序（用户拍板 2026-09-14 修订）：**完全按当日涨跌幅降序**——
 * 涨（红）挨一起在前（涨多→涨少）、跌（绿）在后（跌少→跌多）、停牌灰最后。
 * 热度分不参与排序；契约顺序（rank 按热度）不变。 */
export function sortByPctDesc<T extends { pct_chg: number | null }>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const pa = a.pct_chg;
    const pb = b.pct_chg;
    if (pa == null && pb == null) return 0;
    if (pa == null) return 1;   // 停牌灰最后
    if (pb == null) return -1;
    return pb - pa;             // 涨跌幅降序：涨在前、跌在后，跌组内跌少在前
  });
}

/** 拆分为涨/跌两张图（用户拍板 2026-09-14）：up = pct>0（红，上图）；
 * down = pct≤0 与 null（绿+停牌灰，下图）。 */
export function splitUpDown<T extends { pct_chg: number | null }>(
  items: T[],
): { up: T[]; down: T[] } {
  const up = items.filter((c) => c.pct_chg != null && c.pct_chg > 0);
  const down = items.filter((c) => !(c.pct_chg != null && c.pct_chg > 0));
  return { up, down };
}

export function buildConceptTreeOption(items: ConceptTreeNodeDTO[]) {
  const data = sortByPctDesc(items).map((concept) => ({
    name: concept.sector_name,
    value: concept.pct_chg == null
      ? MIN_TILE_VALUE
      : Math.max(Math.abs(concept.pct_chg), PCT_FLOOR) + MIN_TILE_VALUE,  // 大小=|当日涨跌幅|（面积保底）；停牌灰块
    sector_code: concept.sector_code,
    pct_chg: concept.pct_chg,                            // upperLabel formatter 用（名称旁涨跌幅）
    itemStyle: { color: pctColor(concept.pct_chg) },     // 静态色：红涨绿跌/停牌灰
    children: sortByPctDesc(concept.members).map((member) => ({
      name: member.name,
      value: member.pct_chg == null
        ? MIN_TILE_VALUE
        : Math.max(Math.abs(member.pct_chg), PCT_FLOOR) + MIN_TILE_VALUE,  // 大小=|当日涨跌幅|（面积保底）；停牌灰块
      ts_code: member.ts_code,
      pct_chg: member.pct_chg,                           // label formatter 用（名称旁涨跌幅）
      itemStyle: { color: pctColor(member.pct_chg) },
    })),
  }));
  return {
    backgroundColor: 'transparent',
    animation: false, // 数据刷新重建不闪（echarts option 级；echarts-for-react 3.0.6 无 animation prop）
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: TreemapClickParams) => {
        const d = params.data as TreemapNodeData | undefined;
        if (!d) return '';
        // treePathInfo[0] 是虚拟根节点（实测 name 为空串，TreemapSeries wrapTreePathInfo
        // 自节点向上遍历必含根）——slice(1) 去掉，否则 join 出前导 " › " 残影；
        // 只剩单层（概念节点）时不渲染路径行（与加粗标题重名）
        const pathNodes = (params.treePathInfo ?? [])
          .slice(1).map((node) => node.name).filter(Boolean);
        const head = pathNodes.length > 1
          ? `<div style="opacity:.7">${pathNodes.join(' › ')}</div>` : '';
        if (d.sector_code) {
          const concept = items.find((c) => c.sector_code === d.sector_code);
          if (!concept) return d.name ?? '';
          return `${head}<b>${concept.sector_name}</b><br/>当日涨跌幅：${pctText(concept.pct_chg)}`;
        }
        if (d.ts_code) {
          const member = items.flatMap((c) => c.members)
            .find((m) => m.ts_code === d.ts_code);
          if (!member) return d.name ?? '';
          return `${head}<b>${member.name}</b><br/>当日涨跌幅：${pctText(member.pct_chg)}`;
        }
        return d.name ?? '';
      },
    },
    series: [
      {
        type: 'treemap',
        data,
        // 关键：TreemapSeries 默认 sort: true，layout 会把 true 降级为 'desc'——
        // 即不写 sort 也会按 value（=|pct|）降序重排，破坏"正永远大于负"的展示序
        // （用户验收实测反馈）；必须显式 sort: false 保留数据数组顺序（sortByPctDesc）。
        // 代价：visibleMin 阈值过滤依赖 sort 为真（filterByThreshold 无 sort 直接
        // return）——截断 top 100 后最多 ~3000 节点，无需渲染上限保护，一并移除。
        sort: false,
        breadcrumb: { show: false },  // 关掉默认的面包屑导航（下钻路径条，用户验收反馈移除）
        // 叶子（个股）label：名称旁带当日涨跌幅；概念层名称不在这里——
        // 非叶子节点的 label 走 upperLabel 分支（TreemapView 实测：无 upperLabel
        // 则内部节点无任何文字，levels 挂 upperLabel 无效，必须系列级）
        label: {
          show: true,
          formatter: (params: TreemapClickParams) => {
            const d = params.data as TreemapNodeData | undefined;
            if (!d) return '';
            if (d.ts_code) {
              return `${d.name ?? ''} ${pctText(d.pct_chg ?? null)}`;
            }
            return d.name ?? '';
          },
        },
        // 概念名称条（默认 position [0,'50%'] 左侧竖排居中）：名称 + 当日涨跌幅
        upperLabel: {
          show: true,
          height: 18,
          formatter: (params: TreemapClickParams) => {
            const d = params.data as TreemapNodeData | undefined;
            if (!d) return '';
            return `${d.name ?? ''} ${pctText(d.pct_chg ?? null)}`;
          },
        },
        itemStyle: { borderColor: '#fff' },
        levels: [
          { itemStyle: { borderWidth: 0, gapWidth: 5 } },                // 概念层
          { itemStyle: { gapWidth: 1, borderColorSaturation: 0.6 } },    // 个股层
        ],
      },
    ],
  };
}

/** 点击事件层级识别：有 sector_code → 概念；有 ts_code → 个股。 */
export function nodeClickOf(params: TreemapClickParams): TreemapNodeClick | null {
  const d = params.data as TreemapNodeData | undefined;
  if (!d) return null;
  if (d.sector_code) return { kind: 'concept', code: d.sector_code, name: d.name ?? d.sector_code };
  if (d.ts_code) return { kind: 'stock', code: d.ts_code, name: d.name ?? d.ts_code };
  return null;
}
