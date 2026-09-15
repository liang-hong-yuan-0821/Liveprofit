import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import { daysAgoLocalDate, todayLocalDate } from '../../../shared/format/dateTime';
import type { Drawing } from '../../../shared/charts/drawings';
import { MarketIndicesPanel } from './MarketIndicesPanel';

// 目录端点已删：MarketAssetsService mock 与 assetsMock 全部删除（目录写死
// MARKET_INDEX_CATALOG）；MarketDataService mock 保留；CandlestickChart mock
// 捕获 props（供按需加载状态机用例手动触发 onDataZoom）。
vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: { indexBarsApiV1MarketDataIndicesSymbolBarsGet: vi.fn() },
}));

// 图表 props 按 model 首根日期归档：.INX 的动态 fixture 首行日期（daysAgoLocalDate(178/198/298)）
// 与其余资产的固定帧首行 2026-09-03 不同，据此可定位 .INX 图表；另存全量列表
// （同帧资产的日期 key 会互相覆盖，持久化接线用例按 drawings 内容从列表定位）
const chartPropsByFirstDate: Record<string, Record<string, unknown>> = {};
const chartPropsList: Array<Record<string, unknown>> = [];
vi.mock('../../../shared/charts/CandlestickChart', () => ({
  CandlestickChart: (props: Record<string, unknown>) => {
    const xAxisData = (props.model as { xAxisData: string[] }).xAxisData;
    chartPropsByFirstDate[xAxisData[0] ?? ''] = props;
    chartPropsList.push(props);
    return <div data-testid="candlestick-chart" />;
  },
}));

import { MarketDataService } from '../../../api/generated/services/MarketDataService';

const barsMock = MarketDataService.indexBarsApiV1MarketDataIndicesSymbolBarsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

