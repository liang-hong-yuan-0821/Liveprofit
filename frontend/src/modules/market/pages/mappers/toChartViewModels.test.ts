import { describe, expect, it } from 'vitest';
import type { BarDTO, IndicatorsDTO, MarketAssetDTO } from '../../../../api/generated';
import { barsToCandlestickViewModel, groupAssetsByMarket } from './toChartViewModels';

function asset(market: string, symbol: string, displayOrder: number): MarketAssetDTO {
  return {
    market: market as MarketAssetDTO['market'],
    symbol,
    name: symbol,
    currency: 'USD',
    market_timezone: 'UTC',
    display_order: displayOrder,
    enabled: true,
    supported_intervals: ['1d'],
    availability_status: 'AVAILABLE' as MarketAssetDTO['availability_status'],
  };
}

function bar(timestamp: string, o: number, h: number, l: number, c: number): BarDTO {
  return { timestamp, open: o, high: h, low: l, close: c, volume: null };
}

describe('groupAssetsByMarket', () => {
  it('固定组序 US → KR → CN；组内按服务端 display_order ASC；乱序入参不受影响', () => {
    const groups = groupAssetsByMarket([
      asset('CN', '000016.SH', 30),
      asset('US', '.DJI', 20),
      asset('CN', '000001.SH', 10),
      asset('US', '.INX', 10),
      asset('KR', 'KOSDAQ', 20),
      asset('KR', 'KOSPI', 10),
    ]);

    expect(groups.map((group) => group.market)).toEqual(['US', 'KR', 'CN']);
    expect(groups[0].assets.map((a) => a.symbol)).toEqual(['.INX', '.DJI']);
    expect(groups[1].assets.map((a) => a.symbol)).toEqual(['KOSPI', 'KOSDAQ']);
    expect(groups[2].assets.map((a) => a.symbol)).toEqual(['000001.SH', '000016.SH']);
  });

  it('无资产的市场返回空组（正常空态，不补前端资产）', () => {
    const groups = groupAssetsByMarket([asset('US', '.INX', 10)]);
    expect(groups.map((group) => [group.market, group.assets.length])).toEqual([
      ['US', 1],
      ['KR', 0],
      ['CN', 0],
    ]);
  });
});

describe('barsToCandlestickViewModel', () => {
  it('空 bars 返回 null（调用方不渲染空壳图）', () => {
    expect(barsToCandlestickViewModel([])).toBeNull();
  });

  it('按时间升序映射 [open, close, low, high]', () => {
    const model = barsToCandlestickViewModel([
      bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5),
      bar('2026-09-03T00:00:00Z', 1, 2, 0.5, 1.8),
    ]);

    expect(model).not.toBeNull();
    expect(model?.xAxisData).toEqual(['2026-09-03', '2026-09-04']);
    expect(model?.ohlc).toEqual([
      [1, 1.8, 0.5, 2],
      [4, 4.5, 3, 5],
    ]);
  });

  it('indicators 与 bars 等长时透传 ma/boll 到 ViewModel', () => {
    const indicators: IndicatorsDTO = {
      ma: [{ period: 5, values: [null, 1.6] }],
      boll: {
        period: 20,
        k: 2,
        mid: [null, 1.6],
        upper: [null, 1.9],
        lower: [null, 1.3],
      },
    };
    const model = barsToCandlestickViewModel(
      [bar('2026-09-03T00:00:00Z', 1, 2, 0.5, 1.8), bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)],
      indicators,
    );

    expect(model?.ma).toEqual([{ period: 5, values: [null, 1.6] }]);
    expect(model?.boll).toEqual({
      period: 20,
      k: 2,
      mid: [null, 1.6],
      upper: [null, 1.9],
      lower: [null, 1.3],
    });
  });

  it('indicators 缺失/null 时不设置 ma/boll（旧后端降级渲染纯 K 线）', () => {
    const bars = [bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)];
    expect(barsToCandlestickViewModel(bars)?.ma).toBeUndefined();
    expect(barsToCandlestickViewModel(bars, null)?.boll).toBeUndefined();
  });

  it('indicators 数组长度与 bars 不一致时整体丢弃（不渲染错位指标）', () => {
    const indicators: IndicatorsDTO = {
      ma: [{ period: 5, values: [null, 1.6] }],
      boll: {
        period: 20,
        k: 2,
        mid: [null, 1.6, 1.7],
        upper: [null, 1.9, 2.0],
        lower: [null, 1.3, 1.4],
      },
    };
    const model = barsToCandlestickViewModel([bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)], indicators);

    expect(model).not.toBeNull();
    expect(model?.ma).toBeUndefined();
    expect(model?.boll).toBeUndefined();
  });
});
