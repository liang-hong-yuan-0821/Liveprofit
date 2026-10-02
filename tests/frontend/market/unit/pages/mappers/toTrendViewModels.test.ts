// test-catalog-begin
// {
//   "purpose": "行情界面 / commonBaseDate：共同首日 = 各非空序列首点日期的最大值；全空 → null；基点日之前点被丢弃、首点值 = 100、按基点 close 归一",
//   "keywords": [
//     "行情界面",
//     "市场分析",
//     "to_trend_view_models"
//   ],
//   "covers": [
//     "frontend/src/api/generated/index.ts",
//     "frontend/src/modules/market/pages/mappers/toTrendViewModels.ts"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { describe, expect, it } from 'vitest';
import type { TrendSeriesDTO } from '../../../../../../frontend/src/api/generated';
import { commonBaseDate, toTrendChartSeries } from '../../../../../../frontend/src/modules/market/pages/mappers/toTrendViewModels';

// 归一映射纯函数单测（趋势对比面板方案 4.4.3）：① 共同首日取最大值（三条起点
// 不同的序列）② 基点日之前点被丢弃、首点值 = 100 ③ 空序列返回 data: [] 且不影响
// 其他序列基点 ④ 全空返回 [] ⑤ 值四舍五入 2 位。
function seriesOf(name: string, ...points: Array<[string, number]>): TrendSeriesDTO {
  return {
    symbol: name,
    name,
    points: points.map(([date, close]) => ({ date, close })),
  };
}

// 沪深300 自 2005、中证1000 自 2010、中证2000 自 2014 → 共同首日 = 2014-01-02
const a = seriesOf('沪深300', ['2005-01-04', 1000], ['2014-01-02', 2200], ['2014-01-03', 2211]);
const b = seriesOf('中证1000', ['2010-01-04', 3000], ['2014-01-02', 4000]);
const c = seriesOf('中证2000', ['2014-01-02', 1000]);
const empty = seriesOf('中证500');

describe('commonBaseDate', () => {
  it('共同首日 = 各非空序列首点日期的最大值', () => {
    expect(commonBaseDate([a, b, c])).toBe('2014-01-02');
  });

  it('全空 → null', () => {
    expect(commonBaseDate([empty])).toBeNull();
  });
});

describe('toTrendChartSeries', () => {
  it('基点日之前点被丢弃、首点值 = 100、按基点 close 归一', () => {
    const out = toTrendChartSeries([a, b, c]);
    const byName = Object.fromEntries(out.map((s) => [s.name, s.data] as const));
    // 沪深300 的 2005 点、中证1000 的 2010 点被丢弃（共同首日口径的必然代价）
    expect(byName['沪深300'][0]).toEqual(['2014-01-02', 100]);
    expect(byName['沪深300'][1]).toEqual(['2014-01-03', Math.round((2211 / 2200) * 10000) / 100]);
    expect(byName['中证1000']).toEqual([['2014-01-02', 100]]);
    expect(byName['中证2000']).toEqual([['2014-01-02', 100]]);
  });

  it('某序列在 baseDate 缺行 → 基点落到其后首个有数据的交易日（仍记 100）', () => {
    const bGap = seriesOf('中证1000', ['2010-01-04', 3000], ['2014-01-03', 4100]);
    const out = toTrendChartSeries([a, bGap, c]);
    const bData = out.find((s) => s.name === '中证1000')?.data ?? [];
    expect(bData[0]).toEqual(['2014-01-03', 100]);
    expect(bData).toHaveLength(1);
  });

  it('空序列返回 data: [] 且不影响其他序列基点', () => {
    const out = toTrendChartSeries([a, b, empty]);
    expect(out.find((s) => s.name === '中证500')?.data).toEqual([]);
    expect(out.find((s) => s.name === '沪深300')?.data[0]).toEqual(['2014-01-02', 100]);
  });

  it('全部为空 → 返回 []', () => {
    expect(toTrendChartSeries([empty])).toEqual([]);
    expect(toTrendChartSeries([])).toEqual([]);
  });

  it('值四舍五入保留 2 位', () => {
    const s = seriesOf('X', ['2014-01-02', 300], ['2014-01-03', 100]);
    const out = toTrendChartSeries([s]);
    // 100 / 300 × 100 = 33.333… → 33.33
    expect(out[0].data[1][1]).toBe(33.33);
  });
});
