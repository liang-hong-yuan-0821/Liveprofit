import { Link, useSearchParams } from 'react-router';
import { Button } from '../../../../shared/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../../../shared/ui/dialog';
import { ActiveTasksPanel } from './ActiveTasksPanel';
import { AnalysisTaskForm } from './AnalysisTaskForm';
import { PendingActionsPanel } from './PendingActionsPanel';
import { RecentConclusionsPanel } from './RecentConclusionsPanel';
import { useDashboardSectionQuery } from './queries';
import { AgentTopologyPage } from '../agents/AgentTopologyPage';

// AI hub：顶部 tab 条「任务 | Agent」（?tab= URL 模式，先例 EventStudyPage）。
// 任务 tab = 现有看板内容（默认）；Agent tab = 静态全局拓扑 + 提示词编辑。
// 创建面板开关由 URL ?create=1 承载（自选页可携带 ticker 预填）；切换 tab 保留 create 参数。
// 注意：tab 分支为单一 return 内条件渲染，不得提前 return（hooks 顺序稳定性）。
type AiTab = 'tasks' | 'agents';

export default function AiDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: AiTab = searchParams.get('tab') === 'agents' ? 'agents' : 'tasks';
  const createOpen = searchParams.get('create') === '1';

  function switchTab(next: AiTab) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === 'agents') {
          params.set('tab', 'agents');
        } else {
          params.delete('tab');
        }
        return params;
      },
      { replace: true },
    );
  }

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

  const header = (
    <header className="flex items-center justify-between gap-4">
      <div>
        <h1 className="text-lg font-semibold">AI 工作台</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          {tab === 'agents'
            ? '管理 Agent 提示词与发起分析任务'
            : '此刻该做什么：处理待办、跟踪进行中任务、阅读最近结论或发起新分析'}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant={tab === 'tasks' ? 'default' : 'outline'}
          onClick={() => switchTab('tasks')}
        >
          任务
        </Button>
        <Button
          size="sm"
          variant={tab === 'agents' ? 'default' : 'outline'}
          onClick={() => switchTab('agents')}
        >
          Agent
        </Button>
        {tab === 'tasks' && (
          <>
            <Button size="sm" onClick={() => openCreatePanel()}>
              新建分析
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link to="/ai/tasks">任务中心</Link>
            </Button>
          </>
        )}
      </div>
    </header>
  );

  return (
    <main className="flex flex-col gap-6">
      {header}

      {tab === 'agents' ? (
        <AgentTopologyPage />
      ) : (
        <>
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

          {/* 三区块水平排列（窄屏退化为单列堆叠） */}
          <div className="grid items-start gap-6 lg:grid-cols-3">
            <PendingActionsPanel />
            <ActiveTasksPanel />
            <RecentConclusionsPanel />
          </div>

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
        </>
      )}
    </main>
  );
}
