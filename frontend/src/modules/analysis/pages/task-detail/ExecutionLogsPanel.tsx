import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleHeader } from '../../../../shared/ui/collapsible';
import { MarkdownView } from '../../../../shared/ui/markdown';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { toApiError } from '../../../../api/client';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import type {
  ExecutionDpCallDTO,
  ExecutionLayerDTO,
  ExecutionNodeDTO,
  ExecutionToolDTO,
  ExecutionTushareDTO,
} from '../../../../api/generated';
// ExecutionFileDTO 携带 kind 枚举命名空间（值导入），不能 import type
import { ExecutionFileDTO } from '../../../../api/generated';
import { useExecutionLogsQuery } from './queries';

// 执行调用日志面板：layer → 节点 → DP 调用（req/res + tushare 子调用）→ 工具调用 →
// LLM 提示词/输出 → meta 的嵌套展开结构。非终态由 useExecutionLogsQuery 每 5s 轮询"生长"，
// 默认全部收起、自动展开"最后一个有内容的 node"（userCollapsed 防抖动）；终态停轮询、保留用户展开状态。

const JSON_RENDER_MAX_CHARS = 200_000; // 大 JSON 渲染保护：超限截断显示

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
          <NodeBlock
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

function NodeBlock({
  taskId,
  node,
  open,
  onOpenChange,
}: {
  taskId: string;
  node: ExecutionNodeDTO;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const title = `${node.dir.split('/').pop() ?? node.dir}（${node.node ?? '未知节点'} · ${node.model ?? '未知模型'}）`;
  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <CollapsibleHeader>{title}</CollapsibleHeader>
      <CollapsibleContent>
        <div className="flex flex-col gap-2 py-1">
          <SectionLabel>DP 调用</SectionLabel>
          {node.dp_calls.length === 0 ? (
            <MissingHint />
          ) : (
            node.dp_calls.map((dp) => <DpCallBlock key={dp.dir ?? `legacy-${dp.name}`} taskId={taskId} dp={dp} />)
          )}

          <SectionLabel>LLM 提示词</SectionLabel>
          {node.llm_req ? <FileBody taskId={taskId} file={node.llm_req} /> : <MissingHint />}

          <SectionLabel>工具调用</SectionLabel>
          {node.tools.length === 0 ? (
            <MissingHint />
          ) : (
            node.tools.map((tool) => <ToolBlock key={tool.dir} taskId={taskId} tool={tool} />)
          )}

          <SectionLabel>LLM 输出</SectionLabel>
          {node.llm_res ? <FileBody taskId={taskId} file={node.llm_res} /> : <MissingHint />}

          <SectionLabel>meta</SectionLabel>
          {node.meta ? <JsonPre value={node.meta} /> : <MissingHint />}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function DpCallBlock({ taskId, dp }: { taskId: string; dp: ExecutionDpCallDTO }) {
  const title = dp.dir
    ? `${dp.dir.split('/').pop() ?? dp.dir}　${dp.name}${dp.desc ? ` — ${dp.desc}` : ''}`
    : `${dp.name}${dp.desc ? ` — ${dp.desc}` : ''}（旧格式）`;
  return (
    <Collapsible>
      <CollapsibleHeader>
        {title}
        {dp.error && (
          <Badge variant="destructive" className="ml-1">
            错误
          </Badge>
        )}
      </CollapsibleHeader>
      <CollapsibleContent>
        <div className="flex flex-col gap-1.5 py-1">
          <SectionLabel>入参</SectionLabel>
          {dp.req ? <JsonPre value={dp.req} /> : <MissingHint />}
          <SectionLabel>出参</SectionLabel>
          {dp.res ? <FileBody taskId={taskId} file={dp.res} /> : <MissingHint />}
          {dp.tushare.length > 0 && (
            <>
              <SectionLabel>Tushare 子调用</SectionLabel>
              {dp.tushare.map((ts) => (
                <TushareBlock key={ts.dir} taskId={taskId} ts={ts} />
              ))}
            </>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function TushareBlock({ taskId, ts }: { taskId: string; ts: ExecutionTushareDTO }) {
  return (
    <Collapsible>
      <CollapsibleHeader>
        {ts.name}
        {ts.probe && <Badge variant="secondary" className="ml-1">连通性探测</Badge>}
        {ts.error && (
          <Badge variant="destructive" className="ml-1">
            错误
          </Badge>
        )}
      </CollapsibleHeader>
      <CollapsibleContent>
        <div className="flex flex-col gap-1.5 py-1">
          <SectionLabel>入参</SectionLabel>
          {ts.req ? <JsonPre value={ts.req} /> : <MissingHint />}
          <SectionLabel>出参</SectionLabel>
          {ts.res ? <FileBody taskId={taskId} file={ts.res} /> : <MissingHint />}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ToolBlock({ taskId, tool }: { taskId: string; tool: ExecutionToolDTO }) {
  return (
    <Collapsible>
      <CollapsibleHeader>{tool.name}</CollapsibleHeader>
      <CollapsibleContent>
        <div className="flex flex-col gap-1.5 py-1">
          <SectionLabel>入参</SectionLabel>
          {tool.req ? <JsonPre value={tool.req} /> : <MissingHint />}
          <SectionLabel>出参</SectionLabel>
          {tool.res ? <FileBody taskId={taskId} file={tool.res} /> : <MissingHint />}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// 内容文件：内嵌内容直接渲染；truncated/parse_error 走 content 端点一次性拉全量。
function FileBody({ taskId, file }: { taskId: string; file: ExecutionFileDTO }) {
  const [full, setFull] = useState<ExecutionFileDTO | null>(null);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  const fetchFull = async () => {
    setPending(true);
    setFailed(false);
    try {
      const envelope =
        await AnalysisTasksService.getExecutionLogContentApiV1AnalysisTasksTaskIdExecutionLogsContentGet(
          taskId,
          file.path,
        );
      setFull(envelope.data);
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  };

  if (file.content != null) {
    return <FileContent kind={file.kind} content={file.content} />;
  }
  if (file.truncated || file.parse_error) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-sm" style={mutedStyle}>
          {file.truncated ? `内容过大（${file.total_bytes} 字节）` : '解析失败'}
        </p>
        <div>
          <Button variant="outline" size="sm" onClick={() => void fetchFull()} disabled={pending}>
            {pending ? '加载中…' : '查看完整内容'}
          </Button>
          {failed && (
            <p className="ml-2 inline text-sm" style={mutedStyle}>
              加载失败，请稍后重试
            </p>
          )}
        </div>
        {full && <FileContent kind={full.kind} content={full.content} />}
      </div>
    );
  }
  return <p className="text-sm" style={mutedStyle}>（空内容）</p>;
}

function FileContent({
  kind,
  content,
}: {
  kind: ExecutionFileDTO.kind;
  content: string | Record<string, any> | null | undefined;
}) {
  if (content == null) {
    return <p className="text-sm" style={mutedStyle}>（空内容）</p>;
  }
  if (kind === ExecutionFileDTO.kind.JSON) {
    // 大 JSON 渲染保护：先紧凑 stringify 测长（缩进美化会数倍膨胀长度），
    // 超限直接渲染紧凑截断串；未超限才做缩进美化。
    const compact = typeof content === 'string' ? content : JSON.stringify(content);
    if (compact.length > JSON_RENDER_MAX_CHARS) {
      return <JsonPre value={`${compact.slice(0, JSON_RENDER_MAX_CHARS)}\n…（内容过大，已截断显示）`} />;
    }
    const pretty = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    return <JsonPre value={pretty} />;
  }
  if (kind === ExecutionFileDTO.kind.MD) {
    return <MarkdownView content={String(content)} />;
  }
  return <pre className="whitespace-pre-wrap break-words text-sm">{String(content)}</pre>;
}

function JsonPre({ value }: { value: unknown }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <pre className="overflow-x-auto text-xs">{text}</pre>;
}

function SectionLabel({ children }: { children: string }) {
  return (
    <p className="text-xs font-medium" style={mutedStyle}>
      {children}
    </p>
  );
}

function MissingHint() {
  return (
    <p className="text-sm" style={mutedStyle}>
      未生成（调用可能未完成）
    </p>
  );
}

function nodeHasContent(node: ExecutionNodeDTO): boolean {
  return Boolean(node.llm_req || node.llm_res || node.dp_calls.length > 0 || node.tools.length > 0);
}

function layerNameOf(layers: ExecutionLayerDTO[], nodeDir: string): string | null {
  for (const layer of layers) {
    if (layer.nodes.some((n) => n.dir === nodeDir)) return layer.name;
  }
  return null;
}
