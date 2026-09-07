import { useState } from 'react';
import { Link } from 'react-router';
import { toApiError } from '../../../../api/client';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { taskStatusLabel, taskStatusVariant } from '../../shared/taskStatus';
import { useAnalysisTasksQuery, type TaskListStatusFilter } from './queries';
import { useDeleteTaskMutation } from '../task-detail/queries';
import { ConfirmDialog } from '../../../../shared/ui/ConfirmDialog';
import type { TaskListItemDTO } from '../../../../api/generated';

// 任务摘要列表：仅渲染 TaskListItemDTO 允许字段；
// 固定沿服务端 updated_at DESC, id DESC 追加（useInfiniteQuery pageParam = meta.next_cursor），
// 下一页失败保留已加载结果。不承载 SSE、完整请求参数或报告正文。
export function AnalysisTaskList({ status }: { status: TaskListStatusFilter }) {
  const query = useAnalysisTasksQuery(status);
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const hasData = items.length > 0;
  const [deleteTarget, setDeleteTarget] = useState<TaskListItemDTO | null>(null);

  if (query.isPending) return <LoadingState label="任务列表加载中…" />;

  // 初始加载失败（无任何数据）才整区错误态；已有数据时保留列表，失败仅就地提示不覆盖已加载页
  if (query.isError && !hasData) {
    const error = toApiError(query.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void query.refetch() : undefined} />;
  }

  if (!hasData) {
    return (
      <EmptyState
        title="暂无任务"
        description="发起一次分析后，任务会出现在这里"
        action={
          <Button asChild variant="outline" size="sm">
            <Link to="/ai?create=1">新建分析</Link>
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col gap-2">
        {items.map((task) => (
          <TaskRow key={task.id} task={task} onDeleteRequest={setDeleteTarget} />
        ))}
      </ul>

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="outline"
            size="sm"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? '加载中…' : '加载更多'}
          </Button>
        </div>
      )}

      {query.isError && (
        <p className="text-center text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          下一页加载失败，已保留已加载结果。
          <Button variant="ghost" size="sm" onClick={() => void query.fetchNextPage()}>
            重试
          </Button>
        </p>
      )}

      <DeleteTaskConfirm target={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </div>
  );
}

// 行内删除：仅终态任务可删（非终态先取消）；删除成功后列表经精准失效自动刷新
function DeleteTaskConfirm({
  target,
  onClose,
}: {
  target: TaskListItemDTO | null;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteTaskMutation(target?.id ?? '');
  return (
    <ConfirmDialog
      open={target !== null}
      title={`删除任务「${target?.ticker ?? '全市场'}」`}
      description="将删除该任务及其报告（任务表/报告表同时删除）"
      pending={deleteMutation.isPending}
      onConfirm={() => deleteMutation.mutate(undefined, { onSuccess: onClose })}
      onCancel={onClose}
    />
  );
}

function TaskRow({ task, onDeleteRequest }: { task: TaskListItemDTO; onDeleteRequest: (task: TaskListItemDTO) => void }) {
  const terminal = task.status === 'SUCCEEDED' || task.status === 'FAILED' || task.status === 'CANCELLED';
  return (
    <li>
      <Link
        to={`/ai/tasks/${task.id}`}
        className="flex items-center justify-between gap-4 rounded-md border p-3 hover:bg-[var(--color-surface)]"
        style={{ borderColor: 'var(--color-border)' }}
      >
        <div className="flex items-center gap-3">
          <Badge variant="outline">{task.task_type === 'SINGLE_STOCK' ? '单股' : '全市场'}</Badge>
          <span className="text-sm font-medium">{task.ticker ?? '—'}</span>
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {task.effective_trade_date ?? '—'}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={taskStatusVariant(task.status)}>{taskStatusLabel(task.status)}</Badge>
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            第 {task.attempt_no} 次尝试
          </span>
          {task.status === 'FAILED' && task.error_summary && (
            <span className="max-w-48 truncate text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              {task.error_summary}
            </span>
          )}
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {formatDateTime(task.updated_at)}
          </span>
          {terminal && (
            <Button
              size="sm"
              variant="ghost"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onDeleteRequest(task);
              }}
            >
              删除
            </Button>
          )}
        </div>
      </Link>
    </li>
  );
}
