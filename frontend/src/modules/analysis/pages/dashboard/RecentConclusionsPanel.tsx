import { unavailableSummary } from '../../shared/resultPresentation';
import { Link } from 'react-router';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { layersName } from '../../shared/analysisLayers';
import { useDashboardSectionQuery } from './queries';

// 最近结论：成功任务的服务端 conclusion_summary/risk_flag/risk_hint/has_report。
// conclusion_summary 为 null 时只显示任务元信息与查看完整报告入口，不得截取报告正文兜底。
export function RecentConclusionsPanel() {
  const query = useDashboardSectionQuery('recent_conclusions');

  if (query.isPending) return <LoadingState label="最近结论加载中…" />;
  if (query.isError && !query.data) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const items = query.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近结论</CardTitle>
        <Button asChild variant="ghost" size="sm">
          <Link to="/ai/tasks?status=succeeded">查看全部任务</Link>
        </Button>
      </CardHeader>
      <CardContent>
        {items.length === 0 && <EmptyState title="暂无最近结论" />}
        {query.isError && <p role="status" className="text-xs text-amber-500">更新失败，当前显示上次结果。<button type="button" className="ml-2 underline" onClick={() => void query.refetch()}>重新加载</button></p>}
        {items.map((item) => (
          <Link
            key={item.task_id}
            to={`/ai/tasks/${item.task_id}${item.has_report ? '#report' : ''}`}
            className="rounded-md border p-3 hover:bg-[var(--color-bg)]"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="success">执行完成</Badge>
                {!!item.unavailable_blocks?.length && <Badge variant="warning">报告部分不可用</Badge>}
                <span className="text-sm font-medium">{layersName(item.selected_layers)}</span>
                {item.ticker && (
                  <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    {item.ticker}
                  </span>
                )}
                {item.risk_flag && <Badge variant="warning">风险提示</Badge>}
              </div>
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {formatDateTime(item.completed_at)}
              </span>
            </div>
            <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
              {item.conclusion_summary ?? '暂无摘要，可查看报告'}
            </p>
            {!!item.unavailable_blocks?.length && <p className="mt-2 text-xs text-amber-500">{unavailableSummary(item.unavailable_blocks)}</p>}
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
