import { describe, expect, it } from 'vitest';

import type { PendingEventDTO } from '../../../../../api/generated';
import { toPendingEventRowVM } from './toPendingEventRowVM';

function draft(overrides: Partial<PendingEventDTO> = {}): PendingEventDTO {
  return {
    draft_id: 1,
    title: 'CPI 公布',
    announced_at: '2026-08-01T09:00:00+08:00',
    source: '金十数据',
    ...overrides,
  };
}

describe('toPendingEventRowVM', () => {
  it('默认值取 ai_suggestions，动作默认跳过', () => {
    const vm = toPendingEventRowVM(
      draft({
        ai_suggestions: {
          event_type: '宏观数据',
          event_subtype: 'CPI',
          event_condition: '超预期',
          importance: 5,
          expected_value: 1.9,
          actual_value: 2.1,
          previous_value: 1.5,
        },
      }),
    );
    expect(vm.eventType).toBe('宏观数据');
    expect(vm.eventSubtype).toBe('CPI');
    expect(vm.eventCondition).toBe('超预期');
    expect(vm.importance).toBe(5);
    expect(vm.expectedValue).toBe('1.9');
    expect(vm.actualValue).toBe('2.1');
    expect(vm.previousValue).toBe('1.5');
    expect(vm.action).toBe('skip');
  });

  it('importance 默认链：suggestions → importance_hint → 3', () => {
    expect(toPendingEventRowVM(draft()).importance).toBe(3);
    expect(toPendingEventRowVM(draft({ importance_hint: 4 })).importance).toBe(4);
    expect(
      toPendingEventRowVM(draft({ importance_hint: 4, ai_suggestions: { importance: 2 } })).importance,
    ).toBe(2);
  });

  it('数值缺失 → 空串（提交转 null，不落值）', () => {
    const vm = toPendingEventRowVM(draft({ ai_suggestions: {} }));
    expect(vm.expectedValue).toBe('');
    expect(vm.actualValue).toBe('');
    expect(vm.previousValue).toBe('');
  });

  it('数值 0 是合法值，不被吞', () => {
    const vm = toPendingEventRowVM(draft({ ai_suggestions: { expected_value: 0 } }));
    expect(vm.expectedValue).toBe('0');
  });

  it('缺字段兜底', () => {
    const vm = toPendingEventRowVM(draft());
    expect(vm.source).toBe('金十数据');
    expect(vm.title).toBe('CPI 公布');
    expect(vm.content).toBe('');
    expect(vm.sourceUrl).toBeNull();
    expect(vm.aiSuggestions).toBeNull();
  });
});
