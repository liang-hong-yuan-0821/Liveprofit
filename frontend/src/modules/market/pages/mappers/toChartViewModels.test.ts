import { describe, expect, it } from 'vitest';
import type { BarDTO, IndicatorsDTO } from '../../../../api/generated';
import { barsToCandlestickViewModel } from './toChartViewModels';

// groupAssetsByMarket 随目录端点删除（目录写死 MARKET_INDEX_CATALOG，
// 组序固化——"12 指数 + 组序"回归迁为 MarketIndicesPanel 测试的常量断言）。

function bar(timestamp: string, o: number, h: number, l: number, c: number): BarDTO {
  return { timestamp, open: o, high: h, low: l, close: c, volume: null };
}

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

  it('macd 与 bars 等长时透传到 ViewModel', () => {
    const indicators: IndicatorsDTO = {
      ma: [{ period: 5, values: [null, 1.6] }],
      boll: { period: 20, k: 2, mid: [null, 1.6], upper: [null, 1.9], lower: [null, 1.3] },
      macd: { fast: 12, slow: 26, signal: 9, dif: [null, 0.3], dea: [null, 0.2], hist: [null, 0.2] },
    };
    const model = barsToCandlestickViewModel(
      [bar('2026-09-03T00:00:00Z', 1, 2, 0.5, 1.8), bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)],
      indicators,
    );
    expect(model?.macd).toEqual({ dif: [null, 0.3], dea: [null, 0.2], hist: [null, 0.2] });
  });

  it('macd 数组与 bars 不等长时仅丢弃 macd（ma/boll 保留）', () => {
    const indicators: IndicatorsDTO = {
      ma: [{ period: 5, values: [null, 1.6] }],
      boll: { period: 20, k: 2, mid: [null, 1.6], upper: [null, 1.9], lower: [null, 1.3] },
      macd: { fast: 12, slow: 26, signal: 9, dif: [null], dea: [null], hist: [null] },
    };
    const model = barsToCandlestickViewModel(
      [bar('2026-09-03T00:00:00Z', 1, 2, 0.5, 1.8), bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)],
      indicators,
    );
    expect(model?.macd).toBeUndefined();
    expect(model?.ma).toHaveLength(1);
    expect(model?.boll).toBeDefined();
  });

  it('macd 三数组全 null 时整体丢弃（视为无副图，不白让主图高度）', () => {
    const indicators: IndicatorsDTO = {
      ma: [{ period: 5, values: [null, null] }],
      boll: { period: 20, k: 2, mid: [null, null], upper: [null, null], lower: [null, null] },
      macd: { fast: 12, slow: 26, signal: 9, dif: [null, null], dea: [null, null], hist: [null, null] },
    };
    const model = barsToCandlestickViewModel(
      [bar('2026-09-03T00:00:00Z', 1, 2, 0.5, 1.8), bar('2026-09-04T00:00:00Z', 4, 5, 3, 4.5)],
      indicators,
    );
    expect(model?.macd).toBeUndefined();
  });
});
