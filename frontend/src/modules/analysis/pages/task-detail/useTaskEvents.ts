import { useEffect, useRef, useState } from 'react';
import {
  TASK_EVENT_TYPES,
  compareStreamIds,
  parseTaskEvent,
  type TaskBusinessEvent,
} from './taskEventProtocol';

// 唯一可创建和关闭 EventSource 的 Hook，首期只被任务详情使用（方案 §2.5 / API 契约 §五）：
// - canonical events_url 校验失败视为 API 协议错误，仅 REST 轮询，不建流；
// - 只注册八个命名事件；任何帧先过 parseTaskEvent 严格校验；
// - 仅接收 Stream ID 严格递增的业务事件（重复/乱序/旧事件不写入），时间线最多 50 条；
// - onerror 立即关闭并按 1s/2s/4s/8s…（上限 30s）重建；连续 5 次失败、合法 reset 或协议错误转 REST 轮询；
// - 事件窗口、lastEventId、连接状态、重连次数均为本 Hook State，不创建全局 store。

export type ConnectionState = 'connected' | 'reconnecting' | 'rest-polling';

export const MAX_TIMELINE_EVENTS = 50;
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000];
export const MAX_RECONNECT_DELAY_MS = 30000;
export const MAX_RECONNECT_FAILURES = 5;
/** 心跳超时判定（API 契约 §五.5）：45s 无任何事件按断连处理 */
export const HEARTBEAT_TIMEOUT_MS = 45_000;
/** 空闲看门狗检查节拍 */
export const HEARTBEAT_WATCHDOG_INTERVAL_MS = 5_000;

export interface UseTaskEventsOptions {
  taskId: string;
  eventsUrl: string;
  /** 任务非终态且 events_url 合法时才为 true */
  enabled: boolean;
}

export function canonicalEventsUrl(taskId: string): string {
  return `/api/v1/analysis-tasks/${taskId}/events`;
}

export function useTaskEvents({ taskId, eventsUrl, enabled }: UseTaskEventsOptions): {
  events: TaskBusinessEvent[];
  connectionState: ConnectionState;
} {
  const [events, setEvents] = useState<TaskBusinessEvent[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>('rest-polling');
  const cursorRef = useRef<string | null>(null);
  const failuresRef = useRef(0);
  // 心跳看门狗：任何帧（含 heartbeat）与连接建立都刷新最后活动时间；
  // 45s 无任何事件按断连处理（半开连接不会触发 onerror）
  const lastActivityRef = useRef(Date.now());

  useEffect(() => {
    if (!enabled) {
      setConnectionState('rest-polling');
      return;
    }
    if (eventsUrl !== canonicalEventsUrl(taskId)) {
      setConnectionState('rest-polling');
      return;
    }

    let source: EventSource | null = null;
    let disposed = false;
    // rest-polling 后放弃重建：防止已排程的重连定时器在终态后产生 stray 重连
    let abandoned = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    cursorRef.current = null;
    failuresRef.current = 0;
    setEvents([]);
    setConnectionState('reconnecting');
    lastActivityRef.current = Date.now();

    const cleanup = () => {
      disposed = true;
      timers.forEach((timer) => clearTimeout(timer));
      source?.close();
      source = null;
    };

    const stopStreamAndPoll = () => {
      abandoned = true;
      source?.close();
      source = null;
      setConnectionState('rest-polling');
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      if (failuresRef.current >= MAX_RECONNECT_FAILURES) {
        setConnectionState('rest-polling');
        return;
      }
      setConnectionState('reconnecting');
      const index = Math.min(failuresRef.current - 1, RECONNECT_DELAYS_MS.length - 1);
      const delay = Math.min(RECONNECT_DELAYS_MS[index], MAX_RECONNECT_DELAY_MS);
      timers.push(setTimeout(open, delay));
    };

    const handleMessage = (type: string, raw: MessageEvent) => {
      if (disposed) return;
      lastActivityRef.current = Date.now();
      const data = typeof raw.data === 'string' ? raw.data : String(raw.data);
      const parsed = parseTaskEvent(type, data, raw.lastEventId, taskId);

      if (parsed.kind === 'invalid') {
        // 协议错误：立即停止该连接、不写入时间线/游标，转 REST 轮询
        stopStreamAndPoll();
        return;
      }

      if (parsed.kind === 'control-reset') {
        // 合法 reset：清空本地进度窗口，转 REST 轮询（不把残缺历史展示为完整记录）
        setEvents([]);
        cursorRef.current = null;
        stopStreamAndPoll();
        return;
      }
      if (parsed.kind === 'control-heartbeat') {
        // heartbeat 仅保活，无操作
        return;
      }

      // 仅接受严格递增业务事件；重复/乱序/旧事件不写入
      if (cursorRef.current !== null && compareStreamIds(parsed.event.id, cursorRef.current) <= 0) {
        return;
      }
      cursorRef.current = parsed.event.id;
      setEvents((prev) => [...prev.slice(-(MAX_TIMELINE_EVENTS - 1)), parsed.event]);
    };

    const open = () => {
      if (disposed || abandoned) return;
      source?.close();
      const url = cursorRef.current
        ? `${eventsUrl}?after=${encodeURIComponent(cursorRef.current)}`
        : eventsUrl;
      const es = new EventSource(url);
      source = es;

      TASK_EVENT_TYPES.forEach((type) => {
        es.addEventListener(type, (raw: Event) => handleMessage(type, raw as MessageEvent));
      });
      es.onopen = () => {
        failuresRef.current = 0;
        lastActivityRef.current = Date.now();
        setConnectionState('connected');
      };
      es.onerror = () => {
        // onerror 立即关闭连接，按指数退避手动重建；连续五次失败转 REST 轮询。
        // 同步置 source=null：看门狗只以 source 判活，避免退避等待期内双调度叠加
        es.close();
        source = null;
        failuresRef.current += 1;
        scheduleReconnect();
      };
    };

    open();

    // 空闲看门狗：仅在流存在时生效；超时按断连处理（计入失败次数，同一退避路径）
    timers.push(
      setInterval(() => {
        if (disposed || source === null) return;
        if (Date.now() - lastActivityRef.current >= HEARTBEAT_TIMEOUT_MS) {
          source.close();
          source = null;
          failuresRef.current += 1;
          scheduleReconnect();
        }
      }, HEARTBEAT_WATCHDOG_INTERVAL_MS),
    );

    return cleanup;
  }, [taskId, eventsUrl, enabled]);

  return { events, connectionState };
}
