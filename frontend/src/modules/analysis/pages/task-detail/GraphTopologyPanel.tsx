import { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { useGraphTopologyQuery } from './queries';
import { NodeLogsDialog } from './NodeLogsDialog';
import { buildTopologyChartOption, TOPOLOGY_Y_STEP } from '../../components/topologyChartOption';

// 执行拓扑面板：echarts graph 展示静态图拓扑（三层 + 层内主节点）叠加本次运行状态。
// 固定网格布局（row=层行、order=层内列），状态着色（灰/蓝/琥珀/红），
// 点击节点打开 NodeLogsDialog（复用 execution-logs 同 query 缓存）。
// 非终态由 useGraphTopologyQuery 每 5s 轮询，终态停止；taskFailed 且无节点级错误时
// 显示任务级失败提示（错误可能发生在图外或首个 DP 调用前）。
// option 构建复用共享构造器 topologyChartOption.ts（Agent 静态拓扑同源）。

export const STATUS_META = {
  not_executed: { label: '未执行', color: '#475569' },
  executed: { label: '已执行', color: '#38bdf8' },
  running: { label: '执行中', color: '#fbbf24' },
  error: { label: '出错', color: '#ef4444' },
} as const;

interface GraphTopologyPanelProps {
  taskId: string;
  terminal: boolean;
  taskFailed: boolean;
  /** 任务类型（重跑不可用文案区分全市场逐票循环 vs 旧版本运行） */
  taskType?: string;
  /** 节点弹窗动作（单Agent重跑与提示词编辑方案 3.6）；不传则隐藏对应按钮 */
  onEditPrompt?: (nodeId: string) => void;
  onRerun?: (nodeId: string) => void;
  rerunPending?: boolean;
}

// echarts 点击回调参数（graph 系列 node/edge 均含 data 载荷，只取需要的字段）
interface TopologyChartParams {
  dataType?: string;
  data?: { id?: string; source?: string; target?: string };
}

export function GraphTopologyPanel({
  taskId, terminal, taskFailed, taskType, onEditPrompt, onRerun, rerunPending,
}: GraphTopologyPanelProps) {
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
    return (maxRow + 1) * TOPOLOGY_Y_STEP + 120;
  }, [data]);

  const option = useMemo(() => {
    if (!data) return null;
    return buildTopologyChartOption(
      data.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        layer: n.layer,
        row: n.row,
        order: n.order,
        color: STATUS_META[n.status].color,
        labelExtra:
          `${n.invocation_count > 1 ? ` ×${n.invocation_count}` : ''}${
            n.status === 'running' ? ' · 执行中' : ''
          }`,
      })),
      data.edges,
      (id: string) => {
        const node = data.nodes.find((n) => n.id === id);
        if (!node) return '';
        const meta = STATUS_META[node.status];
        return `${node.label}<br/>状态：${meta.label}<br/>调用次数：${node.invocation_count}`;
      },
    );
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
        taskType={taskType}
        node={selectedNode}
        onClose={() => setSelectedNodeId(null)}
        onEditPrompt={onEditPrompt ? () => selectedNode && onEditPrompt(selectedNode.id) : undefined}
        onRerun={onRerun ? () => selectedNode && onRerun(selectedNode.id) : undefined}
        rerunPending={rerunPending}
      />
    </Card>
  );
}
