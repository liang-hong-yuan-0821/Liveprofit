// test-catalog-begin
// {
//   "purpose": "行情界面 / HotConceptsPanel：默认不指定日期；历史选择不会被后台刷新覆盖；StrictMode 下用户打开个股仅 ensure 一次，100 次自动刷新均 cache_only，关闭后无活跃查询；NO_HOT_CONCEPTS / 空列表是正常空态",
//   "keywords": [
//     "行情界面",
//     "市场分析",
//     "hot_concepts_panel"
//   ],
//   "covers": [
//     "frontend/src/api/generated/services/MarketDataService.ts",
//     "frontend/src/api/queryKeys.ts",
//     "frontend/src/modules/market/components/ConceptTreemap.tsx",
//     "frontend/src/modules/market/pages/HotConceptsPanel.tsx",
//     "frontend/src/shared/charts/CandlestickChart.tsx"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { queryKeys } from '../../../../../frontend/src/api/queryKeys';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../../support/frontend/utils';
import { HotConceptsPanel } from '../../../../../frontend/src/modules/market/pages/HotConceptsPanel';

// 板块区块（板块概念Treemap方案 3.4）：treemap 两层 + 点击弹窗 K 线。
// mock 策略：ConceptTreemap mock 捕获 data 与 onNodeClick（暴露两个按钮模拟
// 概念/个股点击注入——先例 GraphTopologyPanel.test 的 onEvents 注入模式）；
// KLineDialog 的查询经真实 hook 走 mock 的 Service 方法（断言参数与 enabled 门控）。

vi.mock('../../../../../frontend/src/api/generated/services/MarketDataService', () => ({
  MarketDataService: {
    conceptTreeApiV1MarketDataConceptsTreeGet: vi.fn(),
    sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet: vi.fn(),
    stockBarsApiV1MarketDataStocksSymbolBarsGet: vi.fn(),
  },
}));
// 捕获 model prop（m7 断言：indicators 透传给图表，与指数 K 线同款渲染）
const chartCalls = vi.hoisted(() => ({ props: [] as Array<{ model: Record<string, unknown> }> }));
vi.mock('../../../../../frontend/src/shared/charts/CandlestickChart', () => ({
  CandlestickChart: (props: { model: Record<string, unknown> }) => {
    chartCalls.props.push(props);
    return <div data-testid="kline-chart" />;
  },
}));
vi.mock('../../../../../frontend/src/modules/market/components/ConceptTreemap', () => ({
  ConceptTreemap: ({ data, onNodeClick }: {
    data: unknown[]; onNodeClick: (n: { kind: string; code: string; name: string }) => void;
  }) => (
    <div data-testid="concept-treemap" data-items={data.length}>
      <button
        onClick={() => onNodeClick({ kind: 'concept', code: 'BK1753', name: '光刻胶' })}
      >
        click-concept
      </button>
      <button
        onClick={() => onNodeClick({ kind: 'stock', code: '600050.SH', name: '中国联通' })}
      >
        click-stock
      </button>
    </div>
  ),
}));

import { MarketDataService } from '../../../../../frontend/src/api/generated/services/MarketDataService';

const treeMock = MarketDataService.conceptTreeApiV1MarketDataConceptsTreeGet as Mock;
const conceptBarsMock = MarketDataService.sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet as Mock;
const stockBarsMock = MarketDataService.stockBarsApiV1MarketDataStocksSymbolBarsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function makeConcept(code: string, name: string, rank: number) {
  return {
    sector_code: code,
    sector_name: name,
    rank,
    heat_score: 5.77,
    pct_chg: 1.23,
    member_total: 2,
    members: [
      { ts_code: '600050.SH', name: '中国联通', pct_chg: 3.21 },
      { ts_code: '600051.SH', name: '停牌股', pct_chg: null },
    ],
  };
}

function treeSnapshot(items: unknown[], freshness: string = 'FRESH') {
  return {
    as_of: '2026-09-04',
    algorithm_version: 'heat_v1',
    result_status: 'OK',
    items,
    source: 'dc',
    source_updated_at: '2026-09-04T18:00:00Z',
    freshness_status: freshness,
  };
}

