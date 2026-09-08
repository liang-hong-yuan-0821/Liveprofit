import { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { useGraphTopologyQuery } from './queries';
import { NodeLogsDialog } from './NodeLogsDialog';

// 执行拓扑面板：echarts graph 展示静态图拓扑（三层 + 层内主节点）叠加本次运行状态。
// 固定网格布局（row=层行、order=层内列），状态着色（灰/蓝/琥珀/红），
// 点击节点打开 NodeLogsDialog（复用 execution-logs 同 query 缓存）。
// 非终态由 useGraphTopologyQuery 每 5s 轮询，终态停止；taskFailed 且无节点级错误时
// 显示任务级失败提示（错误可能发生在图外或首个 DP 调用前）。

const X_STEP = 170;
const Y_STEP = 120;
const X_ORIGIN = 70; // 左侧留行标签宽度
const Y_ORIGIN = 30;

export const STATUS_META = {
  not_executed: { label: '未执行', color: '#475569' },
  executed: { label: '已执行', color: '#38bdf8' },
  running: { label: '执行中', color: '#fbbf24' },
  error: { label: '出错', color: '#ef4444' },
} as const;

const EDGE_COLOR = '#475569';
const LABEL_COLOR = '#cbd5e1';
const LAYER_LABELS: Record<string, string> = {
  market: '市场层',
  sector: '板块层',
  stock: '个股层',
  screening: '选股',
};

interface GraphTopologyPanelProps {
  taskId: string;
  terminal: boolean;
  taskFailed: boolean;
}

// echarts 点击/悬停回调参数（graph 系列 node/edge 均含 data 载荷，只取需要的字段）
interface TopologyChartParams {
  dataType?: string;
  data?: { id?: string; source?: string; target?: string };
}

export function GraphTopologyPanel({ taskId, terminal, taskFailed }: GraphTopologyPanelProps) {
  const query = useGraphTopologyQuery(taskId, true, terminal);
  const data = query.data;
  // 只存选中节点 id：弹窗内容从最新 data 实时派生（5s 轮询/终态补拉后状态与 dirs
  // 不滞留旧快照）；节点从新数据中消失时弹窗自动关闭
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = useMemo(
    () => data?.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [data, selectedNodeId],
  );

  const height = useMemo(() => {
    if (!data || data.nodes.length === 0) return 240;
    const maxRow = Math.max(...data.nodes.map((n) => n.row));
    return (maxRow + 1) * Y_STEP + 120;
  }, [data]);

  const option = useMemo(() => {
    if (!data) return null;
    const nodes = data.nodes.map((n) => ({
      id: n.id,
      name: n.id,
      x: X_ORIGIN + n.order * X_STEP,
      y: Y_ORIGIN + n.row * Y_STEP,
      symbolSize: 16,
      itemStyle: { color: STATUS_META[n.status].color },
      label: {
        show: true,
        position: 'bottom' as const,
        fontSize: 11,
        color: LABEL_COLOR,
        formatter: `${n.label}${n.invocation_count > 1 ? ` ×${n.invocation_count}` : ''}${
          n.status === 'running' ? ' · 执行中' : ''
        }`,
      },
    }));
    const links = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      lineStyle: {
        color: EDGE_COLOR,
        width: 1.5,
        type: e.kind === 'direct' ? ('solid' as const) : ('dashed' as const),
        curveness: e.parallel ? 0.25 : 0,
      },
      label: e.kind === 'loop' ? { show: true, formatter: '逐票循环', fontSize: 10, color: '#8b95a1' } : undefined,
    }));
    const rows = [...new Set(data.nodes.map((n) => n.row))].sort((a, b) => a - b);
    const rowLayers = rows.map((row) => {
      const sample = data.nodes.find((n) => n.row === row);
      return { row, layer: sample?.layer ?? '' };
    });
    return {
      backgroundColor: 'transparent',
      series: [
        {
          type: 'graph',
          layout: 'none',
          data: nodes,
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
        top: Y_ORIGIN + row * Y_STEP - 6,
        style: { text: LAYER_LABELS[layer] ?? layer, fontSize: 12, fill: '#8b95a1', fontWeight: 'bold' },
      })),
      tooltip: {
        trigger: 'item',
        confine: true,
        formatter: (params: TopologyChartParams) => {
          if (params.dataType === 'edge' && params.data?.source && params.data?.target) {
            return `${params.data.source} → ${params.data.target}`;
          }
          if (params.dataType !== 'node' || !params.data?.id) return '';
          const id: string = params.data.id;
          const node = data.nodes.find((n) => n.id === id);
          if (!node) return '';
          const meta = STATUS_META[node.status];
          return `${node.label}<br/>状态：${meta.label}<br/>调用次数：${node.invocation_count}`;
        },
      },
    };
  }, [data]);

  const handleChartEvents = useMemo(
    () => ({
      click: (params: TopologyChartParams) => {
        if (params.dataType !== 'node' || !params.data?.id || !data) return;
        if (data.nodes.some((n) => n.id === params.data!.id)) setSelectedNodeId(params.data!.id);
      },
    }),
    [data],
  );

  if (query.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>执行拓扑</CardTitle>
        </CardHeader>
        <LoadingState label="拓扑加载中…" />
      </Card>
    );
  }

  if (query.isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>执行拓扑</CardTitle>
        </CardHeader>
        <ErrorState
          error={toApiError(query.error)}
          onRetry={query.isError ? () => void query.refetch() : undefined}
        />
      </Card>
    );
  }

  const hasAnyError = data.nodes.some((n) => n.status === 'error');

  return (
    <Card data-testid="graph-topology-panel">
      <CardHeader>
        <CardTitle>执行拓扑</CardTitle>
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          生成于 {new Date(data.generated_at).toLocaleString('zh-CN', { hour12: false })}
          {terminal ? '' : ' · 每 5 秒自动刷新'}
        </span>
      </CardHeader>
      {!data.available ? (
        <EmptyState title="暂无执行拓扑" description="任务尚未开始写入执行日志" />
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3 px-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {(Object.keys(STATUS_META) as Array<keyof typeof STATUS_META>).map((status) => (
              <span key={status} className="flex items-center gap-1">
                <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: STATUS_META[status].color }} />
                {STATUS_META[status].label}
              </span>
            ))}
            <span>虚线 = 条件边 / 循环边 · 点击节点查看调用日志</span>
          </div>
          {taskFailed && !hasAnyError && (
            <p className="text-sm px-1" style={{ color: 'var(--color-fg-muted)' }}>
              任务失败，但未定位到节点级错误——错误可能发生在图外或首个 DP 调用前
            </p>
          )}
          <div data-testid="topology-chart">
            <ReactECharts option={option} style={{ height, width: '100%' }} notMerge onEvents={handleChartEvents} />
          </div>
        </div>
      )}
      <NodeLogsDialog
        taskId={taskId}
        terminal={terminal}
        node={selectedNode}
        onClose={() => setSelectedNodeId(null)}
      />
    </Card>
  );
}
