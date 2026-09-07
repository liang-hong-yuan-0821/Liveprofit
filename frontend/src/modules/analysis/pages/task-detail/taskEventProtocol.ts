// SSE 任务事件严格解析（API 契约 §五）：仅八个命名事件；
// 业务帧必须含 Redis Stream ID、匹配 task_id、正整数 attempt_no、RFC 3339 occurred_at、
// schema_version 'v1'，且仅允许该事件类型的固定字段（未知字段视为协议错误）；
// reset/heartbeat 为无 ID 控制帧，payload 固定。畸形 JSON / 版本不匹配 / 任务不匹配 /
// 非法控制帧 / 非法 Stream ID 一律返回 invalid，不得写入时间线或推进游标。

export const BUSINESS_EVENT_TYPES = ['queued', 'started', 'progress', 'completed', 'failed', 'cancelled'] as const;
export type BusinessEventType = (typeof BUSINESS_EVENT_TYPES)[number];

export const CONTROL_EVENT_TYPES = ['reset', 'heartbeat'] as const;
export type ControlEventType = (typeof CONTROL_EVENT_TYPES)[number];

export const TASK_EVENT_TYPES = [...BUSINESS_EVENT_TYPES, ...CONTROL_EVENT_TYPES] as const;
export type TaskEventType = (typeof TASK_EVENT_TYPES)[number];

export interface TaskBusinessEvent {
  /** Redis Stream ID（'ms-seq'） */
  id: string;
  type: BusinessEventType;
  taskId: string;
  attemptNo: number;
  occurredAt: string;
  payload: Record<string, unknown>;
}

export interface ResetControlEvent {
  taskUrl: string;
  earliestEventId: string;
  occurredAt: string;
}

export interface HeartbeatControlEvent {
  connectionId: string;
  sentAt: string;
}

export type ParsedTaskEvent =
  | { kind: 'business'; event: TaskBusinessEvent }
  | { kind: 'control-reset'; event: ResetControlEvent }
  | { kind: 'control-heartbeat'; event: HeartbeatControlEvent }
  | { kind: 'invalid'; reason: string };

/** 'ms-seq' → [ms, seq]；非法返回 null */
export function parseStreamId(id: string): [bigint, bigint] | null {
  const match = /^(\d+)-(\d+)$/.exec(id);
  if (!match) return null;
  return [BigInt(match[1]), BigInt(match[2])];
}

/** 两段 BigInt 数值比较：a<b 返回 -1，a=b 返回 0，a>b 返回 1；任一非法返回 NaN */
export function compareStreamIds(a: string, b: string): number {
  const pa = parseStreamId(a);
  const pb = parseStreamId(b);
  if (!pa || !pb) return Number.NaN;
  if (pa[0] !== pb[0]) return pa[0] < pb[0] ? -1 : 1;
  if (pa[1] !== pb[1]) return pa[1] < pb[1] ? -1 : 1;
  return 0;
}

const COMMON_BUSINESS_KEYS = ['task_id', 'attempt_no', 'occurred_at', 'schema_version'] as const;

const EVENT_EXTRA_KEYS: Record<BusinessEventType, readonly string[]> = {
  queued: [],
  started: ['worker_id'],
  progress: ['sequence', 'phase', 'message'],
  completed: ['report_id', 'duration_ms'],
  failed: ['error_code', 'message'],
  cancelled: ['message'],
};

const CONTROL_KEYS: Record<ControlEventType, readonly string[]> = {
  reset: ['task_url', 'earliest_event_id', 'occurred_at', 'schema_version'],
  heartbeat: ['connection_id', 'sent_at', 'schema_version'],
};

export function parseTaskEvent(
  eventName: string,
  data: string,
  lastEventId: string,
  expectedTaskId: string,
): ParsedTaskEvent {
  if (!(TASK_EVENT_TYPES as readonly string[]).includes(eventName)) {
    return { kind: 'invalid', reason: `未知事件类型: ${eventName}` };
  }
  if ((CONTROL_EVENT_TYPES as readonly string[]).includes(eventName)) {
    return parseControlEvent(eventName as ControlEventType, data);
  }
  return parseBusinessEvent(eventName as BusinessEventType, data, lastEventId, expectedTaskId);
}

