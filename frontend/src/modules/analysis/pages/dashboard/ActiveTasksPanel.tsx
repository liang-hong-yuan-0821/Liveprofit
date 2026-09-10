import { Link } from 'react-router';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { taskStatusLabel, taskStatusVariant } from '../../shared/taskStatus';
import { useDashboardSectionQuery } from './queries';

// 进行中：仅 PENDING/QUEUED/RUNNING/RETRYING；展示正式状态摘要，
// 不订阅进度（进度只由任务详情 SSE 提供）。
export function ActiveTasksPanel() {
  const query = useDashboardSectionQuery('active_tasks');

  if (query.isPending) return <LoadingState label="进行中任务加载中…" />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const items = query.data;
  // 空态也保留卡片占位，保持三列布局完整
  if (!items || items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>进行中</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-4 text-center text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            无进行中任务
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>进行中</CardTitle>
        <Button asChild variant="ghost" size="sm">
          <Link to="/ai/tasks?status=active">查看全部</Link>
        </Button>
      </CardHeader>
      <CardContent>
        {items.map((task) => (
          <Link
            key={task.task_id}
            to={`/ai/tasks/${task.task_id}`}
            className="rounded-md border p-3 hover:bg-[var(--color-bg)]"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge variant={taskStatusVariant(task.status)}>{taskStatusLabel(task.status)}</Badge>
                <span className="text-sm font-medium">{task.ticker ?? '全市场'}</span>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                  第 {task.attempt_no} 次尝试
                </span>
              </div>
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {formatDateTime(task.updated_at)}
              </span>
            </div>
            {task.next_retry_at && (
              <p className="mt-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                下次重试：{formatDateTime(task.next_retry_at)}
              </p>
            )}
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}