const barsData = {
  asset: {}, interval: '1d', from: '2026-07-01', to: '2026-09-04',
  bars: [{ timestamp: '2026-09-03T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
  indicators: {
    ma: [
      { period: 5, values: [null] },
      { period: 10, values: [null] },
      { period: 20, values: [null] },
      { period: 60, values: [null] },
    ],
    boll: { period: 20, k: 2, mid: [null], upper: [null], lower: [null] },
  },
  source: 'tushare', as_of: '2026-09-04', source_updated_at: null,
  freshness_status: 'FRESH', market_session_status: 'CLOSED', market_closed_reason: '已收盘',
};

beforeEach(() => {
  barsMock.mockReset();
  barsMock.mockResolvedValue(envelope(barsData));
  for (const key of Object.keys(chartPropsByFirstDate)) delete chartPropsByFirstDate[key];
  chartPropsList.length = 0;
  localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

// .INX 动态帧：首根 = firstBarDaysAgo 天前、from 回显请求参数（按需加载状态机用例）
function inxFrame(from: string, firstBarDaysAgo: number) {
  return {
    ...barsData,
    from,
    to: todayLocalDate(),
    bars: [{
      timestamp: `${daysAgoLocalDate(firstBarDaysAgo)}T00:00:00Z`,
      open: 1, high: 2, low: 0.5, close: 1.5, volume: null,
    }],
  };
}

// 其余资产固定帧、.INX 按调用序返回首行序列（超出取末值：末次未前移 → 到头）
function mockInxResponses(firstDaysAgoByCall: number[]) {
  let inxCalls = 0;
  barsMock.mockImplementation((symbol: string, _market: string, _interval: string, from: string) => {
    if (symbol !== '.INX') return Promise.resolve(envelope(barsData));
    inxCalls += 1;
    const daysAgo = firstDaysAgoByCall[Math.min(inxCalls - 1, firstDaysAgoByCall.length - 1)];
    return Promise.resolve(envelope(inxFrame(from, daysAgo)));
  });
}

function inxRequests() {
  return barsMock.mock.calls.filter((call) => call[0] === '.INX');
}

function inxChartProps(): Record<string, unknown> {
  const firstDates = Object.keys(chartPropsByFirstDate).filter((d) => d && d !== '2026-09-03');
  return chartPropsByFirstDate[firstDates.at(-1)!];
}

// 等待 .INX 指定首行日期的响应帧落定（请求在途期间 isFetching 门控会丢弃新缩放事件，
// 连续触发前必须先等上一次响应渲染——帧首行日期为 daysAgo 天前的图表 props 出现）
async function inxResponseLanded(firstDaysAgo: number) {
  await waitFor(() => expect(chartPropsByFirstDate[daysAgoLocalDate(firstDaysAgo)]).toBeDefined());
}

describe('MarketIndicesPanel', () => {
  it('目录写死：11 指数 + US→KR→CN 组序 + 组内顺序固化（polish g 回归落点）', async () => {
    renderWithRouter(<MarketIndicesPanel />);

    // 11 指数全部渲染（US 3 + KS11 + CN 7 请求 bars 渲染图表；KOSDAQ 已移除）
    for (const name of ['标普500', '道琼斯工业指数', '纳斯达克综合指数',
                        '韩国综合指数',
                        '上证综指', '深证成指', '创业板指', '科创50', '上证50', '中证1000', '上证红利']) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
    // 组序：页面区块按 US → KR → CN 排列（取全部区块标题顺序）
    const headings = screen.getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent ?? '');
    expect(headings).toEqual(['美国（US）', '韩国（KR）', '中国（CN）']);
  });

  it('AVAILABLE 资产发 11 个请求、KOSDAQ 已移除无"暂不可用"卡片', async () => {
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    // US 3 + KS11 + CN 7 全部 AVAILABLE 发请求（渲染序 US → KR → CN）
    const requestedSymbols = barsMock.mock.calls.map((call) => call[0]);
    expect(requestedSymbols).toEqual([
      '.INX', '.DJI', '.IXIC', 'KS11',
      '000001.SH', '399001.SZ', '399006.SZ', '000688.SH', '000016.SH', '000852.SH', '000015.SH',
    ]);
    // KOSDAQ 条目已从目录移除：无任何"暂不可用"卡片
    expect(screen.queryByText('该资产尚未通过实测验收，暂不可用')).not.toBeInTheDocument();
  });

  it('无 bars 不绘制空壳图，显示无可展示时序', async () => {
    barsMock.mockResolvedValue(envelope({ ...barsData, bars: [] }));
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByText('无可展示时序')).toBeInTheDocument();
    expect(screen.queryByTestId('candlestick-chart')).not.toBeInTheDocument();
  });

  it('STALE 显示延迟标识；闭市显示闭市原因', async () => {
    barsMock.mockResolvedValue(envelope({ ...barsData, freshness_status: 'STALE' }));
    renderWithRouter(<MarketIndicesPanel />);

    // 11 个 AVAILABLE 卡片同 mock 帧（US 3 + KS11 + CN 7；KOSDAQ 已移除不发请求）
    await waitFor(() => expect(screen.getAllByText('数据可能延迟')).toHaveLength(11));
    expect(screen.getAllByText(/闭市：已收盘/)).toHaveLength(11);
  });

  it('旧后端无 indicators 字段：降级渲染纯 K 线', async () => {
    const { indicators: _dropped, ...barsWithoutIndicators } = barsData;
    barsMock.mockResolvedValue(envelope(barsWithoutIndicators));
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByTestId('candlestick-chart')).toBeInTheDocument();
  });

  it('按需加载初载：请求 from = 180 天前、图表收到 90 天可见窗口、首行 178 天前不误判到头', async () => {
    mockInxResponses([178]);
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    // 请求参数：from = daysAgoLocalDate(180)、to = today（周期 1d）
    const requests = inxRequests();
    expect(requests).toHaveLength(1);
    expect(requests[0].slice(1)).toEqual(['US', '1d', daysAgoLocalDate(180), todayLocalDate()]);
    // 图表收到 90 天可见窗口锚定 + onDataZoom 出口
    const props = inxChartProps();
    expect(props.visibleRange).toEqual({ start: daysAgoLocalDate(90), end: todayLocalDate() });
    expect(props.onDataZoom).toBeTypeOf('function');
  });

  it('缩小到 100 天可见：扩展请求 from = 200 天前；响应首行前移（198 天前）未到头', async () => {
    mockInxResponses([178, 198]);
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    act(() => {
      (inxChartProps().onDataZoom as (r: { start: string; end: string }) => void)({
        start: daysAgoLocalDate(100),
        end: todayLocalDate(),
      });
    });
    await waitFor(() => expect(inxRequests().length).toBe(2));
    // 100×2=200 > 180×1.1=198 → 路径①扩展至 200 天
    expect(inxRequests()[1][3]).toBe(daysAgoLocalDate(200));
  });

  it('左平移逼近左界（leftBuffer 22d < 半屏）：预拉一屏，请求 from = 300 天前；首行 298 天前未到头', async () => {
    mockInxResponses([178, 198, 298]);
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    act(() => {
      (inxChartProps().onDataZoom as (r: { start: string; end: string }) => void)({
        start: daysAgoLocalDate(100),
        end: todayLocalDate(),
      });
    });
    await waitFor(() => expect(inxRequests().length).toBe(2));
    await inxResponseLanded(198); // 响应落定（isFetching 门控解除）

    // 跨度保持 100 天：路径①不命中（200 ≤ 200×1.1=220），leftBuffer=22d < 50d 走路径②
    act(() => {
      (inxChartProps().onDataZoom as (r: { start: string; end: string }) => void)({
        start: daysAgoLocalDate(178),
        end: daysAgoLocalDate(78),
      });
    });
    await waitFor(() => expect(inxRequests().length).toBe(3));
    expect(inxRequests()[2][3]).toBe(daysAgoLocalDate(300));
  });

  it('继续左平移 → 请求 400 天；响应首行未前移 → 到头，后续 onDataZoom 不再产生新请求', async () => {
    mockInxResponses([178, 198, 298, 298]);
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    const zoom = (range: { start: string; end: string }) => {
      act(() => {
        (inxChartProps().onDataZoom as (r: { start: string; end: string }) => void)(range);
      });
    };
    zoom({ start: daysAgoLocalDate(100), end: todayLocalDate() });
    await waitFor(() => expect(inxRequests().length).toBe(2));
    await inxResponseLanded(198); // 响应落定（isFetching 门控解除）
    zoom({ start: daysAgoLocalDate(178), end: daysAgoLocalDate(78) });
    await waitFor(() => expect(inxRequests().length).toBe(3));
    await inxResponseLanded(298);

    // leftBuffer=2d < 50d → 路径② → 请求 400 天；第四次响应首行仍 298 天前（未前移）→ 到头
    zoom({ start: daysAgoLocalDate(298), end: daysAgoLocalDate(198) });
    await waitFor(() => expect(inxRequests().length).toBe(4));
    expect(inxRequests()[3][3]).toBe(daysAgoLocalDate(400));
    // 第四次响应与第三次同首行日期（298）：等响应 + exhausted 判定 effect 落定（定时推进）
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    // 触发一个本应扩展的平移（leftBuffer=5d < 半屏）：到头后不再发请求
    zoom({ start: daysAgoLocalDate(395), end: daysAgoLocalDate(195) });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(inxRequests().length).toBe(4);
  });

  it('画线持久化接线：初载从 localStorage 按标的 key 读取、onDrawingsChange 后保存、切标的不串', async () => {
    const seeded: Drawing[] = [{ id: 's1', kind: 'hline', p1: { date: '2026-09-01', price: 3000 } }];
    localStorage.setItem('liveprofit.market.drawings.v1..INX', JSON.stringify(seeded));
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findAllByTestId('candlestick-chart');

    // 仅 .INX 的 key 有种子 → 其图表收到 drawings；其余资产为空（同帧日期 key 会互相覆盖，按列表定位）
    const inxProps = chartPropsList.find(
      (p) => Array.isArray(p.drawings) && p.drawings.length > 0,
    )!;
    expect(inxProps.drawings).toEqual(seeded);

    // onDrawingsChange 触发后保存回 localStorage（同 key）
    act(() => {
      (inxProps.onDrawingsChange as (d: Drawing[]) => void)([
        ...seeded,
        { id: 's2', kind: 'hline', p1: { date: '2026-09-02', price: 3050 } },
      ]);
    });
    expect(JSON.parse(localStorage.getItem('liveprofit.market.drawings.v1..INX')!)).toHaveLength(2);
    // 其他标的 key 无 .INX 数据污染（各 section 挂载即保存自己的空数组，key 隔离成立）
    expect(JSON.parse(localStorage.getItem('liveprofit.market.drawings.v1.399001.SZ')!)).toEqual([]);
  });
});
