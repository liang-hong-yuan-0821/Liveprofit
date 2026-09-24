import type { BarDTO } from '../../../../api/generated';

export function toIndexQuote(bars: readonly BarDTO[]) {
  const valid = bars.filter(bar => Number.isFinite(bar.close) && Number.isFinite(Date.parse(bar.timestamp))).slice().sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const last = valid.at(-1);
  const previous = valid.at(-2);
  return {
    close: last?.close ?? null,
    previousDate: previous?.timestamp.slice(0, 10) ?? null,
    date: last?.timestamp.slice(0, 10) ?? null,
    change: last && previous && previous.close > 0 ? last.close - previous.close : null,
    changePct: last && previous && previous.close > 0 ? (last.close / previous.close - 1) * 100 : null,
    closes: valid.slice(-30).map(bar => bar.close),
  };
}
