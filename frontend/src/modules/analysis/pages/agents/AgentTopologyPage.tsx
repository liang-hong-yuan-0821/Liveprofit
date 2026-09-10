// Agent 静态全局拓扑页（单Agent重跑与提示词编辑方案 3.5）。
// 展示整个 Graph 架构（全层 market/sector/screening/stock），复用
// AI/graph/topology.build_topology（后端 /agents/topology 端点）；
// 已自定义提示词的节点琥珀色标记「 · 已自定义」；点击可编辑节点打开 PromptEditDialog。

import { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { PromptEditDialog } from '../../components/PromptEditDialog';
import { buildTopologyChartOption, TOPOLOGY_Y_STEP } from '../../components/topologyChartOption';
import { useAgentsTopologyQuery } from './queries';

const NODE_COLOR_HAS_OVERRIDE = '#f59e0b'; // 琥珀：已自定义提示词
const NODE_COLOR_HAS_PROMPT = '#38bdf8'; // 蓝：可编辑、未自定义
const NODE_COLOR_PURE_CODE = '#475569'; // 灰：纯代码节点（Screening）

interface TopologyChartParams {
  dataType?: string;
  data?: { id?: string };
}

export function AgentTopologyPage() {
  const query = useAgentsTopologyQuery();
  const data = query.data;
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const selectedNode = useMemo(() => {
    const node = data?.nodes.find((n) => n.id === selectedNodeId);
    if (!node || !node.has_prompt) return null; // 纯代码节点不响应编辑
    return { node_id: node.id, label: node.label };
  }, [data, selectedNodeId]);

  const height = useMemo(() => {
    if (!data || data.nodes.length === 0) return 320;
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
        color: n.has_override
          ? NODE_COLOR_HAS_OVERRIDE
          : n.has_prompt
            ? NODE_COLOR_HAS_PROMPT
            : NODE_COLOR_PURE_CODE,
        labelExtra: n.has_override ? ' · 已自定义' : '',
      })),
      data.edges,
      (id: string) => {
        const node = data.nodes.find((n) => n.id === id);
        if (!node) return '';
        const status = node.has_override
          ? '已自定义提示词'
          : node.has_prompt
            ? '默认提示词 · 点击编辑'
            : '纯代码节点（无提示词）';
        return `${node.label}<br/>${status}`;
      },
    );
  }, [data]);

  const handleChartEvents = useMemo<{ click: (params: TopologyChartParams) => void }>(
    () => ({
      click: (params: TopologyChartParams) => {
        if (params.dataType !== 'node' || !params.data?.id || !data) return;
        const node = data.nodes.find((n) => n.id === params.data!.id);
        if (node && node.has_prompt) setSelectedNodeId(params.data!.id);
      },
    }),
    [data],
  );

  if (query.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Agent 拓扑</CardTitle>
        </CardHeader>
        <LoadingState label="拓扑加载中…" />
      </Card>
    );
  }

  if (query.isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Agent 拓扑</CardTitle>
        </CardHeader>
        <ErrorState
          error={toApiError(query.error)}
          onRetry={query.isError ? () => void query.refetch() : undefined}
        />
      </Card>
    );
  }

  return (
    <Card data-testid="agent-topology-panel">
      <CardHeader>
        <CardTitle>Agent 架构拓扑</CardTitle>
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          {data.nodes.length} 个节点 · 点击节点编辑该 Agent 的提示词
        </span>
      </CardHeader>
      {data.nodes.length === 0 ? (
        <EmptyState title="暂无拓扑数据" />
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3 px-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            <span className="flex items-center gap-1">
              <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: NODE_COLOR_HAS_PROMPT }} />
              可编辑 Agent
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: NODE_COLOR_HAS_OVERRIDE }} />
              已自定义提示词
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block size-2.5 rounded-full" style={{ backgroundColor: NODE_COLOR_PURE_CODE }} />
              纯代码节点
            </span>
            <span>虚线 = 条件边 / 逐票循环</span>
          </div>
          <div data-testid="agent-topology-chart">
            <ReactECharts option={option} style={{ height, width: '100%' }} notMerge onEvents={handleChartEvents} />
          </div>
        </div>
      )}
      <PromptEditDialog node={selectedNode} onClose={() => setSelectedNodeId(null)} />
    </Card>
  );
}
