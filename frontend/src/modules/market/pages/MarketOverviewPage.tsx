import { MarketIndicesPanel } from './MarketIndicesPanel';
import { HotConceptsPanel } from './HotConceptsPanel';
import { MacroInformationPanel } from './MacroInformationPanel';

// 大盘：同一连续滚动页按 市场（宏观指数）→ 板块（热门概念）→ 信息（宏观信息）
// 固定顺序组合三个独立 Panel；无页内 Tab、无本地指数名单、无页面间数据拼接；
// 三个 Panel 是独立 Query 边界，一个失败不影响另外两个。
export default function MarketOverviewPage() {
  return (
    <main className="flex flex-col gap-8">
      <header>
        <h1 className="text-lg font-semibold">大盘</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          全球指数 · 热门概念 · 已审核宏观信息
        </p>
      </header>

      <section aria-label="市场区块（宏观指数）">
        <h2 className="mb-3 text-base font-semibold">市场</h2>
        <MarketIndicesPanel />
      </section>

      <section aria-label="板块区块（热门概念）">
        <h2 className="mb-3 text-base font-semibold">板块</h2>
        <HotConceptsPanel />
      </section>

      <section aria-label="信息区块（宏观信息）">
        <h2 className="mb-3 text-base font-semibold">信息</h2>
        <MacroInformationPanel />
      </section>
    </main>
  );
}
