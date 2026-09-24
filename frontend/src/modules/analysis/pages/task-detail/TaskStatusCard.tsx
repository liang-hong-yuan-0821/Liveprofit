import type { TaskDTO } from '../../../../api/generated';
import { Badge } from '../../../../shared/ui/badge';
import { Button } from '../../../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../../../shared/ui/card';
import { formatDateTime } from '../../../../shared/format/dateTime';
import { taskStatusLabel, taskStatusVariant } from '../../shared/taskStatus';

// 任务状态卡：仅展示 TaskDTO 允许字段；RETRYING 只能来自 REST；
// 不展示幂等键、租约、内部路径或完整敏感请求。
interface TaskStatusCardProps {
  task: TaskDTO;
  cancelPending: boolean;
  onCancel: () => void;
}

function canCancel(task: TaskDTO): boolean {
  return !['SUCCEEDED', 'FAILED', 'CANCELLED', 'CANCEL_REQUESTED'].includes(task.status);
}

export function TaskStatusCard({ task, cancelPending, onCancel }: TaskStatusCardProps) {
  const terminal = ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(task.status);
  return (
    <Card>
      <CardHeader>
        <CardTitle>任务状态</CardTitle>
        <Badge variant={taskStatusVariant(task.status)}>{taskStatusLabel(task.status)}</Badge>
      </CardHeader>
      <CardContent>
        {task.status === 'FAILED' && <p role="alert" className="text-sm text-red-400">{task.error_summary ?? task.error_code ?? '任务失败'}</p>}
        <details open={!terminal || undefined}><summary className="mb-3 text-xs text-[var(--color-fg-muted)]">任务信息 · #{task.id.slice(0, 8)} · {task.effective_trade_date ?? '交易日待定'}</summary>
        <dl className="grid grid-cols-[6rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs [&_dd]:min-w-0 [&_dd]:break-all sm:grid-cols-[8rem_minmax(0,1fr)]">
          <dt style={{ color: 'var(--color-fg-muted)' }}>任务 ID</dt>
          <dd>{task.id}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>类型</dt>
          <dd>{task.task_type === 'SINGLE_STOCK' ? '单股分析' : '全市场扫描'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>目标</dt>
          <dd>{task.ticker ?? '全市场'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>请求交易日</dt>
          <dd>{task.requested_trade_date ?? '—'}</dd>
          <dt style={{ color: 'var(--color-fg-muted)' }}>有效交易日</dt>
          <dd>{task.effective_trade_date ?? '—'}</dd>
          {task.date_correction && (
            <>
              <dt style={{ color: 'var(--color-fg-muted)' }}>日期校正</dt>
              <dd>{task.date_correction}</dd>
            </>
          )}
          <dt style={{ color: 'var(--color-fg-muted)' }}>尝试次数</dt>
          <dd>第 {task.attempt_no} 次</dd>
          {task.rerun_from_node_id && (
            <>
              <dt style={{ color: 'var(--color-fg-muted)' }}>本次重跑</dt>
              <dd>
                从节点 {task.rerun_from_node_id} 续跑（复用第 {task.attempt_no - 1} 次结果）
              </dd>
            </>
          )}
          {task.status === 'RETRYING' && task.next_retry_at && (
            <>
              <dt style={{ color: 'var(--color-fg-muted)' }}>下次重试</dt>
              <dd>{formatDateTime(task.next_retry_at)}</dd>
            </>
          )}

        </dl></details>

        {canCancel(task) && (
          <div className="mt-4">
            <Button
              variant="outline"
              size="sm"
              disabled={cancelPending}
              onClick={onCancel}
            >
              {cancelPending ? '取消请求中…' : '取消任务'}
            </Button>
          </div>
        )}
        {task.status === 'CANCEL_REQUESTED' && (
          <p className="mt-4 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            取消请求已提交，等待 Worker 收口；不能承诺立即停止在途外部调用。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
