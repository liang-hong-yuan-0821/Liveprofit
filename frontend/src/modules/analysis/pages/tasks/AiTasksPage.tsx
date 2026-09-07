import { Link, useSearchParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { Button } from '../../../../shared/ui/button';
import { AnalysisTaskList } from './AnalysisTaskList';
import { TASK_LIST_LIMIT, type TaskListStatusFilter } from './queries';

// AI 任务中心：完整任务历史的摘要查询页。不承载创建表单、SSE、operation_history、
// 完整请求参数或报告正文；创建职责保持在看板（仅提供跳转 /ai?create=1）。
// status 筛选为 URL 承载（深层链接可直接恢复），由服务端过滤，禁止前端伪全量筛选。
const STATUS_OPTIONS: { value: TaskListStatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
];

function parseStatus(raw: string | null): TaskListStatusFilter {
  return (STATUS_OPTIONS.some((option) => option.value === raw) ? raw : 'all') as TaskListStatusFilter;
}

export default function AiTasksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = parseStatus(searchParams.get('status'));

  function selectStatus(next: TaskListStatusFilter) {
    const params = new URLSearchParams(searchParams);
    if (next === 'all') {
      params.delete('status');
    } else {
      params.set('status', next);
    }
    setSearchParams(params, { replace: true });
  }

  return (
    <main className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">AI 任务中心</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            全部任务摘要，按更新时间倒序；单页最多 {TASK_LIST_LIMIT} 条，游标分页
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to="/ai">
              <ArrowLeft className="size-4" aria-hidden />
              返回看板
            </Link>
          </Button>
          <Button asChild size="sm">
            <Link to="/ai?create=1">新建分析</Link>
          </Button>
        </div>
      </header>

      <nav className="flex gap-2" aria-label="任务状态筛选">
        {STATUS_OPTIONS.map((option) => (
          <Button
            key={option.value}
            size="sm"
            variant={status === option.value ? 'default' : 'outline'}
            onClick={() => selectStatus(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </nav>

      <AnalysisTaskList status={status} />
    </main>
  );
}
