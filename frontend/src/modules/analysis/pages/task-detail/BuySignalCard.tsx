import { useEffect, useRef, useState } from 'react';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { CandlestickChart, type CandlestickReferenceLine } from '../../../../shared/charts/CandlestickChart';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { todayLocalDate } from '../../../../shared/format/dateTime';
import { useStockBarsQuery } from '../../../../modules/market/pages/queries';
import { barsToCandlestickViewModel } from '../../../../modules/market/pages/mappers/toChartViewModels';
import type { QuantSignalRowDTO } from '../../../../api/generated';

// 买点卡片（买点列表 K 线增强）：每个命中标的 = 一张卡片（标的信息 + 个股日线 K 线）。
// K 线窗口锚定信号日（无信号日回退今天）往前 180 个自然日；与指数 K 线同款渲染——
// indicators（MA/BOLL/MACD）透传 mapper，止损/止盈以参考价横线直画在图上（虚线，
// 红涨绿跌：止盈红、止损绿）。滚动进入视口才发起行情请求（全市场命中可能数十个，
// 避免一次性打满行情接口）；jsdom 无 IntersectionObserver，测试环境退化为立即请求。

const KLINE_CARD_DAYS = 180;
const KLINE_CARD_HEIGHT = 400;

const fmt = (v: number | null | undefined): string => (v == null ? '-' : v.toFixed(2));

export function BuySignalCard({ row }: { row: QuantSignalRowDTO }) {
  const { ref, inView } = useInView();
  const to = row.signal_trade_date ?? todayLocalDate();
  const barsQuery = useStockBarsQuery(
    row.ts_code,
    { market: 'CN', interval: '1d', from: daysAgoFrom(to, KLINE_CARD_DAYS), to },
    inView,
  );
  const viewModel = barsQuery.data
    ? barsToCandlestickViewModel(barsQuery.data.bars, barsQuery.data.indicators)
    : null;
  // 止损/止盈参考价横线（策略价口径，与卡片文字同源）；缺失不画
  const referenceLines: CandlestickReferenceLine[] = [
    ...(row.stop_loss != null
      ? [{ price: row.stop_loss, label: `止损 ${fmt(row.stop_loss)}`, color: '#22c55e' }]
      : []),
    ...(row.take_profit != null
      ? [{ price: row.take_profit, label: `止盈 ${fmt(row.take_profit)}`, color: '#ef4444' }]
      : []),
  ];

  return (
    <div ref={ref} className="flex flex-col gap-2 rounded-md border p-3" style={{ borderColor: 'var(--color-border)' }}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className="font-medium">{row.ts_code}</span>
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>评分 {fmt(row.score)}</span>
          <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>{row.reason}</span>
        </div>
        <OrderStatusBadge status={row.order_status} />
      </div>

      <div className="flex flex-col gap-0.5 text-xs">
        <span>
          策略价（{row.signal_price_basis === 'raw' ? '原始价格 raw' : '前复权 qfq'}）入场 {fmt(row.entry_price)} · 止损 {fmt(row.stop_loss)} · 止盈 {fmt(row.take_profit)}
        </span>
        {row.order_entry_price != null && (
          <span style={{ color: 'var(--color-fg-muted)' }}>
            订单候选（{row.execution_price_basis === 'qfq' ? '前复权 qfq' : '原始价格 raw'}）入场 {fmt(row.order_entry_price)} · 止损 {fmt(row.order_stop_price)} · 止盈 {fmt(row.order_take_price)}
          </span>
        )}
      </div>

      {/* 未入视口不发起请求，也不显示加载态（避免屏外卡片误示「加载中」） */}
      {inView && barsQuery.isPending && <LoadingState label="K 线加载中…" />}
      {inView && barsQuery.isError && !barsQuery.data && (
        <p className="flex items-center gap-2 text-xs text-red-400">
          K 线加载失败
          <Button variant="ghost" size="sm" onClick={() => void barsQuery.refetch()}>
            重试
          </Button>
        </p>
      )}
      {inView && barsQuery.data && !viewModel && (
        <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>无可展示时序</p>
      )}
      {viewModel && (
        <CandlestickChart
          model={viewModel}
          height={KLINE_CARD_HEIGHT}
          referenceLines={referenceLines.length > 0 ? referenceLines : undefined}
        />
      )}
    </div>
  );
}

/** 订单状态徽章（买点卡片与持仓/订单行共用）。 */
export function OrderStatusBadge({ status }: { status: string | null | undefined }) {
  if (status == null) return null;
  const variant = status === 'ELIGIBLE' ? 'success' : status.startsWith('BUY_REJECTED') || status.startsWith('SELL_') ? 'warning' : 'outline';
  return <Badge variant={variant}><span title={status}>{status === 'ELIGIBLE' ? '符合下单条件' : status.startsWith('BUY_REJECTED') ? '买入受限' : status.startsWith('SELL_') ? '卖出需关注' : status}</span>{status !== 'ELIGIBLE' && <code className="ml-1 text-[10px]">{status}</code>}</Badge>;
}

/** 滚动进入视口后触发（一次性）：入场即断开监听。 */
function useInView() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // jsdom 无 IntersectionObserver：测试环境直接标记可见
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, inView };
}

/** YYYY-MM-DD 往前推 N 个自然日（信号日锚定窗口，无时区换算）。 */
function daysAgoFrom(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() - days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
