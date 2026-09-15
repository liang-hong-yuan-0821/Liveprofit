import type { BarDTO, IndicatorsDTO } from '../../../../api/generated';
import type { CandlestickChartViewModel } from '../../../../shared/charts/CandlestickChart';

// DTO → 图表 ViewModel（图表只接收 ViewModel，不识别市场代码/OHLC 字段/报告 DTO）。
// groupAssetsByMarket 随目录端点删除（目录前端写死 MARKET_INDEX_CATALOG，组序固化）。

/** bars 升序序列 → K 线图 ViewModel；无 bars 返回 null（调用方不渲染空壳图）。
 *  indicators 可选（旧后端/降级无此字段）：各数组长度与 bars 不一致时整体丢弃（不渲染错位指标）。
 *  对齐前提：服务端契约保证 bars 升序（本地 sort 为既有防御，升序输入下为恒等）；indicators
 *  按后端原序透传，若后端违反升序契约则两者会按 index 错位——以契约为准，不在此二次猜测。 */
export function barsToCandlestickViewModel(
  bars: BarDTO[],
  indicators?: IndicatorsDTO | null,
): CandlestickChartViewModel | null {
  if (bars.length === 0) return null;
  const sorted = [...bars].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  const viewModel: CandlestickChartViewModel = {
    xAxisData: sorted.map((bar) => bar.timestamp.slice(0, 10)),
    ohlc: sorted.map((bar) => [bar.open, bar.close, bar.low, bar.high] as [number, number, number, number]),
    volume: sorted.map((bar) => bar.volume),
  };
  if (indicators && indicatorsAligned(indicators, sorted.length)) {
    viewModel.ma = indicators.ma.map((line) => ({ period: line.period, values: line.values }));
    viewModel.boll = {
      period: indicators.boll.period,
      k: indicators.boll.k,
      mid: indicators.boll.mid,
      upper: indicators.boll.upper,
      lower: indicators.boll.lower,
    };
    if (indicators.macd && macdAligned(indicators.macd, sorted.length)) {
      viewModel.macd = {
        dif: indicators.macd.dif,
        dea: indicators.macd.dea,
        hist: indicators.macd.hist,
      };
    }
  }
  return viewModel;
}

function indicatorsAligned(indicators: IndicatorsDTO, barCount: number): boolean {
  const { ma, boll } = indicators;
  return (
    ma.every((line) => line.values.length === barCount) &&
    boll.mid.length === barCount &&
    boll.upper.length === barCount &&
    boll.lower.length === barCount
  );
}

/** macd 两类防御：① 任一数组与 bars 不等长 → 整体丢弃（不渲染错位副图）；
 * ② 三数组全 null → 整体丢弃（视为无副图，不白白让出主图高度）。 */
function macdAligned(macd: NonNullable<IndicatorsDTO['macd']>, barCount: number): boolean {
  const { dif, dea, hist } = macd;
  if (dif.length !== barCount || dea.length !== barCount || hist.length !== barCount) return false;
  const allNull = (values: (number | null)[]) => values.every((value) => value === null);
  if (allNull(dif) && allNull(dea) && allNull(hist)) return false;
  return true;
}
