import { describe, expect, it } from 'vitest';

import type { ImpactDraftDTO } from '../../../../../api/generated';
import { toImpactRowVM } from './toImpactRowVM';

function draft(): ImpactDraftDTO {
  return {
    event_id: 101,
    title: 'CPI 公布',
    t0: '2026-08-03',
    computed_at: '2026-08-04T08:31:02.123456',
    assets: {
      '000001.SH': {
        pre_event_5d: {
          window_days: 5,
          cumulative_abnormal_return: 0.0123,
          t_stat: 2.15,
          direction: 1,
          is_contaminated: false,
        },
        post_event_5d: {
          window_days: 5,
          cumulative_abnormal_return: -0.02,
          t_stat: -2.5,
          direction: -1,
          is_contaminated: true,
          error: 'not enough data',
        },
      },
    },
  };
}

describe('toImpactRowVM', () => {
  it('平铺 event_id × ticker × window', () => {
    const rows = toImpactRowVM(draft());
    expect(rows.map((r) => r.key)).toEqual([
      '101:000001.SH:pre_event_5d',
      '101:000001.SH:post_event_5d',
    ]);
    expect(rows[0]).toMatchObject({ eventId: 101, title: 'CPI 公布', t0: '2026-08-03' });
  });

  it('CAR 三位小数、t 两位小数、方向徽章、污染角标', () => {
    const rows = toImpactRowVM(draft());
    expect(rows[0].carPct).toBe('1.230');
    expect(rows[0].tStat).toBe('2.15');
    expect(rows[0].direction).toBe(1);
    expect(rows[0].directionLabel).toBe('利好 ↑');
    expect(rows[0].contaminated).toBe(false);
    expect(rows[1].directionLabel).toBe('利空 ↓');
    expect(rows[1].contaminated).toBe(true);
    expect(rows[1].error).toBe('not enough data');
  });

  it('CAR/t 无值 → 空串；未知方向 → ?', () => {
    const rows = toImpactRowVM({
      event_id: 1,
      title: 'x',
      assets: {
        '000300.SH': {
          event_day: { window_days: 1, cumulative_abnormal_return: null, t_stat: null, direction: 7 },
        },
      },
    });
    expect(rows[0].carPct).toBe('');
    expect(rows[0].tStat).toBe('');
    expect(rows[0].direction).toBeNull();
    expect(rows[0].directionLabel).toBe('?');
  });

  it('assets 缺失 → 空列表', () => {
    expect(toImpactRowVM({ event_id: 1, title: 'x' })).toEqual([]);
  });
});
