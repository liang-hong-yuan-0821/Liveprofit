import { describe, expect, it } from 'vitest';
import {
  compareStreamIds,
  parseStreamId,
  parseTaskEvent,
} from './taskEventProtocol';

const TASK_ID = 'task-1';

function businessData(type: string, overrides: Record<string, unknown> = {}): string {
  const base: Record<string, unknown> = {
    task_id: TASK_ID,
    attempt_no: 1,
    occurred_at: '2026-09-05T09:12:01Z',
    schema_version: 'v1',
  };
  if (type === 'progress') Object.assign(base, { sequence: 3, phase: 'sector', message: '板块层分析完成' });
  if (type === 'completed') Object.assign(base, { report_id: 'r-1', duration_ms: 1200 });
  if (type === 'failed') Object.assign(base, { error_code: 'X', message: '失败' });
  if (type === 'cancelled') Object.assign(base, { message: '已取消' });
  if (type === 'started') Object.assign(base, { worker_id: 'w-1' });
  return JSON.stringify({ ...base, ...overrides });
}

describe('parseStreamId / compareStreamIds', () => {
  it('解析 ms-seq 为 BigInt 两段；非法返回 null', () => {
    expect(parseStreamId('1725520000000-0')).toEqual([1725520000000n, 0n]);
    expect(parseStreamId('abc')).toBeNull();
    expect(parseStreamId('1-2-3')).toBeNull();
  });

  it('两段 BigInt 数值比较', () => {
    expect(compareStreamIds('1-5', '1-5')).toBe(0);
    expect(compareStreamIds('1-5', '2-0')).toBe(-1);
    expect(compareStreamIds('2-0', '1-9')).toBe(1);
    expect(compareStreamIds('1-10', '1-2')).toBe(1);
    expect(Number.isNaN(compareStreamIds('bad', '1-0'))).toBe(true);
  });
});

describe('parseTaskEvent 业务帧严格校验', () => {
  it('合法 progress 帧返回 business 事件', () => {
    const result = parseTaskEvent('progress', businessData('progress'), '1-0', TASK_ID);
    expect(result.kind).toBe('business');
    if (result.kind === 'business') {
      expect(result.event.type).toBe('progress');
      expect(result.event.id).toBe('1-0');
      expect(result.event.attemptNo).toBe(1);
    }
  });

  it('未知事件类型 → invalid', () => {
    expect(parseTaskEvent('retrying', '{}', '1-0', TASK_ID).kind).toBe('invalid');
  });

  it('畸形 JSON / 非对象 → invalid', () => {
    expect(parseTaskEvent('queued', '{bad', '1-0', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('queued', '"str"', '1-0', TASK_ID).kind).toBe('invalid');
  });

  it('未知字段 → invalid', () => {
    const result = parseTaskEvent('queued', businessData('queued', { extra: 1 }), '1-0', TASK_ID);
    expect(result).toMatchObject({ kind: 'invalid', reason: expect.stringContaining('未知字段') });
  });

  it('版本不匹配 / 任务不匹配 → invalid', () => {
    expect(parseTaskEvent('queued', businessData('queued', { schema_version: 'v2' }), '1-0', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('queued', businessData('queued', { task_id: 'other' }), '1-0', TASK_ID).kind).toBe('invalid');
  });

  it('attempt_no 非法（0/负数/小数/缺失）→ invalid', () => {
    expect(parseTaskEvent('queued', businessData('queued', { attempt_no: 0 }), '1-0', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('queued', businessData('queued', { attempt_no: -1 }), '1-0', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('queued', businessData('queued', { attempt_no: 1.5 }), '1-0', TASK_ID).kind).toBe('invalid');
  });

  it('occurred_at 非法 → invalid', () => {
    expect(parseTaskEvent('queued', businessData('queued', { occurred_at: 'not-a-time' }), '1-0', TASK_ID).kind).toBe('invalid');
  });

  it('非法 Stream ID（空/格式错误）→ invalid', () => {
    expect(parseTaskEvent('queued', businessData('queued'), '', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('queued', businessData('queued'), 'xx', TASK_ID).kind).toBe('invalid');
  });

  it('failed 帧含 retryable 字段 → invalid（白名单拒绝）', () => {
    expect(parseTaskEvent('failed', businessData('failed', { retryable: true }), '1-0', TASK_ID).kind).toBe('invalid');
  });
});

describe('parseTaskEvent 控制帧', () => {
  it('合法 reset 帧（固定四字段、无 ID）返回 control', () => {
    const data = JSON.stringify({
      task_url: '/api/v1/analysis-tasks/task-1/events',
      earliest_event_id: '9-9',
      occurred_at: '2026-09-05T09:00:00Z',
      schema_version: 'v1',
    });
    const result = parseTaskEvent('reset', data, '', TASK_ID);
    expect(result.kind).toBe('control-reset');
    if (result.kind === 'control-reset') {
      expect(result.event.earliestEventId).toBe('9-9');
    }
  });

  it('合法 heartbeat 帧返回 control', () => {
    const data = JSON.stringify({ connection_id: 'c-1', sent_at: '2026-09-05T09:00:00Z', schema_version: 'v1' });
    const result = parseTaskEvent('heartbeat', data, '', TASK_ID);
    expect(result.kind).toBe('control-heartbeat');
  });

  it('控制帧多字段/缺字段/版本不符 → invalid', () => {
    const reset = { task_url: '/x', earliest_event_id: '1-0', occurred_at: '2026-09-05T09:00:00Z', schema_version: 'v1' };
    expect(parseTaskEvent('reset', JSON.stringify({ ...reset, extra: 1 }), '', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('reset', JSON.stringify({ task_url: '/x' }), '', TASK_ID).kind).toBe('invalid');
    expect(parseTaskEvent('heartbeat', JSON.stringify({ connection_id: 'c', sent_at: 't', schema_version: 'v2' }), '', TASK_ID).kind).toBe('invalid');
  });
});
