import { Badge } from '../../../../shared/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../../../shared/ui/dialog';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import type { TopologyNodeDTO } from '../../../../api/generated';
import { useExecutionLogsQuery } from './queries';
import { NodeDetails } from './ExecutionNodeDetails';
import { STATUS_META } from './GraphTopologyPanel';

// 拓扑节点弹窗：展示该节点本次运行的全部调用日志实例（dirs → ExecutionNodeDTO）。
// 数据复用 useExecutionLogsQuery（与执行日志面板同 query key 同缓存，零额外请求）；
// 未执行节点显示空态；多实例（辩论循环/逐票循环）按 dirs 顺序列出。

const STATUS_BADGE_VARIANT = {
  not_executed: 'secondary',
  executed: 'default',
  running: 'warning',
  error: 'destructive',
} as const;

interface NodeLogsDialogProps {
  taskId: string;
  terminal: boolean;
  node: TopologyNodeDTO | null;
  onClose: () => void;
}

export function NodeLogsDialog({ taskId, terminal, node, onClose }: NodeLogsDialogProps) {
  // 仅弹窗打开时订阅执行日志（同 query key 复用面板缓存，零额外请求）；
  // 关闭时不订阅——避免晚于面板挂载的观察者触发 refetchOnMount 二次拉取
  const logsQuery = useExecutionLogsQuery(taskId, node !== null, terminal);

  const open = node !== null;
  const logs = logsQuery.data;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-3xl" data-testid="node-logs-dialog">
        {node && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {node.label}
                <Badge variant={STATUS_BADGE_VARIANT[node.status]}>{STATUS_META[node.status].label}</Badge>
                {node.invocation_count > 1 && <Badge variant="outline">×{node.invocation_count}</Badge>}
              </DialogTitle>
              <DialogDescription>
                {node.invocation_count === 0
                  ? '该节点本次未产生日志目录'
                  : `共 ${node.invocation_count} 次调用${node.dirs.length > 1 ? '，按调用顺序列出' : ''}`}
              </DialogDescription>
            </DialogHeader>

            {node.status === 'not_executed' ? (
              <EmptyState title="该节点本次未执行" description="未找到匹配的日志目录" />
            ) : logsQuery.isPending ? (
              <LoadingState label="执行日志加载中…" />
            ) : logs?.available ? (
              node.dirs.length > 0 ? (
                <div className="flex flex-col gap-1">
                  {node.dirs.map((dir, index) => {
                    const detail = logs.layers
                      .flatMap((layer) => layer.nodes)
                      .find((n) => n.dir === dir);
                    return detail ? (
                      <NodeDetails key={dir} taskId={taskId} node={detail} defaultOpen={index === 0} />
                    ) : (
                      <p key={dir} className="text-sm px-1" style={{ color: 'var(--color-fg-muted)' }}>
                        {dir}（尚未出现在执行日志树中，轮询中）
                      </p>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title="无日志内容" description="该节点已执行但日志目录尚未写入" />
              )
            ) : (
              <EmptyState title="暂无执行日志" description="任务尚未开始写入或本次运行未产生日志" />
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
