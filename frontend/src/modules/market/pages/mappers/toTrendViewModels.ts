import type { TrendSeriesDTO } from '../../../../api/generated';
import type { LineChartSeries } from '../../../../shared/charts/LineChart';

// 趋势 DTO → 折线 ViewModel（趋势对比面板方案 4.4.1）：共同首日=100 归一。
// 图表只接收 ViewModel；序列名一律取响应的 series[].name（前端不硬编码指数名单）。
// 归一基点 = 共同首日（2026-09-19 用户拍板）：各序列以"所有序列都有数据的第一个
// 交易日"为基点日，各自该日 close 记 100，基点日之前的点丢弃——「全部」区间下
// 分层组从 2014-01-02 起、板组从 2019-12-31 起，换取多线同起点可比。

/** 共同首日 = 各非空序列首点日期的最大值（= 所有序列都有数据的第一个交易日）。
 * 全部序列为空 → null。 */
export function commonBaseDate(series: TrendSeriesDTO[]): string | null {
  const firsts = series
    .filter((s) => s.points.length > 0)
    .map((s) => s.points[0].date);
  if (firsts.length === 0) return null;
  return firsts.reduce((a, b) => (a > b ? a : b)); // ISO 日期字符串字典序 = 时间序
}

/** DTO → 图表 ViewModel：按共同首日归一（共同首日 = 100），baseDate 之前的点丢弃。
 * 某序列在 baseDate 缺行 → 基点落到其后首个有数据的交易日（仍记 100）；
 * 空序列 → data: []（保留在 legend）；全部为空 → 返回 []。 */
export function toTrendChartSeries(series: TrendSeriesDTO[]): LineChartSeries[] {
  const baseDate = commonBaseDate(series);
  if (baseDate === null) return [];
  return series.map((s) => {
    if (s.points.length === 0) return { name: s.name, data: [] };
    // 序列升序契约（服务端 ORDER BY trade_date）：基点 = date >= baseDate 的首个点
    const baseIndex = s.points.findIndex((p) => p.date >= baseDate);
    if (baseIndex < 0) return { name: s.name, data: [] };
    const baseClose = s.points[baseIndex].close;
    if (!(baseClose > 0)) return { name: s.name, data: [] }; // close 恒正（NOT NULL 价格）；防御 0/NaN
    return {
      name: s.name,
      data: s.points.slice(baseIndex).map((p) => [
        p.date,
        Math.round((p.close / baseClose) * 10000) / 100, // 四舍五入 2 位
      ]),
    };
  });
}
