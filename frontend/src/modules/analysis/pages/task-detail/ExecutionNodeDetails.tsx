import { useState } from 'react';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleHeader } from '../../../../shared/ui/collapsible';
import { MarkdownView } from '../../../../shared/ui/markdown';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import type {
  ExecutionDpCallDTO,
  ExecutionNodeDTO,
  ExecutionToolDTO,
  ExecutionTushareDTO,
} from '../../../../api/generated';
// ExecutionFileDTO 携带 kind 枚举命名空间（值导入），不能 import type
import { ExecutionFileDTO } from '../../../../api/generated';

// 执行日志节点级渲染块（ExecutionLogsPanel 与 NodeLogsDialog 共用）：
// 单个节点目录的完整调用详情（DP 调用/LLM 提示词/工具调用/LLM 输出/meta）。
// 自 ExecutionLogsPanel 原样迁出（任务拓扑图方案 §3.3.1），渲染行为零改动；
// 预测目录（无节点级 meta.json，如 Screening）按既有行为显示"未知节点 · 未知模型"。

const JSON_RENDER_MAX_CHARS = 200_000; // 大 JSON 渲染保护：超限截断显示

const mutedStyle = { color: 'var(--color-fg-muted)' } as const;

export interface NodeDetailsProps {
  taskId: string;
  node: ExecutionNodeDTO;
  /** 受控展开（日志面板用）：open 与 onOpenChange 需同时提供 */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 非受控默认展开（弹窗单实例用） */
  defaultOpen?: boolean;
}

export function NodeDetails({ taskId, node, open, onOpenChange, defaultOpen = false }: NodeDetailsProps) {
  const title = `${node.dir.split('/').pop() ?? node.dir}（${node.node ?? '未知节点'} · ${node.model ?? '未知模型'}）`;
  const controlled = open !== undefined && onOpenChange !== undefined;
  return (
    <Collapsible {...(controlled ? { open, onOpenChange } : { defaultOpen })}>
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

export function DpCallBlock({ taskId, dp }: { taskId: string; dp: ExecutionDpCallDTO }) {
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

export function TushareBlock({ taskId, ts }: { taskId: string; ts: ExecutionTushareDTO }) {
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

export function ToolBlock({ taskId, tool }: { taskId: string; tool: ExecutionToolDTO }) {
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
export function FileBody({ taskId, file }: { taskId: string; file: ExecutionFileDTO }) {
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

export function FileContent({
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

export function JsonPre({ value }: { value: unknown }) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return <pre className="overflow-x-auto text-xs">{text}</pre>;
}

export function SectionLabel({ children }: { children: string }) {
  return (
    <p className="text-xs font-medium" style={mutedStyle}>
      {children}
    </p>
  );
}

export function MissingHint() {
  return (
    <p className="text-sm" style={mutedStyle}>
      未生成（调用可能未完成）
    </p>
  );
}

export function nodeHasContent(node: ExecutionNodeDTO): boolean {
  return Boolean(node.llm_req || node.llm_res || node.dp_calls.length > 0 || node.tools.length > 0);
}
