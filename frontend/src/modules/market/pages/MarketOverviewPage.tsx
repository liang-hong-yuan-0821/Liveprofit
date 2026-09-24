import { Resource } from '../../../api/generated';
import { MarketRefreshStatus } from '../components/MarketRefreshStatus';
import { MarketDatesContext, useMarketRefresh } from './refreshQueries';
import { MarketIndicesPanel } from './MarketIndicesPanel';
import { TrendComparisonPanel } from './TrendComparisonPanel';
import { HotConceptsPanel } from './HotConceptsPanel';
import { MacroInformationPanel } from './MacroInformationPanel';

// 大盘：同一连续滚动页按 市场（宏观指数）→ 趋势对比（市值分层/市场板归一曲线）
// → 板块（热门概念）→ 信息（宏观信息）固定顺序组合四个独立 Panel；
// 无页内 Tab、无本地指数名单、无页面间数据拼接；四个 Panel 是独立 Query 边界，
// 一个失败不影响另外三个。
export default function MarketOverviewPage() {
  const refresh = useMarketRefresh();
  return (
    <MarketDatesContext.Provider value={refresh.dates}>
    <div className="flex flex-col gap-7">
      <header className="relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] px-6 py-7">
        <div className="pointer-events-none absolute -right-12 -top-24 size-72 rounded-full bg-[var(--color-accent)] opacity-10 blur-3xl" aria-hidden="true" />
        <p className="mb-2 text-[10px] font-semibold tracking-[.24em] text-[var(--color-accent-bright)]">MARKET INTELLIGENCE</p>
        <h1 className="text-3xl font-semibold">市场全景</h1>
        <p className="mt-2 text-sm text-[var(--color-fg-muted)]">从全球指数到市场脉络，让每一个判断有据可循。</p>
        <nav aria-label="大盘页内导航" className="mt-6 flex flex-wrap gap-2">
          {[['indices', '全球指数'], ['trends', '趋势对比'], ['concepts', '热门概念'], ['information', '宏观信息']].map(([id, label]) => <a key={id} href={`#${id}`} className="rounded-full border border-[var(--color-border)] px-4 py-1.5 text-xs transition-colors hover:border-[var(--color-accent)] hover:text-[var(--color-accent-bright)]">{label} ↗</a>)}
        </nav>
      </header>

      <section id="indices" aria-label="市场区块（宏观指数）">
        <h2 className="mb-3 text-base font-semibold">市场</h2>
        <div className="mb-3"><MarketRefreshStatus refresh={refresh} resources={[Resource.CN_INDEX_BARS, Resource.CN_INDEX_FACTORS, Resource.US_INDEX_BARS, Resource.KR_INDEX_BARS]} /></div>
        <MarketIndicesPanel />
      </section>

      <section id="trends" aria-label="趋势对比区块">
        <h2 className="mb-3 text-base font-semibold">趋势对比</h2>
        <TrendComparisonPanel />
      </section>

      <section id="concepts" aria-label="板块区块（热门概念）">
        <h2 className="mb-3 text-base font-semibold">板块</h2>
        <div className="mb-3"><MarketRefreshStatus refresh={refresh} resources={[Resource.CN_SECTOR_DAILY]} /></div>
        <HotConceptsPanel />
      </section>

      <section id="information" aria-label="信息区块（宏观信息）">
        <h2 className="mb-3 text-base font-semibold">信息</h2>
        <MacroInformationPanel />
      </section>
    </div>
    </MarketDatesContext.Provider>
  );
}
