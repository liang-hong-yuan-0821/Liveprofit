import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { EmptyState } from '../../../../shared/feedback/EmptyState';
import { formatDateTime } from '../../../../shared/format/dateTime';
import type { TaskBusinessEvent } from './taskEventProtocol';

// 可信有限时间线：仅展示本页期间收到且通过严格校验的业务事件（≤50 条），
// 不是永久运行记录；RETRYING 无对应事件（仅 REST 呈现）。
const EVENT_LABELS: Record<string, string> = {
  queued: '排队',
  started: '开始执行',
  progress: '阶段进度',
  completed: '完成',
  failed: '失败',
  cancelled: '取消',
};

export function TaskProgressTimeline({ events }: { events: TaskBusinessEvent[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>进度时间线</CardTitle>
        <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
          仅展示本页期间收到的事件
        </span>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <EmptyState title="暂无进度事件" description="等待任务事件推送（排队/开始/阶段进度…）" />
        ) : (
          <ol className="flex flex-col gap-2 border-l pl-4" style={{ borderColor: 'var(--color-border)' }}>
            {events.map((event) => (
              <li key={event.id} className="flex flex-col gap-0.5 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{EVENT_LABELS[event.type] ?? event.type}</span>
                  <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    第 {event.attemptNo} 次尝试 · {formatDateTime(event.occurredAt)}
                  </span>
                </div>
                {event.type === 'progress' && (
                  <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    {typeof event.payload.phase === 'string' ? `${event.payload.phase}：` : ''}
                    {typeof event.payload.message === 'string' ? event.payload.message : ''}
                  </p>
                )}
                {event.type === 'failed' && (
                  <p className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                    {typeof event.payload.error_code === 'string' ? event.payload.error_code : ''}
                    {typeof event.payload.message === 'string' ? `：${event.payload.message}` : ''}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
