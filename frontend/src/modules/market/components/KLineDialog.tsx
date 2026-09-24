import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../../shared/ui/dialog';
import { CandlestickChart } from '../../../shared/charts/CandlestickChart';
import { loadDrawings, saveDrawings, type Drawing } from '../../../shared/charts/drawings';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { requestEnvelope, toApiError } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import type { BarsData } from '../../../api/generated';
import { daysBefore, useMarketDate } from '../pages/refreshQueries';
import { barsToCandlestickViewModel } from '../pages/mappers/toChartViewModels';
import { useConceptBarsQuery, useStockBarsQuery } from '../pages/queries';
import type { TreemapNodeClick } from './conceptTreeOption';

// 点击弹窗 K 线（板块概念Treemap方案 3.4 + m7 修订）：概念 → 板块指数 K 线
// （库内积累长度，首跑 ~33 根、逐日增长）、个股 → 180 天窗口内全部可用日线；
// 首版固定窗口不做渐进加载（全历史窗口另案）。查询 enabled 门控 = 弹窗打开
// （react-query-dialog 坑：共享 key 弹窗订阅必须 enabled 门控），关闭即卸载、
// 不留查询。
//
// m7（用户拍板 2026-09-16）：与指数 K 线同款渲染——indicators（MA/BOLL/MACD）
// 透传给 mapper（概念 = 后端自算、个股 = stk_factor_pro 按需拉取入库，均为后端
// 职责）+ 画线工具（按 node.code 持久化，localStorage 与指数卡同规则）。高度与
// 宽度对齐指数卡（460 / max-w-4xl）以容纳双副图 + 图例。

const KLINE_DIALOG_DAYS = 180;

export interface KLineDialogProps {
  node: TreemapNodeClick;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function KLineDialog({ node, open, onOpenChange }: KLineDialogProps) {
  const client = useQueryClient();
  const marketDate = useMarketDate('CN');
  // This is a user-selected window. Server midnight must not re-run factor ensure.
  const [windowEnd] = useState(marketDate);
  const [visibleRange, setVisibleRange] = useState<{ start: string; end: string }>();
  const onDataZoom = useCallback((range: { start: string; end: string }) => setVisibleRange(range), []);
  const ensured = useRef(new Set<string>());
  const [drawings, setDrawings] = useState<Drawing[]>(() => loadDrawings(node.code));
  useEffect(() => {
    saveDrawings(node.code, drawings);
  }, [node.code, drawings]);

  const filters = {
    market: 'CN',
    interval: '1d',
    from: daysBefore(windowEnd, KLINE_DIALOG_DAYS),
    to: windowEnd,
  };
  const { market, interval, from, to } = filters;
  useEffect(() => {
    if (!open || node.kind !== 'stock') return;
    const trigger = `${node.code}:${from}:${to}`;
    if (ensured.current.has(trigger)) return;
    ensured.current.add(trigger);
    const key = queryKeys.stockBars.list({ symbol: node.code, market, interval, from, to });
    // Explicit user interaction only. Automatic query/refetch always uses cache_only.
    void requestEnvelope<BarsData>(MarketDataService.stockBarsApiV1MarketDataStocksSymbolBarsGet(node.code, market, interval, from, to, 'ensure'))
      .then(async ({ data }) => {
        await client.cancelQueries({ queryKey: key, exact: true });
        client.setQueryData(key, data);
      }).catch(() => { /* Cached bars remain usable; the backend enforces ensure cooldown. */ });
  }, [client, node.kind, node.code, open, market, interval, from, to]);
  const conceptQuery = useConceptBarsQuery(
    node.kind === 'concept' ? node.code : '',
    { ...filters, source: 'dc' },
    open && node.kind === 'concept',
  );
  const stockQuery = useStockBarsQuery(
    node.kind === 'stock' ? node.code : '',
    filters,
    open && node.kind === 'stock',
  );
  const query = node.kind === 'concept' ? conceptQuery : stockQuery;
  const viewModel = query.data
    ? barsToCandlestickViewModel(query.data.bars, query.data.indicators)
    : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {node.name} <span className="ml-1 text-sm font-normal" style={{ color: 'var(--color-fg-muted)' }}>{node.code}</span>
          </DialogTitle>
        </DialogHeader>
        {query.isPending && <LoadingState label="K 线加载中…" />}
        {query.isError && !query.data && (
          <ErrorState
            error={toApiError(query.error)}
            onRetry={toApiError(query.error).retryable ? () => void query.refetch() : undefined}
          />
        )}
        {query.data && !viewModel && <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>K 线不可用</p>}
        {viewModel && (
          <CandlestickChart
            model={viewModel}
            height={460}
            drawings={drawings}
            onDrawingsChange={setDrawings}
            visibleRange={visibleRange}
            onDataZoom={onDataZoom}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
