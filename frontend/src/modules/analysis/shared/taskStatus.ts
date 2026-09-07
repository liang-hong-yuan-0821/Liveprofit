import type { TaskDTO } from '../../../api/generated';

// analysis 三个工作区复用的稳定任务状态展示（模块内共享，不上升为共享 feature）。
// 各 DTO 的 status 枚举互不相同（TaskDTO/TaskListItemDTO/ActiveTaskDTO/TaskCreatedData），
// 展示层按字符串值处理，未知值原样兜底，不做枚举间强转。
export type TaskStatus = TaskDTO.status;

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  PENDING: '等待确认',
  QUEUED: '排队中',
  RUNNING: '运行中',
  RETRYING: '重试中',
  SUCCEEDED: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
  CANCEL_REQUESTED: '取消请求中',
};

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status as TaskStatus] ?? status;
}

export function taskStatusVariant(status: string): 'default' | 'success' | 'destructive' | 'warning' | 'secondary' {
  switch (status) {
    case 'SUCCEEDED':
      return 'success';
    case 'FAILED':
      return 'destructive';
    case 'RETRYING':
    case 'CANCEL_REQUESTED':
      return 'warning';
    case 'PENDING':
    case 'QUEUED':
    case 'RUNNING':
      return 'default';
    case 'CANCELLED':
      return 'secondary';
    default:
      return 'default';
  }
}

export function taskTargetLabel(taskType: string, ticker?: string | null): string {
  return ticker ? ticker : taskType === 'MARKET_WIDE' ? '全市场' : '—';
}
