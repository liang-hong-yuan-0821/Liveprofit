import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import { HotConceptsPanel } from './HotConceptsPanel';

// 关闭即卸载的**可证伪**断言（CR 二轮 minor 2）：mock KLineDialog 恒渲染
// testid（不随 open=false 卸载——Radix 真组件在 open=false 时本就卸载，无法证伪），
// 因此 testid 消失只可能来自父组件 selectedNode=null 的卸载分支；
// 若回退 setSelectedNode(null)，该用例必红。

vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: {
    conceptTreeApiV1MarketDataConceptsTreeGet: vi.fn(),
  },
}));
vi.mock('../components/KLineDialog', () => ({
  KLineDialog: ({ onOpenChange }: { node: unknown; open: boolean; onOpenChange: (open: boolean) => void }) => (
    <div data-testid="kline-dialog">
      <button onClick={() => onOpenChange(false)}>close-dialog</button>
    </div>
  ),
}));
vi.mock('../components/ConceptTreemap', () => ({
  ConceptTreemap: ({ onNodeClick }: {
    data: unknown[]; onNodeClick: (n: { kind: string; code: string; name: string }) => void;
  }) => (
    <div data-testid="concept-treemap">
      <button
        onClick={() => onNodeClick({ kind: 'concept', code: 'BK1753', name: '光刻胶' })}
      >
        click-concept
      </button>
    </div>
  ),
}));

import { MarketDataService } from '../../../api/generated/services/MarketDataService';

const treeMock = MarketDataService.conceptTreeApiV1MarketDataConceptsTreeGet as Mock;

function treeSnapshot(items: unknown[]) {
  return {
    as_of: '2026-09-04', algorithm_version: 'heat_v1', result_status: 'OK',
    items, source: 'dc', source_updated_at: null, freshness_status: 'FRESH',
  };
}

beforeEach(() => {
  treeMock.mockReset();
  treeMock.mockResolvedValue({
    data: treeSnapshot([{
      sector_code: 'BK1753', sector_name: '光刻胶', rank: 1, heat_score: 5.77,
      pct_chg: 1.23, member_total: 0, members: [],
    }]),
    meta: { request_id: 'r', schema_version: 'v1' },
  });
});

describe('HotConceptsPanel 关闭即卸载', () => {
  it('点击节点挂载弹窗；关闭后 selectedNode 清空 → 弹窗组件卸载', async () => {
    renderWithRouter(<HotConceptsPanel />);
    await screen.findByTestId('concept-treemap');
    expect(screen.queryByTestId('kline-dialog')).not.toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', { name: 'click-concept' }));
    await waitFor(() => expect(screen.getByTestId('kline-dialog')).toBeInTheDocument());

    await userEvent.setup().click(screen.getByRole('button', { name: 'close-dialog' }));
    await waitFor(() =>
      expect(screen.queryByTestId('kline-dialog')).not.toBeInTheDocument());
  });
});
