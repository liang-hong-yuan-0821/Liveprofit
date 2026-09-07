import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../../shared/ui/card';
import { WatchlistGroupList } from '../watchlists/WatchlistGroupList';
import { WatchlistItemList } from '../watchlists/WatchlistItemList';
import { PortfolioList } from '../portfolios/PortfolioList';
import { PositionList } from '../portfolios/PositionList';

// 自选 /watchlist：同页两个独立业务区——自选分组与标的、手工组合与持仓。
// 二者选择、弹窗和编辑草稿均为页面局部状态；两套资源各自 Query/Mutation 与错误恢复，
// 不共享 revision；AI 报告 final_position_plan 不得自动写入、合并或覆盖手工持仓。
export default function WatchlistPage() {
  const [selectedWatchlist, setSelectedWatchlist] = useState<string | null>(null);
  const [selectedPortfolio, setSelectedPortfolio] = useState<string | null>(null);

  return (
    <main className="flex flex-col gap-4">
      <header>
        <h1 className="text-lg font-semibold">自选</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          关注清单与手工持仓分两区维护；与 AI 仓位建议完全隔离
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>自选分组与标的</CardTitle>
          </CardHeader>
          <CardContent>
            <WatchlistGroupList selectedId={selectedWatchlist} onSelect={setSelectedWatchlist} />
            <div className="mt-3 border-t pt-3" style={{ borderColor: 'var(--color-border)' }}>
              <WatchlistItemList watchlistId={selectedWatchlist} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>手工组合与持仓</CardTitle>
          </CardHeader>
          <CardContent>
            <PortfolioList selectedId={selectedPortfolio} onSelect={setSelectedPortfolio} />
            <div className="mt-3 border-t pt-3" style={{ borderColor: 'var(--color-border)' }}>
              <PositionList portfolioId={selectedPortfolio} />
            </div>
            <p className="mt-3 rounded-md border p-2 text-xs" style={{ borderColor: 'var(--color-border)', color: 'var(--color-fg-muted)' }}>
              AI 建议说明：首期不会将分析报告中的建议仓位（final_position_plan）自动写入、合并或覆盖手工持仓。
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