function parseJson(data: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(data);
    return isRecord(value) ? value : null;
  } catch {
    return null;
  }
}

function parseControlEvent(type: ControlEventType, data: string): ParsedTaskEvent {
  const payload = parseJson(data);
  if (!payload) return { kind: 'invalid', reason: '畸形 JSON' };

  const allowed = CONTROL_KEYS[type];
  const unknown = Object.keys(payload).filter((key) => !allowed.includes(key));
  if (unknown.length > 0) return { kind: 'invalid', reason: `未知字段: ${unknown.join(',')}` };
  const missing = allowed.filter((key) => !(key in payload));
  if (missing.length > 0) return { kind: 'invalid', reason: `缺少字段: ${missing.join(',')}` };
  if (payload.schema_version !== 'v1') return { kind: 'invalid', reason: '版本不匹配' };

  if (type === 'reset') {
    const occurredAt = payload.occurred_at;
    if (typeof occurredAt !== 'string' || !isRfc3339(occurredAt)) return { kind: 'invalid', reason: 'occurred_at 非法' };
    if (typeof payload.task_url !== 'string' || typeof payload.earliest_event_id !== 'string') {
      return { kind: 'invalid', reason: 'reset payload 类型非法' };
    }
    return {
      kind: 'control-reset',
      event: { taskUrl: payload.task_url, earliestEventId: payload.earliest_event_id, occurredAt },
    };
  }

  if (typeof payload.connection_id !== 'string' || typeof payload.sent_at !== 'string') {
    return { kind: 'invalid', reason: 'heartbeat payload 类型非法' };
  }
  return { kind: 'control-heartbeat', event: { connectionId: payload.connection_id, sentAt: payload.sent_at } };
}

function parseBusinessEvent(
  type: BusinessEventType,
  data: string,
  lastEventId: string,
  expectedTaskId: string,
): ParsedTaskEvent {
  const payload = parseJson(data);
  if (!payload) return { kind: 'invalid', reason: '畸形 JSON' };

  const allowed = [...COMMON_BUSINESS_KEYS, ...EVENT_EXTRA_KEYS[type]];
  const unknown = Object.keys(payload).filter((key) => !allowed.includes(key));
  if (unknown.length > 0) return { kind: 'invalid', reason: `未知字段: ${unknown.join(',')}` };
  const missing = COMMON_BUSINESS_KEYS.filter((key) => !(key in payload));
  if (missing.length > 0) return { kind: 'invalid', reason: `缺少字段: ${missing.join(',')}` };

  if (payload.schema_version !== 'v1') return { kind: 'invalid', reason: '版本不匹配' };
  if (payload.task_id !== expectedTaskId) return { kind: 'invalid', reason: '任务不匹配' };

  const attemptNo = payload.attempt_no;
  if (!Number.isInteger(attemptNo) || (attemptNo as number) <= 0) {
    return { kind: 'invalid', reason: 'attempt_no 非法' };
  }

  const occurredAt = payload.occurred_at;
  if (typeof occurredAt !== 'string' || !isRfc3339(occurredAt)) return { kind: 'invalid', reason: 'occurred_at 非法' };

  if (!lastEventId || parseStreamId(lastEventId) === null) {
    return { kind: 'invalid', reason: '非法 Stream ID' };
  }

  // failed 仅对应数据库 FAILED：payload 不可含 retryable=true（白名单已拒绝 retryable 字段）

  return {
    kind: 'business',
    event: {
      id: lastEventId,
      type,
      taskId: payload.task_id as string,
      attemptNo: attemptNo as number,
      occurredAt,
      payload,
    },
  };
}

function isRfc3339(value: string): boolean {
  return !Number.isNaN(Date.parse(value));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