const barsData = {
  asset: { market: 'CN', symbol: 'X', name: 'X' }, interval: '1d',
  from: '2026-03-18', to: '2026-09-11',
  bars: [{ timestamp: '2026-09-03T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
  indicators: null, source: 'dc', as_of: '2026-09-04', source_updated_at: null,
  freshness_status: 'FRESH', market_session_status: 'CLOSED', market_closed_reason: '已收盘',
};

beforeEach(() => {
  treeMock.mockReset();
  conceptBarsMock.mockReset();
  stockBarsMock.mockReset();
  chartCalls.props = [];
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('HotConceptsPanel', () => {
  it('默认不指定日期；历史选择不会被后台刷新覆盖', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([])));
    const { queryClient } = renderWithRouter(<HotConceptsPanel />);
    await screen.findByText('当前条件下暂无热门概念');
    expect(treeMock).toHaveBeenLastCalledWith('CN', '1d', 30, undefined);
    fireEvent.change(screen.getByLabelText('榜单日期'), { target: { value: '2026-08-31' } });
    await waitFor(() => expect(treeMock).toHaveBeenLastCalledWith('CN', '1d', 30, '2026-08-31'));
    await act(async () => { await queryClient.invalidateQueries({ queryKey: queryKeys.conceptTree.all }); });
    expect(screen.getByLabelText('榜单日期')).toHaveValue('2026-08-31');
    expect(treeMock).toHaveBeenLastCalledWith('CN', '1d', 30, '2026-08-31');
  });

  it('StrictMode 下用户打开个股仅 ensure 一次，100 次自动刷新均 cache_only，关闭后无活跃查询', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([makeConcept('BK1753', '光刻胶', 1)])));
    stockBarsMock.mockResolvedValue(envelope(barsData));
    const { queryClient } = renderWithRouter(<StrictMode><HotConceptsPanel /></StrictMode>);
    await screen.findByTestId('concept-treemap');
    await userEvent.setup().click(screen.getByRole('button', { name: 'click-stock' }));
    await screen.findByTestId('kline-chart');
    const before = stockBarsMock.mock.calls.length;
    await act(async () => {
      for (let index = 0; index < 100; index++) await queryClient.refetchQueries({ queryKey: queryKeys.stockBars.all, type: 'active' });
    });
    expect(stockBarsMock.mock.calls.length - before).toBe(100);
    expect(stockBarsMock.mock.calls.filter(call => call[5] === 'ensure')).toHaveLength(1);
    expect(stockBarsMock.mock.calls.slice(before).every(call => call[5] === 'cache_only')).toBe(true);
    await userEvent.setup().click(screen.getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByTestId('kline-chart')).not.toBeInTheDocument());
    const closed = stockBarsMock.mock.calls.length;
    await act(async () => { await queryClient.refetchQueries({ queryKey: queryKeys.stockBars.all, type: 'active' }); });
    expect(stockBarsMock).toHaveBeenCalledTimes(closed);
  });

  it('NO_HOT_CONCEPTS / 空列表是正常空态', async () => {
    treeMock.mockResolvedValue(envelope({
      ...treeSnapshot([]), result_status: 'NO_HOT_CONCEPTS',
    }));
    renderWithRouter(<HotConceptsPanel />);

    await waitFor(() => expect(screen.getByText('当前条件下暂无热门概念')).toBeInTheDocument(), { timeout: 3000 });
  });

  it('treemap 渲染榜单数据；STALE 显示数据延迟徽标', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([makeConcept('BK1753', '光刻胶', 1)], 'STALE')));
    renderWithRouter(<HotConceptsPanel />);

    await waitFor(() =>
      expect(screen.getByTestId('concept-treemap')).toHaveAttribute('data-items', '1'));
    expect(screen.getByText('数据可能延迟')).toBeInTheDocument();
    expect(screen.getByText('榜单 2026-09-04 · heat_v1')).toBeInTheDocument();
  });

  it('点击概念节点 → 弹窗 K 线：sectorBars 参数正确、关闭后卸载不再请求', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([makeConcept('BK1753', '光刻胶', 1)])));
    conceptBarsMock.mockResolvedValue(envelope(barsData));
    renderWithRouter(<HotConceptsPanel />);

    await screen.findByTestId('concept-treemap');
    // 弹窗未挂载（selectedNode=null 条件渲染）→ 不存在 hook 实例；
    // 真正的 enabled 门控断言在 queries.test.ts（enabled=false 不发起请求）

    await userEvent.setup().click(screen.getByRole('button', { name: 'click-concept' }));
    await waitFor(() => expect(screen.getByTestId('kline-chart')).toBeInTheDocument());
    // 概念 K 线：sector_code + source=dc + 180 天窗口（daysAgoLocalDate(180) → today）
    expect(conceptBarsMock).toHaveBeenCalledTimes(1);
    const [code, market, interval, from, to, source] = conceptBarsMock.mock.calls[0];
    expect([code, market, interval, source]).toEqual(['BK1753', 'CN', '1d', 'dc']);
    expect(from < to).toBe(true);
    expect(screen.getByText('光刻胶')).toBeInTheDocument();  // 弹窗标题
    expect(stockBarsMock).not.toHaveBeenCalled();

    // 关闭弹窗 → selectedNode 清空（卸载查询），不产生新请求
    await userEvent.setup().click(screen.getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByTestId('kline-chart')).not.toBeInTheDocument());
    expect(conceptBarsMock).toHaveBeenCalledTimes(1);
  });

  it('点击个股节点 → 个股 K 线：stockBars 参数正确', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([makeConcept('BK1753', '光刻胶', 1)])));
    stockBarsMock.mockResolvedValue(envelope(barsData));
    renderWithRouter(<HotConceptsPanel />);

    await screen.findByTestId('concept-treemap');
    await userEvent.setup().click(screen.getByRole('button', { name: 'click-stock' }));
    await waitFor(() => expect(screen.getByTestId('kline-chart')).toBeInTheDocument());
    expect(stockBarsMock.mock.calls.filter(call => call[5] === 'ensure')).toHaveLength(1);
    expect(stockBarsMock.mock.calls.filter(call => call[5] === 'cache_only')).toHaveLength(1);
    const [symbol, market, interval] = stockBarsMock.mock.calls[0];
    expect([symbol, market, interval]).toEqual(['600050.SH', 'CN', '1d']);
    expect(conceptBarsMock).not.toHaveBeenCalled();
  });

  it('弹窗 K 线透传指标（m7：MA/BOLL/MACD 进 mapper → 与指数 K 线同款渲染）', async () => {
    treeMock.mockResolvedValue(envelope(treeSnapshot([makeConcept('BK1753', '光刻胶', 1)])));
    conceptBarsMock.mockResolvedValue(envelope({
      ...barsData,
      indicators: {
        ma: [
          { period: 5, values: [1.1] },
          { period: 10, values: [1.2] },
          { period: 20, values: [1.3] },
          { period: 60, values: [null] },
        ],
        boll: { period: 20, k: 2.0, mid: [1.3], upper: [1.4], lower: [1.2] },
        macd: { fast: 12, slow: 26, signal: 9, dif: [0.5], dea: [0.4], hist: [0.2] },
      },
    }));
    renderWithRouter(<HotConceptsPanel />);

    await screen.findByTestId('concept-treemap');
    await userEvent.setup().click(screen.getByRole('button', { name: 'click-concept' }));
    await waitFor(() => expect(screen.getByTestId('kline-chart')).toBeInTheDocument());

    expect(chartCalls.props).toHaveLength(1);
    const model = chartCalls.props[0].model;
    expect(model.ma).toEqual([
      { period: 5, values: [1.1] },
      { period: 10, values: [1.2] },
      { period: 20, values: [1.3] },
      { period: 60, values: [null] },
    ]);
    expect(model.boll).toEqual({ period: 20, k: 2.0, mid: [1.3], upper: [1.4], lower: [1.2] });
    expect(model.macd).toEqual({ dif: [0.5], dea: [0.4], hist: [0.2] });
  });
});
