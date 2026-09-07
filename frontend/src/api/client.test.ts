import { describe, expect, it } from 'vitest';
import { ApiError, parseProblemDetails, requestEnvelope, toApiError, unwrapEnvelope } from './client';

describe('unwrapEnvelope', () => {
  it('解包成功 envelope { data, meta }', () => {
    const result = unwrapEnvelope<{ taskId: string }>({
      data: { taskId: 'abc' },
      meta: { request_id: 'req-1', schema_version: 'v1' },
    });
    expect(result.data.taskId).toBe('abc');
    expect(result.meta.request_id).toBe('req-1');
  });

  it('支持 Cursor 分页 meta.next_cursor（最后一页为 null）', () => {
    const page = unwrapEnvelope<unknown[]>({
      data: [],
      meta: { next_cursor: null },
    });
    expect(page.meta.next_cursor).toBeNull();
  });

  it('DELETE 成功按 200 envelope 处理（data={deleted, resource_id}，不假设 204）', () => {
    const result = unwrapEnvelope<{ deleted: boolean; resource_id: string }>({
      data: { deleted: true, resource_id: 'res-1' },
      meta: { request_id: 'req-2' },
    });
    expect(result.data.deleted).toBe(true);
    expect(result.data.resource_id).toBe('res-1');
  });

  it('缺失 data 或 meta 时抛出 INVALID_ENVELOPE ApiError', () => {
    expect(() => unwrapEnvelope({ data: {} })).toThrow(ApiError);
    expect(() => unwrapEnvelope({ meta: {} })).toThrow(ApiError);
    expect(() => unwrapEnvelope(null)).toThrow(ApiError);
  });
});

describe('parseProblemDetails', () => {
  it('保留稳定 code、request_id 与 retryable', () => {
    const err = parseProblemDetails(
      { error: { code: 'MARKET_DATA_UPSTREAM_UNAVAILABLE', message: '行情上游暂不可用', request_id: 'req-3', retryable: true } },
      503,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('MARKET_DATA_UPSTREAM_UNAVAILABLE');
    expect(err.requestId).toBe('req-3');
    expect(err.retryable).toBe(true);
    expect(err.status).toBe(503);
  });

  it('兼容 { error: {...} } 包裹与顶层两种形态', () => {
    const wrapped = parseProblemDetails({ error: { code: 'X', retryable: false } }, 500);
    const flat = parseProblemDetails({ code: 'X', retryable: false }, 500);
    expect(wrapped.code).toBe('X');
    expect(flat.code).toBe('X');
  });

  it('字段缺失时兜底 INTERNAL_ERROR 且不抛出', () => {
    const err = parseProblemDetails('not json', 502);
    expect(err.code).toBe('INTERNAL_ERROR');
    expect(err.retryable).toBe(false);
  });
});

describe('requestEnvelope 超时', () => {
  it('超过 45s 未响应：调用 cancel 并抛 REQUEST_TIMEOUT（不可重试）', async () => {
    vi.useFakeTimers();
    const cancel = vi.fn();
    const pending = new Promise<never>(() => {}) as Promise<unknown> & { cancel?: () => void };
    pending.cancel = cancel;

    const assertion = expect(requestEnvelope(pending)).rejects.toMatchObject({
      code: 'REQUEST_TIMEOUT',
      retryable: false,
    });
    await vi.advanceTimersByTimeAsync(45_000);
    await assertion;
    expect(cancel).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

describe('toApiError', () => {
  it('ApiError 原样返回', () => {
    const err = new ApiError({ code: 'X', retryable: true, status: 503 });
    expect(toApiError(err)).toBe(err);
  });

  it('普通 Error 归一为不可重试 UNKNOWN，保留可读 message', () => {
    const err = toApiError(new Error('boom'));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('UNKNOWN');
    expect(err.retryable).toBe(false);
    expect(err.message).toBe('boom');
  });
});
