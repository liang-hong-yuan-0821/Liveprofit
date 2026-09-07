import { useState } from 'react';
import type { HotConceptDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Button } from '../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { todayLocalDate, daysAgoLocalDate, formatDateTime } from '../../../shared/format/dateTime';
import { CandlestickChart } from '../../../shared/charts/CandlestickChart';
import { useHotConceptsQuery } from './queries';
import { barsToCandlestickViewModel } from './mappers/toChartViewModels';

// 板块区块（热门概念）：排名、热度原因、算法版本均来自服务端快照，前端不计算；
// 无热点（NO_HOT_CONCEPTS/空列表）是正常空态；STALE 可展示旧快照并标注；上游失败不使用静态概念兜底。
export function HotConceptsPanel() {
  const [from, setFrom] = useState(daysAgoLocalDate(60));
  const [to, setTo] = useState(todayLocalDate());
  const [rangeError, setRangeError] = useState<string | null>(null);

  function applyRange(nextFrom: string, nextTo: string) {
    setFrom(nextFrom);
    setTo(nextTo);
    setRangeError(nextFrom && nextTo && nextFrom > nextTo ? '开始日期不能晚于结束日期' : null);
  }

  const query = useHotConceptsQuery({ market: 'CN', interval: '1d', from, to });
  const items = query.data?.items ?? [];
  const snapshot = query.data;
  const stale = snapshot?.freshness_status === 'STALE';

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hot-from">开始日期</Label>
          <Input id="hot-from" type="date" value={from} onChange={(event) => applyRange(event.target.value, to)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hot-to">结束日期</Label>
          <Input id="hot-to" type="date" value={to} onChange={(event) => applyRange(from, event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5 pb-1 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          <span>市场 CN · 周期 1d</span>
          {snapshot && <span>快照 {snapshot.as_of ?? '—'} · {snapshot.algorithm_version}</span>}
          {stale && <Badge variant="warning">快照可能延迟</Badge>}
        </div>
      </div>
      {rangeError && <p className="text-xs text-red-400">{rangeError}</p>}

      {query.isPending && <LoadingState label="热门概念加载中…" />}

      {query.isError && !query.data && (
        <ErrorState
          error={toApiError(query.error)}
          onRetry={toApiError(query.error).retryable ? () => void query.refetch() : undefined}
        />
      )}

      {snapshot && items.length === 0 && (
        <EmptyState title="当前条件下暂无热点概念" />
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {items.map((concept) => (
            <ConceptCard key={concept.concept_code} concept={concept} />
          ))}
        </div>
      )}

    </div>
  );
}

function ConceptCard({ concept }: { concept: HotConceptDTO }) {
  const [showDaily, setShowDaily] = useState(false);
  const viewModel = barsToCandlestickViewModel(concept.bars);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>
            #{concept.rank} {concept.concept_name}
          </CardTitle>
          <p className="mt-0.5 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {concept.concept_code} · {formatDateTime(concept.updated_at)}
          </p>
        </div>
        {concept.period_return !== null && (
          <Badge variant={concept.period_return >= 0 ? 'destructive' : 'success'}>
            {concept.period_return >= 0 ? '+' : ''}{concept.period_return.toFixed(2)}%
          </Badge>
        )}
      </CardHeader>
      <CardContent>
        {concept.hotness_reason && (
          <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {concept.hotness_reason}
          </p>
        )}
        {viewModel && <CandlestickChart model={viewModel} height={160} />}
        {!viewModel && <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>K 线不可用</p>}
        {concept.daily_changes && concept.daily_changes.length > 0 && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => setShowDaily((prev) => !prev)}>
              {showDaily ? '收起近 10 日涨跌幅' : '展开近 10 日涨跌幅'}
            </Button>
            {showDaily && (
              <ul className="mt-1 flex flex-col gap-0.5 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                {concept.daily_changes.map((change, index) => (
                  <li key={index}>
                    {change.date}：{change.change_pct >= 0 ? '+' : ''}{change.change_pct}%
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
