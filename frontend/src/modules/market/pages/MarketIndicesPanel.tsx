import { useState } from 'react';
import type { MarketAssetDTO } from '../../../api/generated';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { Input } from '../../../shared/ui/input';
import { Label } from '../../../shared/ui/label';
import { todayLocalDate, daysAgoLocalDate } from '../../../shared/format/dateTime';
import { CandlestickChart } from '../../../shared/charts/CandlestickChart';
import { useMarketAssetsQuery, useMarketBarsQuery } from './queries';
import { barsToCandlestickViewModel, groupAssetsByMarket } from './mappers/toChartViewModels';

// 市场区块（宏观指数）：先读资产目录，仅对 availability_status=AVAILABLE 的资产请求 bars；
// 固定 US → KR → CN 分组、组内按服务端 display_order 排序，不补充前端资产。
const MARKET_LABELS: Record<string, string> = { US: '美国', KR: '韩国', CN: '中国' };

export function MarketIndicesPanel() {
  const assetsQuery = useMarketAssetsQuery();

  if (assetsQuery.isPending) return <LoadingState label="市场指数加载中…" />;
  if (assetsQuery.isError) {
    const error = toApiError(assetsQuery.error);
    return <ErrorState error={error} onRetry={error.retryable ? () => void assetsQuery.refetch() : undefined} />;
  }

  const groups = groupAssetsByMarket(assetsQuery.data?.items ?? []);

  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <section key={group.market} aria-label={`${MARKET_LABELS[group.market]}市场`}>
          <h3 className="mb-2 text-sm font-semibold">{MARKET_LABELS[group.market]}（{group.market}）</h3>
          {group.assets.length === 0 ? (
            <EmptyState title="该市场暂无可用资产" />
          ) : (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {group.assets.map((asset) => (
                <IndexCandlestickSection key={`${asset.market}-${asset.symbol}`} asset={asset} />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}

function IndexCandlestickSection({ asset }: { asset: MarketAssetDTO }) {
  const [interval] = useState(asset.supported_intervals[0] ?? '1d');
  const [from, setFrom] = useState(daysAgoLocalDate(60));
  const [to, setTo] = useState(todayLocalDate());
  const [rangeError, setRangeError] = useState<string | null>(null);

  const available = asset.availability_status === 'AVAILABLE';
  const rangeValid = from !== '' && to !== '' && from <= to;

  function applyRange(nextFrom: string, nextTo: string) {
    setFrom(nextFrom);
    setTo(nextTo);
    if (nextFrom && nextTo && nextFrom > nextTo) {
      setRangeError('开始日期不能晚于结束日期');
    } else {
      setRangeError(null);
    }
  }

  const barsQuery = useMarketBarsQuery(
    asset.symbol,
    { market: asset.market, interval, from, to },
    available && rangeValid,
  );

  if (!available) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{asset.name}</CardTitle>
          <Badge variant="secondary">不可用</Badge>
        </CardHeader>
        <CardContent>
          <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            {asset.availability_status === 'DISABLED'
              ? '该资产未启用'
              : '该资产尚未通过实测验收，暂不可用'}
          </p>
        </CardContent>
      </Card>
    );
  }

  const bars = barsQuery.data?.bars ?? [];
  const viewModel = barsQuery.data ? barsToCandlestickViewModel(bars, barsQuery.data.indicators) : null;
  const error = barsQuery.isError ? toApiError(barsQuery.error) : null;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{asset.name}</CardTitle>
          <p className="mt-0.5 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            {asset.symbol} · {asset.currency} · {asset.market_timezone}
          </p>
        </div>
        {barsQuery.data && <FreshnessBadges data={barsQuery.data} />}
      </CardHeader>
      <CardContent>
        <div className="flex items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`from-${asset.symbol}`}>开始日期</Label>
            <Input
              id={`from-${asset.symbol}`}
              type="date"
              value={from}
              onChange={(event) => applyRange(event.target.value, to)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`to-${asset.symbol}`}>结束日期</Label>
            <Input
              id={`to-${asset.symbol}`}
              type="date"
              value={to}
              onChange={(event) => applyRange(from, event.target.value)}
            />
          </div>
          <span className="pb-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            周期 {interval}
          </span>
        </div>
        {rangeError && <p className="text-xs text-red-400">{rangeError}</p>}

        {barsQuery.isPending && <LoadingState label="K 线加载中…" />}

        {error && !rangeError && (
          <ErrorState
            error={error}
            onRetry={error.retryable ? () => void barsQuery.refetch() : undefined}
          />
        )}

        {barsQuery.data && viewModel && (
          <>
            <CandlestickChart model={viewModel} />
            <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              来源 {barsQuery.data.source ?? '—'} · as_of {barsQuery.data.as_of ?? '—'}
              {barsQuery.data.source_updated_at ? ` · 源更新时间 ${barsQuery.data.source_updated_at}` : ''}
            </p>
          </>
        )}
        {barsQuery.data && !viewModel && (
          <EmptyState title="无可展示时序" description={barsQuery.data.market_closed_reason ?? '暂无该区间数据'} />
        )}
      </CardContent>
    </Card>
  );
}

function FreshnessBadges({ data }: { data: NonNullable<ReturnType<typeof useMarketBarsQuery>['data']> }) {
  const stale = data.freshness_status === 'STALE';
  const unavailable = data.freshness_status === 'UNAVAILABLE';
  const closed = data.market_session_status === 'CLOSED';
  return (
    <div className="flex flex-col items-end gap-1">
      <Badge variant={unavailable ? 'secondary' : stale ? 'warning' : 'success'}>
        {unavailable ? '无可展示时序' : stale ? '数据可能延迟' : '数据新鲜'}
      </Badge>
      {closed && (
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          闭市：{data.market_closed_reason ?? '—'}
        </span>
      )}
    </div>
  );
}
