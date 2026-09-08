import { useEffect, useMemo, useRef, useState } from 'react';
import { Card, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleHeader } from '../../../../shared/ui/collapsible';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import type { ExecutionLayerDTO } from '../../../../api/generated';
import { useExecutionLogsQuery } from './queries';
import { NodeDetails, nodeHasContent } from './ExecutionNodeDetails';

// 执行调用日志面板：layer → 节点 → DP 调用（req/res + tushare 子调用）→ 工具调用 →
// LLM 提示词/输出 → meta 的嵌套展开结构。非终态由 useExecutionLogsQuery 每 5s 轮询"生长"，
// 默认全部收起、自动展开"最后一个有内容的 node"（userCollapsed 防抖动）；终态停轮询、保留用户展开状态。
// 节点级渲染块（NodeDetails 等）在 ExecutionNodeDetails.tsx，与拓扑弹窗共用。

const mutedStyle = { color: 'var(--color-fg-muted)' } as const;

interface ExecutionLogsPanelProps {
  taskId: string;
  terminal: boolean;
}

export function ExecutionLogsPanel({ taskId, terminal }: ExecutionLogsPanelProps) {
  const query = useExecutionLogsQuery(taskId, true, terminal);
  const data = query.data;

  const [expandedLayers, setExpandedLayers] = useState<Set<string>>(new Set());
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [userCollapsed, setUserCollapsed] = useState<Set<string>>(new Set());
  const prevAutoDir = useRef<string | null>(null);

  // 最后一个有内容的 node dir（按内核落盘时序遍历）
  const lastContentDir = useMemo(() => {
    if (!data?.available) return null;
    let last: string | null = null;
    for (const layer of data.layers) {
      for (const node of layer.nodes) {
        if (nodeHasContent(node)) last = node.dir;
      }
    }
    return last;
  }, [data]);

  // 运行中"生长"聚焦：自动展开目标随最新 node 前移；只作用于新出现的 dir，
  // 用户手动收起过的 dir 不再自动展开；终态不再自动展开。
  useEffect(() => {
    if (terminal || !lastContentDir || !data) return;
    if (prevAutoDir.current === lastContentDir) return;
    if (userCollapsed.has(lastContentDir)) return;
    prevAutoDir.current = lastContentDir;
    setExpandedNodes((prev) => new Set(prev).add(lastContentDir));
    const layer = layerNameOf(data.layers, lastContentDir);
    if (layer) setExpandedLayers((prev) => new Set(prev).add(layer));
  }, [lastContentDir, terminal, userCollapsed, data]);

  const toggleLayer = (name: string, open: boolean) =>
    setExpandedLayers((prev) => {
      const next = new Set(prev);
      if (open) next.add(name);
      else next.delete(name);
      return next;
    });

  const toggleNode = (dir: string, open: boolean) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (open) next.add(dir);
      else next.delete(dir);
      return next;
    });
    setUserCollapsed((prev) => {
      const next = new Set(prev);
      if (open) next.delete(dir);
      else next.add(dir);
      return next;
    });
  };

  if (query.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>执行调用日志</CardTitle>
        </CardHeader>
        <LoadingState label="执行日志加载中…" />
      </Card>
    );
  }

  if (query.isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>执行调用日志</CardTitle>
        </CardHeader>
        <ErrorState
          error={toApiError(query.error)}
          onRetry={query.isError ? () => void query.refetch() : undefined}
        />
      </Card>
    );
  }

  return (
    <Card data-testid="execution-logs-panel">
      <CardHeader>
        <CardTitle>执行调用日志</CardTitle>
        <span className="text-xs" style={mutedStyle}>
          生成于 {new Date(data.generated_at).toLocaleString('zh-CN', { hour12: false })}
          {terminal ? '' : ' · 每 5 秒自动刷新'}
        </span>
      </CardHeader>
      {data.available && data.layers.length > 0 ? (
        <div className="flex flex-col gap-1">
          {data.layers.map((layer) => (
            <LayerBlock
              key={layer.name}
              taskId={taskId}
              layer={layer}
              open={expandedLayers.has(layer.name)}
              onOpenChange={(open) => toggleLayer(layer.name, open)}
              expandedNodes={expandedNodes}
              onToggleNode={toggleNode}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          title="暂无执行日志"
          description={
            data.available ? '本次运行尚未产生日志内容' : '任务尚未开始写入或本次运行未产生日志'
          }
        />
      )}
    </Card>
  );
}

function LayerBlock({
  taskId,
  layer,
  open,
  onOpenChange,
  expandedNodes,
  onToggleNode,
}: {
  taskId: string;
  layer: ExecutionLayerDTO;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  expandedNodes: Set<string>;
  onToggleNode: (dir: string, open: boolean) => void;
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <CollapsibleHeader className="text-sm font-semibold">
        {layer.name}
        <span className="ml-1 font-normal text-xs" style={mutedStyle}>
          {layer.nodes.length} 节点
        </span>
      </CollapsibleHeader>
      <CollapsibleContent>
        {layer.nodes.map((node) => (
          <NodeDetails
            key={node.dir}
            taskId={taskId}
            node={node}
            open={expandedNodes.has(node.dir)}
            onOpenChange={(o) => onToggleNode(node.dir, o)}
          />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

function layerNameOf(layers: ExecutionLayerDTO[], nodeDir: string): string | null {
  for (const layer of layers) {
    if (layer.nodes.some((n) => n.dir === nodeDir)) return layer.name;
  }
  return null;
}
