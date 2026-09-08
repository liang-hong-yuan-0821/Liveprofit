import { Link, useSearchParams } from 'react-router';
import { ClipboardCheck, FlaskConical } from 'lucide-react';
import { Button } from '../../../../shared/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../../../shared/ui/dialog';
import { ActiveTasksPanel } from './ActiveTasksPanel';
import { AnalysisTaskForm } from './AnalysisTaskForm';
import { PendingActionsPanel } from './PendingActionsPanel';
import { RecentConclusionsPanel } from './RecentConclusionsPanel';
import { useDashboardSectionQuery } from './queries';

// AI 投研看板：回答"现在该做什么"。读取专用聚合 DTO，不建立 SSE、不保存运行流、不复制报告正文。
// 创建面板开关由 URL ?create=1 承载（自选页可携带 ticker 预填）；关闭面板清理 create/ticker 参数。
export default function AiDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const createOpen = searchParams.get('create') === '1';

  // 三区块整体空态：所有区块均为空时才引导新建分析（任一失败/加载中不显示）
  const pendingEmpty = useDashboardSectionQuery('pending_actions', (dto) => dto.pending_actions.length === 0);
  const activeEmpty = useDashboardSectionQuery('active_tasks', (dto) => dto.active_tasks.length === 0);
  const conclusionsEmpty = useDashboardSectionQuery(
    'recent_conclusions',
    (dto) => dto.recent_conclusions.length === 0,
  );
  const overallEmpty =
    pendingEmpty.isSuccess && activeEmpty.isSuccess && conclusionsEmpty.isSuccess &&
    pendingEmpty.data && activeEmpty.data && conclusionsEmpty.data;

  function openCreatePanel() {
    setSearchParams({ create: '1' }, { replace: true });
  }

  function closeCreatePanel() {
    // 关闭面板清理 create search params，不影响看板数据
    const next = new URLSearchParams(searchParams);
    next.delete('create');
    setSearchParams(next, { replace: true });
  }

  return (
    <main className="flex flex-col gap-6">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">AI 投研看板</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            此刻该做什么：处理待办、跟踪进行中任务、阅读最近结论或发起新分析
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => openCreatePanel()}>
            新建分析
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/event-study">
              <FlaskConical className="size-4" aria-hidden />
              事件研究
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/ai/event-study/review">
              <ClipboardCheck className="size-4" aria-hidden />
              事件审核
            </Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link to="/ai/tasks">任务中心</Link>
          </Button>
        </div>
      </header>

      {overallEmpty && (
        <div className="rounded-lg border p-6 text-center" style={{ borderColor: 'var(--color-border)' }}>
          <p className="text-sm font-medium">当前没有待处理事项、进行中任务和结论</p>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            发起一次单股分析或全市场扫描，跟踪进度并阅读结构化报告
          </p>
          <div className="mt-3 flex justify-center gap-3">
            <Button size="sm" onClick={() => openCreatePanel()}>
              新建分析
            </Button>
          </div>
        </div>
      )}

      <PendingActionsPanel />
      <ActiveTasksPanel />
      <RecentConclusionsPanel />

      <Dialog open={createOpen} onOpenChange={(open) => !open && closeCreatePanel()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建分析</DialogTitle>
            <DialogDescription>
              配置分析类型、目标与层级；创建成功后跳转任务详情跟踪进度
            </DialogDescription>
          </DialogHeader>
          <AnalysisTaskForm onCancel={closeCreatePanel} />
        </DialogContent>
      </Dialog>
    </main>
  );
}
