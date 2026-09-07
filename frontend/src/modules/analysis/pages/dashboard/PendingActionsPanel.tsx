import { Link } from 'react-router';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { Badge } from '../../../../shared/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { useDashboardSectionQuery } from './queries';
import type { PendingActionDTO } from '../../../../api/generated';

// 待我处理：仅 pending_actions（终态 FAILED 或成功任务的报告区块 UNAVAILABLE）；
// NOT_REQUESTED 不视为待办，RETRYING/CANCELLED 不进入待处理。
export function PendingActionsPanel() {
  const query = useDashboardSectionQuery('pending_actions');

  if (query.isPending) return <LoadingState label="待我处理加载中…" />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const items = query.data;
  if (!items || items.length === 0) return <EmptyState title="当前没有待处理事项" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>待我处理</CardTitle>
      </CardHeader>
      <CardContent>
        {items.map((item) => (
          <PendingActionRow key={`${item.kind}-${item.task_id}`} item={item} />
        ))}
      </CardContent>
    </Card>
  );
}

function PendingActionRow({ item }: { item: PendingActionDTO }) {
  const isFailed = item.kind === 'FAILED_TASK';
  const target = `/ai/tasks/${item.task_id}${isFailed ? '' : '#report'}`;

  return (
    <Link
      to={target}
      className="rounded-md border p-3 hover:bg-[var(--color-bg)]"
      style={{ borderColor: 'var(--color-border)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={isFailed ? 'destructive' : 'warning'}>
            {isFailed ? '任务失败' : '报告区块不可用'}
          </Badge>
          <span className="text-sm font-medium">{item.ticker ?? '全市场'}</span>
        </div>
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          {formatDateTime(item.updated_at)}
        </span>
      </div>
      <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
        {isFailed
          ? (item.error_summary ?? item.error_code ?? '任务执行失败')
          : (item.unavailable_blocks ?? [])
              .map((block) => `${block.block}${block.reason ? `：${block.reason}` : ''}`)
              .join('；')}
      </p>
    </Link>
  );
}
