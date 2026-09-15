import { useEffect, useRef, useState } from 'react';
import { toApiError } from '../../../api/client';
import { ErrorState } from '../../../shared/feedback/ErrorState';
import { EmptyState } from '../../../shared/feedback/EmptyState';
import { LoadingState } from '../../../shared/feedback/LoadingState';
import { Badge } from '../../../shared/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { todayLocalDate, daysAgoLocalDate, daysBetween } from '../../../shared/format/dateTime';
import { CandlestickChart } from '../../../shared/charts/CandlestickChart';
import { loadDrawings, saveDrawings, type Drawing } from '../../../shared/charts/drawings';
import { useMarketBarsQuery } from './queries';
import { barsToCandlestickViewModel } from './mappers/toChartViewModels';

// 平台资产目录前端写死（证券市场数据库统一方案 2.2/决策 4）：11 指数
// {market, symbol, name, currency, market_timezone, availability}——值转录自
// 原 market_assets 表现状（组序 = 原 display_order 固化：US → KR → CN）；
// US 3 + KS11 已实测验收 → AVAILABLE（2026-09-14 US/KR 上线方案）；
// KOSDAQ 无可用数据源（tushare index_global 无 KQ11、东财本机空表、
// 新浪环球表无此品种），用户拍板从目录移除（目录字段名 availability
// 刻意区别于已删门控列 availability_status）。
// 与 db.instrument INDEX_TARGETS（13 个：CN 9 + US 3 + KS11）的同步不变式：
// CN 7 与后端采集清单的 CN 子集必须一致；US/KR 项变更需实测验收 + 两处同步。
const MARKET_INDEX_CATALOG: CatalogEntry[] = [
  { market: 'US', symbol: '.INX', name: '标普500', currency: 'USD', market_timezone: 'America/New_York', availability: 'AVAILABLE' },
  { market: 'US', symbol: '.DJI', name: '道琼斯工业指数', currency: 'USD', market_timezone: 'America/New_York', availability: 'AVAILABLE' },
  { market: 'US', symbol: '.IXIC', name: '纳斯达克综合指数', currency: 'USD', market_timezone: 'America/New_York', availability: 'AVAILABLE' },
  { market: 'KR', symbol: 'KS11', name: '韩国综合指数', currency: 'KRW', market_timezone: 'Asia/Seoul', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '000001.SH', name: '上证综指', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '399001.SZ', name: '深证成指', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '399006.SZ', name: '创业板指', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '000688.SH', name: '科创50', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '000016.SH', name: '上证50', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '000852.SH', name: '中证1000', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
  { market: 'CN', symbol: '000015.SH', name: '上证红利', currency: 'CNY', market_timezone: 'Asia/Shanghai', availability: 'AVAILABLE' },
];

interface CatalogEntry {
  market: string;
  symbol: string;
  name: string;
  currency: string;
  market_timezone: string;
  availability: 'AVAILABLE' | 'UNAVAILABLE';
}

// 市场区块分组序（原 MARKET_GROUP_ORDER US → KR → CN 固化）
const MARKET_GROUP_ORDER: string[] = ['US', 'KR', 'CN'];
const MARKET_LABELS: Record<string, string> = { US: '美国', KR: '韩国', CN: '中国' };
const DEFAULT_INTERVAL = '1d';

export function MarketIndicesPanel() {
  const groups = MARKET_GROUP_ORDER.map((market) => ({
    market,
    assets: MARKET_INDEX_CATALOG.filter((entry) => entry.market === market),
  }));

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

// 按需加载常量（§3.4）：初始预拉 6 个月、默认展示近 3 个月；缓冲不变量 loaded ≥ 2×visible
const INITIAL_LOADED_DAYS = 180;
const INITIAL_VISIBLE_DAYS = 90;
const BUFFER_RATIO = 2; // 缓冲不变量：loaded ≥ 2 × visible
const HYSTERESIS = 1.1; // 10% 迟滞，防滚轮逐像素触发重复扩展
const PAN_EDGE_RATIO = 0.5; // 左平移触发：可见窗口左缘距加载左界 < 半屏即预拉

function IndexCandlestickSection({ asset }: { asset: CatalogEntry }) {
  const interval = DEFAULT_INTERVAL;
  const available = asset.availability === 'AVAILABLE';
  const [loadedFrom, setLoadedFrom] = useState(daysAgoLocalDate(INITIAL_LOADED_DAYS));
  const [visible, setVisible] = useState({
    start: daysAgoLocalDate(INITIAL_VISIBLE_DAYS),
    end: todayLocalDate(),
  });
  const [exhausted, setExhausted] = useState(false);
  // 画线持久化（§3.6.1）：组件已按 symbol 作 key（MarketIndicesPanel 渲染处），
  // 切标的自动重挂载 → 懒初始化即载入对应标的 key，无需 symbol 变化 effect
  const [drawings, setDrawings] = useState<Drawing[]>(() => loadDrawings(asset.symbol));
  const prevFirstBarRef = useRef<string | null>(null); // 上一次真实响应的首根 bar 日期
  const lastJudgedFromRef = useRef<string | null>(null); // 上次参与到头判定的响应 from
  const lastReportedRef = useRef<string | null>(null); // 上次上报的可见窗口键（逐像素去重）

  useEffect(() => {
    saveDrawings(asset.symbol, drawings);
  }, [asset.symbol, drawings]);

  const barsQuery = useMarketBarsQuery(
    asset.symbol,
    { market: asset.market, interval, from: loadedFrom, to: todayLocalDate() },
    available,
  );

  // 数据到头检测（§3.4：相邻响应对比，免容差常量、长假免疫；仅扩展响应参与判定）：
  // 比较两次真实响应的首根 bar 日期——新响应首根未前移（≥ 上一次）即库内无更早数据，
  // 置 exhausted 停止扩展。依赖只挂 data + isPlaceholderData（不挂 loadedFrom——扩展期间
  // data 是 placeholder 旧数据，挂 loadedFrom 会用旧 bars[0] 比新 loadedFrom 误判到头）；
  // 仅 data.from 变化（扩展响应必有新 from）才参与判定——查询 key 含 to=today，页面停留
  // 跨日 rollover 会重发同 from 请求，若参与判定会因首行未变误判到头。
  useEffect(() => {
    const data = barsQuery.data;
    if (!data || barsQuery.isPlaceholderData) return;
    if (data.from === lastJudgedFromRef.current) return;
    lastJudgedFromRef.current = data.from;
    const first = data.bars[0]?.timestamp.slice(0, 10) ?? null;
    if (first === null) return;
    if (prevFirstBarRef.current !== null && first >= prevFirstBarRef.current) setExhausted(true);
    prevFirstBarRef.current = first;
  }, [barsQuery.data, barsQuery.isPlaceholderData]);

  function handleDataZoom(range: { start: string; end: string }) {
    const key = `${range.start}|${range.end}`;
    if (key === lastReportedRef.current) return; // 逐像素事件去重
    lastReportedRef.current = key;
    setVisible(range);
    if (exhausted || barsQuery.isFetching) return;
    const visibleDays = daysBetween(range.start, range.end); // 自然日差
    const loadedDays = daysBetween(loadedFrom, todayLocalDate());
    const leftBuffer = daysBetween(loadedFrom, range.start); // 左缘距加载左界
    // 路径① 滚轮缩小破坏缓冲不变量 loaded ≥ 2×visible → 扩展至 2×visible（≥ 初始 180 天）
    if (visibleDays * BUFFER_RATIO > loadedDays * HYSTERESIS) {
      setLoadedFrom(daysAgoLocalDate(Math.max(INITIAL_LOADED_DAYS, visibleDays * BUFFER_RATIO)));
      return;
    }
    // 路径② 向左平移逼近加载左界（< 半屏）→ 预拉一屏
    if (leftBuffer < visibleDays * PAN_EDGE_RATIO) {
      setLoadedFrom(daysAgoLocalDate(Math.max(INITIAL_LOADED_DAYS, loadedDays + visibleDays)));
    }
  }

  if (!available) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{asset.name}</CardTitle>
          <Badge variant="secondary">暂不可用</Badge>
        </CardHeader>
        <CardContent>
          <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            该资产尚未通过实测验收，暂不可用
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
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          周期 {interval}
        </span>

        {barsQuery.isPending && <LoadingState label="K 线加载中…" />}

        {error && (
          <ErrorState
            error={error}
            onRetry={error.retryable ? () => void barsQuery.refetch() : undefined}
          />
        )}

        {barsQuery.data && viewModel && (
          <>
            <CandlestickChart
              model={viewModel}
              height={460}
              visibleRange={visible}
              onDataZoom={handleDataZoom}
              drawings={drawings}
              onDrawingsChange={setDrawings}
            />
            {barsQuery.isFetching && !barsQuery.isPending && (
              <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                加载更多历史…
              </p>
            )}
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
