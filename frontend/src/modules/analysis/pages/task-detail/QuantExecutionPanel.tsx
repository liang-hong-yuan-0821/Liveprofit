// 量化执行面板（plan 4.4.1）：策略审计/组合快照/全市场买点/持仓信号/建议订单/告警。
// 买点/持仓/订单以 cursor 分页加载完整结果（后端 DTO 预览仅前 50 条）；
// 建议订单明确标注「需人工确认，未下单」；被风控拒绝的 BUY 仍显示在买点列表。

import { useState } from 'react';

import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import type { QuantExecutionDTO } from '../../../../api/generated';
import { useQuantSignalsInfiniteQuery, type QuantSignalKind } from '../strategies/queries';

const fmt = (v: number | null | undefined): string => (v == null ? '-' : v.toFixed(2));

export function QuantExecutionPanel({ taskId, quant }: { taskId: string; quant: QuantExecutionDTO }) {
  const [tab, setTab] = useState<QuantSignalKind>('buy');
  const s = quant.summary;

  return (
    <Card aria-label="量化执行">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>全市场量化买点</span>
          <span className="text-xs font-normal" style={{ color: 'var(--color-fg-muted)' }}>
            {quant.strategy.name} v{quant.strategy.version_no} · 源码 {quant.strategy.source_hash_prefix}
            {quant.strategy.published_at ? ` · 发布于 ${formatDateTime(quant.strategy.published_at)}` : ''}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="rounded-md border p-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          组合快照 {quant.portfolio_snapshot.name}（v{quant.portfolio_snapshot.version}）·
          总资产 {Number(quant.portfolio_snapshot.total_assets).toFixed(2)} ·
          可用现金 {Number(quant.portfolio_snapshot.available_cash).toFixed(2)}
          {quant.valued_at ? ` · 估值于 ${formatDateTime(quant.valued_at)}` : ''}
        </div>

        <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <Stat label="扫描标的" value={s.universe_total} />
          <Stat label="数据完备" value={s.data_complete} />
          <Stat label="命中买点" value={s.buy_matches} />
          <Stat label="失败" value={s.failed_count} />
          <Stat label="建议买入" value={s.suggested_buy_orders} />
          <Stat label="建议卖出" value={s.suggested_sell_orders} />
        </div>

        {quant.warnings.length > 0 && (
          <div className="flex flex-col gap-1 rounded-md border p-2">
            {quant.warnings.map((w, i) => (
              <p key={i} className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                ⚠ {w}
              </p>
            ))}
          </div>
        )}

        <div className="flex gap-2">
          {(['buy', 'holding', 'orders'] as const).map((t) => (
            <Button key={t} size="sm" variant={tab === t ? 'default' : 'outline'} onClick={() => setTab(t)}>
              {t === 'buy' ? '买点' : t === 'holding' ? '持仓信号' : '建议订单'}
            </Button>
          ))}
        </div>

        <SignalList taskId={taskId} kind={tab} total={tab === 'buy' ? s.buy_matches : undefined} />
      </CardContent>
    </Card>
  );
}

function SignalList({ taskId, kind, total }: { taskId: string; kind: QuantSignalKind; total?: number }) {
  const query = useQuantSignalsInfiniteQuery(taskId, kind);
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const hasMore = Boolean(query.data?.pages.at(-1)?.next_cursor);

  if (query.isLoading) return <p className="p-2 text-sm">加载中…</p>;
  if (query.isError) {
    return <p className="p-2 text-sm text-red-400">信号列表加载失败，请刷新页面重试</p>;
  }

  return (
    <div className="flex flex-col gap-1" aria-label={kind === 'buy' ? '买点列表' : kind === 'holding' ? '持仓信号' : '建议订单'}>
      {kind === 'orders' && (
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          建议订单（需人工确认，未下单）——系统不会自动下单、不改持仓。
        </p>
      )}
      {items.length === 0 && <p className="p-2 text-sm" style={{ color: 'var(--color-fg-muted)' }}>无数据</p>}
      {items.map((row, index) => (
        <div key={`${row.ts_code}-${row.id}-${index}`} className="flex items-center justify-between rounded-md border p-2 text-sm">
          <span className="flex items-center gap-2">
            {row.ts_code}
            {kind === 'buy' && (
              <>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>score {fmt(row.score)}</span>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>{row.reason}</span>
                <span className="text-xs">
                  入场 {fmt(row.entry_price)} · 止损 {fmt(row.stop_loss)} · 止盈 {fmt(row.take_profit)}
                </span>
              </>
            )}
            {kind === 'holding' && (
              <>
                <Badge variant="outline">{row.action ?? '-'}</Badge>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>{row.reason}</span>
              </>
            )}
            {kind === 'orders' && (
              <>
                <Badge variant={row.action === 'BUY' ? 'default' : 'warning'}>{row.action}</Badge>
                <span className="text-xs">{fmt(row.shares)} 股 · 金额 {fmt(row.notional)}</span>
                <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                  成本价 {fmt(row.order_cost_price)} · 估值 {fmt(row.valuation_price)}
                </span>
                {row.risk_bucket?.industry_name && (
                  <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    行业 {row.risk_bucket.industry_name}
                  </span>
                )}
              </>
            )}
          </span>
          {kind !== 'orders' && <OrderStatusBadge status={row.order_status} />}
        </div>
      ))}
      {hasMore && (
        <div className="flex justify-center p-2">
          <Button size="sm" variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {query.isFetchingNextPage ? '加载中…' : '加载更多'}
          </Button>
        </div>
      )}
      {total !== undefined && (
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          已显示 {items.length}/{total}
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border p-2">
      <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>{label}</p>
      <p className="text-base font-semibold">{value}</p>
    </div>
  );
}

function OrderStatusBadge({ status }: { status: string | null | undefined }) {
  if (status == null) return null;
  const variant = status === 'ELIGIBLE' ? 'success' : status.startsWith('BUY_REJECTED') || status.startsWith('SELL_') ? 'warning' : 'outline';
  return <Badge variant={variant}>{status}</Badge>;
}
