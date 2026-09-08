// 统一 base URL、超时、成功 envelope 解包与 RFC 7807 Problem Details 适配。
// generated client（src/api/generated/，由 openapi-typescript-codegen 生成）不直连 feature：
// feature 只经本文件的 requestEnvelope 消费服务方法；页面不得直接发起 fetch。

import { OpenAPI } from './generated';
import { ApiError as GeneratedApiError } from './generated/core/ApiError';

export interface ApiMeta {
  request_id?: string;
  schema_version?: string;
  next_cursor?: string | null;
}

export interface ApiEnvelope<T> {
  data: T;
  meta: ApiMeta;
}

/** 统一 envelope：所有 /api/v1 成功响应（含 DELETE，200 + data={deleted, resource_id}）均为 { data, meta } */
export function unwrapEnvelope<T>(payload: unknown): ApiEnvelope<T> {
  if (!isRecord(payload) || !('data' in payload) || !isRecord(payload.meta)) {
    throw new ApiError({
      code: 'INVALID_ENVELOPE',
      message: '响应不符合 { data, meta } envelope',
      retryable: false,
      status: 200,
    });
  }
  return payload as unknown as ApiEnvelope<T>;
}

export class ApiError extends Error {
  readonly code: string;
  readonly requestId?: string;
  readonly retryable: boolean;
  readonly status: number;

  constructor(opts: { code: string; message?: string; requestId?: string; retryable: boolean; status: number }) {
    super(opts.message ?? opts.code);
    this.name = 'ApiError';
    this.code = opts.code;
    this.requestId = opts.requestId;
    this.retryable = opts.retryable;
    this.status = opts.status;
  }
}

/** RFC 7807 Problem Details → ApiError；兼容 { error: {...} } 包裹与顶层两种形态。 */
export function parseProblemDetails(payload: unknown, status: number): ApiError {
  const root = isRecord(payload) ? payload : {};
  const inner = isRecord(root.error) ? root.error : root;
  const code = typeof inner.code === 'string' ? inner.code : 'INTERNAL_ERROR';
  const message =
    typeof inner.message === 'string'
      ? inner.message
      : typeof inner.detail === 'string'
        ? inner.detail
        : undefined;
  const requestId = typeof inner.request_id === 'string' ? inner.request_id : undefined;
  const retryable = inner.retryable === true;
  return new ApiError({ code, message, requestId, retryable, status });
}

/** 将任意捕获异常归一为 ApiError：页面只展示稳定 code/可读摘要/request_id，不展示堆栈。 */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError({
    code: 'UNKNOWN',
    message: err instanceof Error ? err.message : '未知错误',
    retryable: false,
    status: 0,
  });
}

export const clientDefaults = {
  baseURL: '/api/v1',
  // 客户端请求超时兜底（API 契约 §7.1：预测类建议 45s，大于服务端 30s 超时）
  timeoutMs: 45_000,
} as const;

// generated client 的 BASE 为前缀拼接（BASE + path），而生成路径已含 /api/v1；
// OpenAPI.ts 默认 BASE 即为空串（重新生成后仍如此），此处显式置空并注释，防漂移。
OpenAPI.BASE = '';

/**
 * 统一消费 generated client 服务方法：解包 { data, meta } envelope，
 * 并将 generated ApiError（body 为 Problem Details）映射为页面 ApiError。
 * opts.timeoutMs 可覆盖默认 45s（如审核批量提交/预填用 300s——既有调用点零改动）。
 */
export async function requestEnvelope<TData>(
  promise: PromiseLike<unknown> & { cancel?: () => void },
  opts?: { timeoutMs?: number },
): Promise<ApiEnvelope<TData>> {
  const timeoutMs = opts?.timeoutMs ?? clientDefaults.timeoutMs;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const result = await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          promise.cancel?.();
          reject(
            new ApiError({ code: 'REQUEST_TIMEOUT', message: '请求超时，请稍后重试', retryable: false, status: 0 }),
          );
        }, timeoutMs);
      }),
    ]);
    return unwrapEnvelope<TData>(result);
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof GeneratedApiError) throw parseProblemDetails(err.body, err.status);
    throw toApiError(err);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
