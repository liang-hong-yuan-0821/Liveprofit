import type { ImpactDraftDTO } from '../../../../../api/generated';

// 影响草稿平铺：event_id × ticker × window_type 一行；CAR/t 值格式化到展示精度。
export interface ImpactRowVM {
  key: string;
  eventId: number;
  title: string;
  t0: string | null;
  ticker: string;
  windowType: string;
  carPct: string; // 三位小数百分比数值；无值空串
  tStat: string; // 两位小数；无值空串
  direction: 1 | -1 | 0 | null; // 1 利好 / -1 利空 / 0 中性 / null 未知
  directionLabel: string;
  contaminated: boolean;
  error: string | null;
}

const DIRECTION_LABELS: Record<number, string> = {
  1: '利好 ↑',
  [-1]: '利空 ↓',
  0: '中性 →',
};

function num(value: unknown): number | null {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null;
}

function directionOf(value: unknown): 1 | -1 | 0 | null {
  const n = num(value);
  return n === 1 || n === -1 || n === 0 ? n : null;
}

export function toImpactRowVM(draft: ImpactDraftDTO): ImpactRowVM[] {
  const assets = draft.assets ?? {};
  const rows: ImpactRowVM[] = [];
  for (const [ticker, windows] of Object.entries(assets)) {
    for (const [windowType, result] of Object.entries(windows ?? {})) {
      const car = num(result?.cumulative_abnormal_return);
      const t = num(result?.t_stat);
      const direction = directionOf(result?.direction);
      rows.push({
        key: `${draft.event_id}:${ticker}:${windowType}`,
        eventId: draft.event_id,
        title: draft.title,
        t0: draft.t0 ?? null,
        ticker,
        windowType,
        carPct: car === null ? '' : (car * 100).toFixed(3),
        tStat: t === null ? '' : t.toFixed(2),
        direction,
        directionLabel: direction === null ? '?' : DIRECTION_LABELS[direction],
        contaminated: result?.is_contaminated === true,
        error: typeof result?.error === 'string' ? result.error : null,
      });
    }
  }
  return rows;
}
