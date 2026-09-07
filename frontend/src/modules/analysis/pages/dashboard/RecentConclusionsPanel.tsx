import { Link } from 'react-router';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { useDashboardSectionQuery } from './queries';

// 最近结论：成功任务的服务端 conclusion_summary/risk_flag/risk_hint/has_report。
// conclusion_summary 为 null 时只显示任务元信息与查看完整报告入口，不得截取报告正文兜底。
export function RecentConclusionsPanel() {
  const query = useDashboardSectionQuery('recent_conclusions');

  if (query.isPending) return <LoadingState label="最近结论加载中…" />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const items = query.data;
  if (!items || items.length === 0) return <EmptyState title="暂无最近结论" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近结论</CardTitle>
        <Button asChild variant="ghost" size="sm">
          <Link to="/ai/tasks?status=succeeded">查看全部任务</Link>
        </Button>
      </CardHeader>
      <CardContent>
        {items.map((item) => (
          <Link
            key={item.task_id}
            to={`/ai/tasks/${item.task_id}${item.has_report ? '#report' : ''}`}
            className="rounded-md border p-3 hover:bg-[var(--color-bg)]"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge variant="success">成功</Badge>
                <span className="text-sm font-medium">{item.ticker ?? '全市场'}</span>
                {item.risk_flag && <Badge variant="warning">风险提示</Badge>}
              </div>
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {formatDateTime(item.completed_at)}
              </span>
            </div>
            <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
              {item.conclusion_summary ?? '（服务端暂无可靠摘要）'}
            </p>
            {item.risk_flag && item.risk_hint && (
              <p className="mt-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {item.risk_hint}
              </p>
            )}
            <p className="mt-1 text-xs underline" style={{ color: 'var(--color-accent)' }}>
              {item.has_report ? '查看完整报告' : '查看任务详情'}
            </p>
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}
