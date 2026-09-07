import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeEventSource } from '../../../../test/fakeEventSource';
import {
  MAX_RECONNECT_FAILURES,
  MAX_TIMELINE_EVENTS,
  canonicalEventsUrl,
  useTaskEvents,
} from './useTaskEvents';

const TASK_ID = 'task-1';
const EVENTS_URL = canonicalEventsUrl(TASK_ID);

function businessData(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    task_id: TASK_ID,
    attempt_no: 1,
    occurred_at: '2026-09-05T09:12:01Z',
    schema_version: 'v1',
    ...overrides,
  });
}

function renderEvents(options: Partial<{ enabled: boolean; eventsUrl: string }> = {}) {
  return renderHook(() =>
    useTaskEvents({
      taskId: TASK_ID,
      eventsUrl: options.eventsUrl ?? EVENTS_URL,
      enabled: options.enabled ?? true,
    }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.reset();
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('useTaskEvents', () => {
  it('enabled=false 不创建 EventSource，状态 rest-polling', () => {
    const { result } = renderEvents({ enabled: false });
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current.connectionState).toBe('rest-polling');
  });

  it('events_url 非 canonical 视为协议错误：不建流，仅 REST 轮询', () => {
    const { result } = renderEvents({ eventsUrl: '/evil/url' });
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current.connectionState).toBe('rest-polling');
  });

  it('连接后按 url 建立 EventSource（首连不带 after）', () => {
    renderEvents();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe(EVENTS_URL);
  });

  it('仅接受严格递增业务事件；重复/乱序不写入；时间线最多 50 条', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];

    act(() => {
      es.emit('queued', businessData(), '1-0');
      es.emit('queued', businessData(), '1-0'); // 重复
      es.emit('queued', businessData(), '0-9'); // 旧事件
      es.emit('started', businessData({ worker_id: 'w-1' }), '1-1');
    });
    expect(result.current.events.map((event) => event.id)).toEqual(['1-0', '1-1']);

    act(() => {
      for (let i = 0; i < 60; i += 1) {
        es.emit('progress', businessData({ sequence: i, phase: 'market', message: 'm' }), `2-${i}`);
      }
    });
    expect(result.current.events).toHaveLength(MAX_TIMELINE_EVENTS);
    expect(result.current.events[0].id).toBe('2-10');
  });

  it('协议错误帧：立即关闭连接并转 REST 轮询，不写入时间线', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];

    act(() => {
      es.emit('progress', businessData({ sequence: 1, phase: 'market', message: 'm', extra: 1 }), '1-0');
    });
    expect(result.current.events).toHaveLength(0);
    expect(result.current.connectionState).toBe('rest-polling');
    expect(es.closed).toBe(true);
  });

  it('合法 reset：清空时间线并转 REST 轮询', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];

    act(() => {
      es.emit('queued', businessData(), '1-0');
      es.emit('reset', JSON.stringify({
        task_url: EVENTS_URL,
        earliest_event_id: '9-9',
        occurred_at: '2026-09-05T09:00:00Z',
        schema_version: 'v1',
      }));
    });
    expect(result.current.events).toHaveLength(0);
    expect(result.current.connectionState).toBe('rest-polling');
    expect(es.closed).toBe(true);
  });

  it('heartbeat 仅保活：不写入时间线、不改变状态', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];
    act(() => {
      es.open();
      es.emit('heartbeat', JSON.stringify({ connection_id: 'c-1', sent_at: '2026-09-05T09:00:00Z', schema_version: 'v1' }));
    });
    expect(result.current.connectionState).toBe('connected');
    expect(result.current.events).toHaveLength(0);
  });

  it('onerror 按指数退避重建；连续五次失败转 REST 轮询', () => {
    const { result } = renderEvents();
    expect(FakeEventSource.instances).toHaveLength(1);

    // 前 4 次失败：按 1s/2s/4s/8s 重建
    let expectedInstances = 1;
    for (const delay of [1000, 2000, 4000, 8000]) {
      act(() => {
        FakeEventSource.instances.at(-1)?.error();
      });
      expect(result.current.connectionState).toBe('reconnecting');
      act(() => {
        vi.advanceTimersByTime(delay);
      });
      expectedInstances += 1;
      expect(FakeEventSource.instances).toHaveLength(expectedInstances);
    }

    // 第 5 次失败：转 REST 轮询，不再重建
    act(() => {
      FakeEventSource.instances.at(-1)?.error();
    });
    expect(result.current.connectionState).toBe('rest-polling');
    const count = FakeEventSource.instances.length;
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(FakeEventSource.instances).toHaveLength(count);
    // 初始 1 次 + 前 4 次失败各重建 1 次 = 5；第 5 次失败后不再重建
    expect(count).toBe(MAX_RECONNECT_FAILURES);
  });

  it('重连成功重置失败计数并恢复 connected；断线重连带 ?after= 游标', () => {
    const { result } = renderEvents();
    const first = FakeEventSource.instances[0];
    act(() => {
      first.emit('queued', businessData(), '1-0');
      first.error();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    const second = FakeEventSource.instances[1];
    expect(second.url).toBe(`${EVENTS_URL}?after=1-0`);
    act(() => {
      second.open();
    });
    expect(result.current.connectionState).toBe('connected');
  });

  it('心跳看门狗：45s 无任何事件按断连处理，触发同一退避重建', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];
    act(() => {
      es.open();
    });
    expect(result.current.connectionState).toBe('connected');

    // 44s 无帧：仍保持连接
    act(() => {
      vi.advanceTimersByTime(44_000);
    });
    expect(result.current.connectionState).toBe('connected');
    expect(FakeEventSource.instances).toHaveLength(1);

    // 45s 无帧：按断连处理（关闭并退避重建）
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(es.closed).toBe(true);
    act(() => {
      vi.advanceTimersByTime(1_000); // 第一次失败的 1s 退避
    });
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it('有帧活动时不触发看门狗：heartbeat 刷新最后活动时间', () => {
    const { result } = renderEvents();
    const es = FakeEventSource.instances[0];
    act(() => {
      es.open();
    });
    // 每 40s 一条 heartbeat：持续 160s 不触发断连
    for (let i = 0; i < 4; i += 1) {
      act(() => {
        vi.advanceTimersByTime(40_000);
        es.emit('heartbeat', JSON.stringify({ connection_id: 'c-1', sent_at: '2026-09-05T09:00:00Z', schema_version: 'v1' }));
      });
    }
    expect(result.current.connectionState).toBe('connected');
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('协议错误转 REST 后不产生 stray 重连（已排程定时器被放弃）', () => {
    const { result } = renderEvents();
    const first = FakeEventSource.instances[0];
    // 断连 → 排程 1s 重建 → 重建后收到协议错误帧 → REST 轮询
    act(() => {
      first.error();
    });
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    const second = FakeEventSource.instances[1];
    act(() => {
      second.emit('progress', businessData({ sequence: 1, phase: 'market', message: 'm', extra: 1 }), '1-0');
    });
    expect(result.current.connectionState).toBe('rest-polling');

    // 长时间推进：不再有新的 EventSource 实例
    const count = FakeEventSource.instances.length;
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(FakeEventSource.instances).toHaveLength(count);
  });

  it('卸载清理：关闭 EventSource 并清理计时器', () => {
    const { unmount } = renderEvents();
    const es = FakeEventSource.instances[0];
    unmount();
    expect(es.closed).toBe(true);
  });
});
