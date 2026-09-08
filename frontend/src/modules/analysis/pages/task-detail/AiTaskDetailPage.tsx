import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { queryKeys } from '../../../../api/queryKeys';
import { toApiError } from '../../../../api/client';
import { ErrorState } from '../../../../shared/feedback/ErrorState';
import { LoadingState } from '../../../../shared/feedback/LoadingState';
import { NotFoundState } from '../../../../shared/feedback/NotFoundState';
import { Button } from '../../../../shared/ui/button';
import { Badge } from '../../../../shared/ui/badge';
import { ConfirmDialog } from '../../../../shared/ui/ConfirmDialog';
import { TaskStatusCard } from './TaskStatusCard';
import { TaskProgressTimeline } from './TaskProgressTimeline';
import { GraphTopologyPanel } from './GraphTopologyPanel';
import { ExecutionLogsPanel } from './ExecutionLogsPanel';
import { ReportContent } from './ReportContent';
import { toReportViewModel } from './reportMappers/toReportViewModels';
import { canonicalEventsUrl, useTaskEvents } from './useTaskEvents';
import { isTerminalStatus, useCancelTaskMutation, useDeleteTaskMutation, useReportQuery, useTaskQuery } from './queries';

// 任务详情：运行中任务的唯一 canonical URL，成功任务阅读最新结构化报告的唯一页面。
// 严格顺序：GET Task → 404 只显示资源不存在页；非终态且 events_url 满足 canonical 规则
// 才启动唯一 useTaskEvents（否则仅 5s REST 轮询）；SUCCEEDED 后在同页启用 Report Query；
// 卸载或 taskId 改变时 Hook 内部关闭 EventSource、清理重连与计时器。
export default function AiTaskDetailPage() {
  const { taskId = '' } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const reportSectionRef = useRef<HTMLElement | null>(null);

  const taskQuery = useTaskQuery(taskId);
  const task = taskQuery.data;
  const terminal = task ? isTerminalStatus(task.status) : false;

  const eventsEnabled = Boolean(task && !terminal && task.events_url === canonicalEventsUrl(taskId));
  const { events, connectionState } = useTaskEvents({
    taskId,
    eventsUrl: task?.events_url ?? '',
    enabled: eventsEnabled,
  });

  const reportQuery = useReportQuery(taskId, task?.status === 'SUCCEEDED');
  const cancelMutation = useCancelTaskMutation(taskId);
  const deleteMutation = useDeleteTaskMutation(taskId);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const navigate = useNavigate();

  // 收到 completed/failed/cancelled 业务帧 → 立即失效 Task Query（REST 终态收口并关闭流）
  useEffect(() => {
    const last = events[events.length - 1];
    if (last && ['completed', 'failed', 'cancelled'].includes(last.type)) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTask.detail(taskId) });
    }
  }, [events, taskId, queryClient]);

  // 终态收口（任一来源：SSE 终态帧、REST 轮询、取消结果）：
  // 精准失效任务详情与看板/任务列表域，避免 30s staleTime 内返回列表仍显示旧状态
  useEffect(() => {
    if (task && isTerminalStatus(task.status)) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisDashboard.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTasks.all });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.status, taskId, queryClient]);

  // 终态翻转补拉（防尾部丢失）：最后一次日志轮询可能早于内核末次落盘（res.md/meta 后写），
  // terminal false→true 翻转的瞬间失效 executionLogs key，触发最后一次主动 refetch（之后缓存冻结）。
  // 页面初次挂载即为终态时不补拉（初始 fetch 已是最终态，无翻转发生）。
  const prevTerminalRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevTerminalRef.current === null) {
      prevTerminalRef.current = terminal;
      return;
    }
    const wasTerminal = prevTerminalRef.current;
    prevTerminalRef.current = terminal;
    if (terminal && !wasTerminal) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTask.executionLogs(taskId) });
      // 拓扑状态与日志同节奏冻结，同步补拉防尾部滞留（FAILED 末节点 error 标记后写）
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTask.graphTopology(taskId) });
    }
  }, [terminal, taskId, queryClient]);

  // #report 锚点定位：报告区实际挂载（Report Query 成功）后再滚动
  useEffect(() => {
    if (location.hash === '#report' && reportQuery.isSuccess && reportSectionRef.current) {
      reportSectionRef.current.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }
  }, [location.hash, reportQuery.isSuccess]);

  if (taskQuery.isPending) {
    return <LoadingState label="任务加载中…" />;
  }

  if (taskQuery.isError) {
    const error = toApiError(taskQuery.error);
    if (error.status === 404 || error.code === 'TASK_NOT_FOUND') {
      // 404 只展示资源不存在页：不建立任何实时连接、无限重试
      return (
        <main className="flex min-h-[60vh] items-center justify-center">
          <NotFoundState to="/ai" label="返回 AI 投研看板" />
        </main>
      );
    }
    return <ErrorState error={error} onRetry={error.retryable ? () => void taskQuery.refetch() : undefined} />;
  }

  if (!task) {
    return <ErrorState error={new Error('任务数据缺失')} />;
  }

  return (
    <main className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">任务详情</h1>
          <p className="mt-1 text-sm" style={{ color: 'var(--color-fg-muted)' }}>
            {task.ticker ?? '全市场'} · 有效交易日 {task.effective_trade_date ?? '—'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link to="/ai">
              <ArrowLeft className="size-4" aria-hidden />
              返回看板
            </Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link to="/ai/tasks">任务中心</Link>
          </Button>
          {terminal && (
            <Button variant="destructive" size="sm" onClick={() => setDeleteConfirmOpen(true)}>
              删除任务
            </Button>
          )}
        </div>
      </header>

      <TaskStatusCard task={task} cancelPending={cancelMutation.isPending} onCancel={() => cancelMutation.mutate()} />

      {!terminal && (
        <>
          <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-fg-muted)' }}>
            <Badge variant={connectionState === 'connected' ? 'success' : connectionState === 'reconnecting' ? 'warning' : 'secondary'}>
              {connectionState === 'connected' ? '实时已连接' : connectionState === 'reconnecting' ? '重连中' : 'REST 回退轮询'}
            </Badge>
            <span>正式状态以 REST 为准</span>
          </div>
          <TaskProgressTimeline events={events} />
        </>
      )}

      <GraphTopologyPanel taskId={taskId} terminal={terminal} taskFailed={task.status === 'FAILED'} />

      <ExecutionLogsPanel taskId={taskId} terminal={terminal} />

      {task.status === 'SUCCEEDED' && (
        <section id="report" ref={reportSectionRef}>
          {reportQuery.isPending ? (
            <LoadingState label="报告加载中…" />
          ) : reportQuery.isError ? (
            <ErrorState
              error={toApiError(reportQuery.error)}
              onRetry={toApiError(reportQuery.error).retryable ? () => void reportQuery.refetch() : undefined}
            />
          ) : reportQuery.data ? (
            <ReportContent report={toReportViewModel(reportQuery.data)} onRetryReport={() => void reportQuery.refetch()} />
          ) : null}
        </section>
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        title="删除任务记录"
        description="将删除该任务及其报告（任务表/报告表同时删除）"
        pending={deleteMutation.isPending}
        onConfirm={() =>
          deleteMutation.mutate(undefined, {
            onSuccess: () => navigate('/ai/tasks'),
          })
        }
        onCancel={() => setDeleteConfirmOpen(false)}
      />

      {task.status === 'FAILED' && (
        <p className="text-sm" style={{ color: 'var(--color-fg-muted)' }}>
          任务失败：可返回看板重新发起分析（新任务，不重试原任务）。
          <Button asChild variant="outline" size="sm" className="ml-2">
            <Link to="/ai?create=1">重新发起分析</Link>
          </Button>
        </p>
      )}
    </main>
  );
}
