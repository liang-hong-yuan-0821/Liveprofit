// 拓扑图 echarts option 共享构造器（任务详情执行拓扑 + Agent 静态全局拓扑共用）。
// 固定网格布局（row=层行、order=层内列）、行标签（graphic text）、虚线条件边、
// 平行边曲线、边箭头；节点颜色/标签后缀/悬停提示由调用方参数化。

export const TOPOLOGY_X_STEP = 170;
export const TOPOLOGY_Y_STEP = 120;
export const TOPOLOGY_X_ORIGIN = 70; // 左侧留行标签宽度
export const TOPOLOGY_Y_ORIGIN = 30;

export const TOPOLOGY_EDGE_COLOR = '#475569';
export const TOPOLOGY_LABEL_COLOR = '#cbd5e1';

export const TOPOLOGY_LAYER_LABELS: Record<string, string> = {
  market: '市场层',
  sector: '板块层',
  stock: '个股层',
  screening: '选股',
};

export interface TopologyChartNode {
  id: string;
  label: string;
  row: number;
  order: number;
  /** 所属层名（行标签用；缺省时从 id 前缀推导，保持既有语义） */
  layer?: string;
  /** 节点填充色（任务拓扑=状态色；Agent 拓扑=has_override 色） */
  color: string;
  /** 追加到节点标签的文案（如 " ×2"、" · 执行中"、" · 已自定义"） */
  labelExtra?: string;
}

export interface TopologyChartEdge {
  source: string;
  target: string;
  kind: 'direct' | 'conditional' | 'loop';
  parallel: boolean;
}

interface TopologyChartParams {
  dataType?: string;
  data?: { id?: string; source?: string; target?: string };
}

export function buildTopologyChartOption(
  nodes: TopologyChartNode[],
  edges: TopologyChartEdge[],
  nodeTooltip: (id: string) => string,
) {
  const chartNodes = nodes.map((n) => ({
    id: n.id,
    name: n.id,
    x: TOPOLOGY_X_ORIGIN + n.order * TOPOLOGY_X_STEP,
    y: TOPOLOGY_Y_ORIGIN + n.row * TOPOLOGY_Y_STEP,
    symbolSize: 16,
    itemStyle: { color: n.color },
    label: {
      show: true,
      position: 'bottom' as const,
      fontSize: 11,
      color: TOPOLOGY_LABEL_COLOR,
      formatter: `${n.label}${n.labelExtra ?? ''}`,
    },
  }));
  const links = edges.map((e) => ({
    source: e.source,
    target: e.target,
    lineStyle: {
      color: TOPOLOGY_EDGE_COLOR,
      width: 1.5,
      type: e.kind === 'direct' ? ('solid' as const) : ('dashed' as const),
      curveness: e.parallel ? 0.25 : 0,
    },
    label: e.kind === 'loop' ? { show: true, formatter: '逐票循环', fontSize: 10, color: '#8b95a1' } : undefined,
  }));
  const rows = [...new Set(nodes.map((n) => n.row))].sort((a, b) => a - b);
  const rowLayers = rows.map((row) => {
    const sample = nodes.find((n) => n.row === row);
    return { row, layer: sample?.layer ?? (sample ? layerOf(sample) : '') };
  });
  return {
    backgroundColor: 'transparent',
    series: [
      {
        type: 'graph',
        layout: 'none',
        data: chartNodes,
        links,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 7,
        emphasis: { focus: 'adjacency' },
        // 注意：series 级 tooltip 会整体覆盖全局 tooltip（echarts 级联模型），
        // 不得在此配置 show:false——悬停提示走顶层全局 tooltip
      },
    ],
    graphic: rowLayers.map(({ row, layer }) => ({
      type: 'text',
      left: 0,
      top: TOPOLOGY_Y_ORIGIN + row * TOPOLOGY_Y_STEP - 6,
      style: { text: TOPOLOGY_LAYER_LABELS[layer] ?? layer, fontSize: 12, fill: '#8b95a1', fontWeight: 'bold' },
    })),
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: TopologyChartParams) => {
        if (params.dataType === 'edge' && params.data?.source && params.data?.target) {
          return `${params.data.source} → ${params.data.target}`;
        }
        if (params.dataType !== 'node' || !params.data?.id) return '';
        return nodeTooltip(params.data.id);
      },
    },
  };
}

function layerOf(node: TopologyChartNode): string {
  // 行标签 layer：调用方节点未带 layer 字段，从 id 前缀推导
  return node.id.split(':')[0] ?? '';
}
