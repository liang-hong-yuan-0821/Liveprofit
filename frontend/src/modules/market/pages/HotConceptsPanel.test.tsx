import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import { HotConceptsPanel } from './HotConceptsPanel';

vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: { hotConceptsApiV1MarketDataConceptsHotGet: vi.fn() },
}));
vi.mock('../../../shared/charts/CandlestickChart', () => ({
  CandlestickChart: () => <div data-testid="concept-chart" />,
}));

import { MarketDataService } from '../../../api/generated/services/MarketDataService';

const hotMock = MarketDataService.hotConceptsApiV1MarketDataConceptsHotGet as Mock;

function envelope(data: unknown, nextCursor: string | null = null) {
  return { data, meta: { request_id: 'r', schema_version: 'v1', next_cursor: nextCursor } };
}

function makeConcept(code: string, name: string, rank: number) {
  return {
    concept_code: code,
    concept_name: name,
    rank,
    hotness_reason: '区间涨幅居前',
    period_return: 6.32,
    daily_changes: [{ date: '2026-09-03', change_pct: 1.2 }],
    updated_at: '2026-09-04T18:00:00Z',
    bars: [{ timestamp: '2026-09-03T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
  };
}

function snapshot(items: unknown[]) {
  return {
    as_of: '2026-09-04',
    algorithm_version: 'heat_v1',
    result_status: 'OK',
    items,
    source: 'akshare',
    source_updated_at: '2026-09-04T18:00:00Z',
    freshness_status: 'FRESH',
  };
}

beforeEach(() => {
  hotMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('HotConceptsPanel', () => {
  it('NO_HOT_CONCEPTS / 空列表是正常空态', async () => {
    hotMock.mockResolvedValue(envelope({ ...snapshot([]), result_status: 'NO_HOT_CONCEPTS' }));
    renderWithRouter(<HotConceptsPanel />);

    expect(await screen.findByText('当前条件下暂无热点概念')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });

  it('卡片展示服务端排名/原因/涨跌幅与 K 线；可展开近 10 日涨跌幅', async () => {
    const user = userEvent.setup();
    hotMock.mockResolvedValue(envelope(snapshot([makeConcept('BK1', '半导体', 1)])));
    renderWithRouter(<HotConceptsPanel />);

    expect(await screen.findByText(/半导体/)).toBeInTheDocument();
    expect(screen.getByText('区间涨幅居前')).toBeInTheDocument();
    expect(screen.getByText('+6.32%')).toBeInTheDocument();
    expect(screen.getAllByTestId('concept-chart').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: '展开近 10 日涨跌幅' }));
    expect(screen.getByText(/2026-09-03：\+1.2%/)).toBeInTheDocument();
  });

  it('STALE 快照展示旧快照并标注延迟', async () => {
    hotMock.mockResolvedValue(envelope({ ...snapshot([makeConcept('BK1', '半导体', 1)]), freshness_status: 'STALE' }));
    renderWithRouter(<HotConceptsPanel />);

    expect(await screen.findByText('快照可能延迟')).toBeInTheDocument();
    expect(screen.getByText(/半导体/)).toBeInTheDocument();
  });

  it('首期无分页：一次全量返回（top_n≤30），无加载更多入口', async () => {
    hotMock.mockResolvedValue(envelope(snapshot([makeConcept('BK1', '半导体', 1), makeConcept('BK2', '人工智能', 2)])));
    renderWithRouter(<HotConceptsPanel />);

    expect(await screen.findByText(/半导体/)).toBeInTheDocument();
    expect(screen.getByText(/人工智能/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
    expect(hotMock).toHaveBeenCalledWith('CN', '1d', expect.any(String), expect.any(String), 30);
  });
});
