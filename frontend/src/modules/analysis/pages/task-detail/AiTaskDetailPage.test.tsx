import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { FakeEventSource } from '../../../../test/fakeEventSource';
import { renderWithRouter } from '../../../../test/utils';
import AiTaskDetailPage from './AiTaskDetailPage';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: {
    getTaskApiV1AnalysisTasksTaskIdGet: vi.fn(),
    cancelTaskApiV1AnalysisTasksTaskIdCancelPost: vi.fn(),
    deleteTaskApiV1AnalysisTasksTaskIdDelete: vi.fn(),
    getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet: vi.fn(),
    getExecutionLogContentApiV1AnalysisTasksTaskIdExecutionLogsContentGet: vi.fn(),
    getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet: vi.fn(),
    rerunTaskApiV1AnalysisTasksTaskIdRerunPost: vi.fn(),
  },
}));
vi.mock('../../../../api/generated/services/AgentsService', () => ({
  AgentsService: {
    getAgentsTopologyApiV1AgentsTopologyGet: vi.fn(),
    listAgentPromptsApiV1AgentsPromptsGet: vi.fn(),
    upsertAgentPromptApiV1AgentsPromptsNodeIdPut: vi.fn(),
    resetAgentPromptApiV1AgentsPromptsNodeIdDelete: vi.fn(),
  },
}));
vi.mock('../../../../api/generated/services/ReportsService', () => ({
  ReportsService: { getReportApiV1AnalysisTasksTaskIdReportGet: vi.fn() },
}));
// 拓扑面板渲染 echarts（jsdom 无 canvas），页面级测试同面板测试 mock 掉
vi.mock('echarts-for-react', () => ({
  default: vi.fn(() => <div data-testid="echarts" />),
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import { ReportsService } from '../../../../api/generated/services/ReportsService';

const getTaskMock = AnalysisTasksService.getTaskApiV1AnalysisTasksTaskIdGet as Mock;
const cancelMock = AnalysisTasksService.cancelTaskApiV1AnalysisTasksTaskIdCancelPost as Mock;
const deleteMock = AnalysisTasksService.deleteTaskApiV1AnalysisTasksTaskIdDelete as Mock;
const getReportMock = ReportsService.getReportApiV1AnalysisTasksTaskIdReportGet as Mock;
const getLogsMock = AnalysisTasksService.getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet as Mock;
const getTopologyMock = AnalysisTasksService.getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet as Mock;
const rerunMock = AnalysisTasksService.rerunTaskApiV1AnalysisTasksTaskIdRerunPost as Mock;

const TASK_ID = 'task-1';

function makeTask(status: string, overrides: Record<string, unknown> = {}) {
  return {
    id: TASK_ID,
    task_type: 'SINGLE_STOCK',
    ticker: '000001.SZ',
    requested_trade_date: '2026-09-04',
    effective_trade_date: '2026-09-03',
    date_correction: null,
    selected_layers: ['market', 'sector', 'stock'],
    status,
    attempt_no: status === 'RETRYING' ? 2 : 1,
    next_retry_at: status === 'RETRYING' ? '2026-09-05T09:32:00Z' : null,
    error_code: null,
    error_summary: null,
    created_at: '2026-09-05T09:00:00Z',
    updated_at: '2026-09-05T09:15:00Z',
    events_url: `/api/v1/analysis-tasks/${TASK_ID}/events`,
    report_url: `/api/v1/analysis-tasks/${TASK_ID}/report`,
    ...overrides,
  };
}

function makeReport() {
  return {
    schema_version: 'v1',
    report_version: 1,
    generated_at: '2026-09-05T09:05:00Z',
    task: { task_id: TASK_ID, task_type: 'SINGLE_STOCK', ticker: '000001.SZ', effective_trade_date: '2026-09-03', duration_ms: 1200 },
    sections: [
      { block: 'market', status: 'AVAILABLE', title: '市场环境', summary: '市场摘要', content: '市场正文内容', charts: null, unavailable_reason: null, retryable: null },
      { block: 'sector', status: 'UNAVAILABLE', title: '板块分析', summary: null, content: null, charts: null, unavailable_reason: '数据源失败', retryable: true },
      { block: 'stock', status: 'NOT_REQUESTED', title: '个股研究', summary: null, content: null, charts: null, unavailable_reason: null, retryable: null },
      { block: 'decision', status: 'AVAILABLE', title: '交易决策', summary: '决策摘要', content: '决策正文（含 final_position_plan）', charts: null, unavailable_reason: null, retryable: null },
    ],
    data_sources: [{ label: '行情', source: 'tushare', as_of: '2026-09-04' }],
    risk_note: '以上分析仅供参考',
  };
}

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function makeLogs(overrides: Record<string, unknown> = {}) {
  return {
    task_id: TASK_ID,
    attempt_no: 1,
    available: false,
    generated_at: '2026-09-06T10:00:00Z',
    layers: [],
    ...overrides,
  };
}

function makeTopology(overrides: Record<string, unknown> = {}) {
  return {
    task_id: TASK_ID,
    attempt_no: 1,
    available: false,
    generated_at: '2026-09-08T10:00:00Z',
    nodes: [],
    edges: [],
    ...overrides,
  };
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal('EventSource', FakeEventSource);
  getTaskMock.mockReset();
  cancelMock.mockReset();
  deleteMock.mockReset();
  getReportMock.mockReset();
  getLogsMock.mockReset();
  getTopologyMock.mockReset();
  getTaskMock.mockResolvedValue(envelope(makeTask('RUNNING')));
  getReportMock.mockResolvedValue(envelope(makeReport()));
  getLogsMock.mockResolvedValue(envelope(makeLogs()));
  getTopologyMock.mockResolvedValue(envelope(makeTopology()));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage() {
  return renderWithRouter(<AiTaskDetailPage />, { initialEntries: [`/ai/tasks/${TASK_ID}`] });
}

describe('AiTaskDetailPage 加载与状态', () => {
  it('任务 404：只显示资源不存在页，不建立 SSE', async () => {
    getTaskMock.mockRejectedValue(new ApiError({ code: 'TASK_NOT_FOUND', message: '任务不存在', retryable: false, status: 404 }));
    renderPage();

    expect(await screen.findByRole('heading', { name: '资源不存在' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回 AI 投研看板' })).toHaveAttribute('href', '/ai');
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('非终态任务且 events_url canonical：建立唯一 EventSource，展示状态卡与时间线', async () => {
    renderPage();

    expect(await screen.findByText('运行中')).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe(`/api/v1/analysis-tasks/${TASK_ID}/events`);
    expect(screen.getByText('暂无进度事件')).toBeInTheDocument();
    expect(screen.getByText('正式状态以 REST 为准')).toBeInTheDocument();
  });

  it('events_url 非 canonical：不建流，仅 REST 轮询', async () => {
    getTaskMock.mockResolvedValue(envelope(makeTask('RUNNING', { events_url: '/evil/events' })));
    renderPage();

    await screen.findByText('运行中');
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(screen.getByText('REST 回退轮询')).toBeInTheDocument();
  });

  it('终态任务不建立 SSE；SUCCEEDED 同页加载最新报告（三态区块 + 折叠策略）', async () => {
    getTaskMock.mockResolvedValue(envelope(makeTask('SUCCEEDED')));
    renderPage();

    expect(await screen.findByText('最新报告')).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(0);
    // #report 锚点存在（看板"报告不可用/最近结论"链接的定位目标）
    expect(document.getElementById('report')).not.toBeNull();

    // decision 默认展开：正文可见
    expect(screen.getByText('决策正文（含 final_position_plan）')).toBeInTheDocument();
    // market 默认折叠：显示 summary，正文不可见
    expect(screen.getByText('市场摘要')).toBeInTheDocument();
    expect(screen.queryByText('市场正文内容')).not.toBeInTheDocument();
    // UNAVAILABLE：原因 + 重试
    expect(screen.getByText('数据源失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新读取报告' })).toBeInTheDocument();
    // NOT_REQUESTED：说明 + 引导新建
    expect(screen.getByText('本次分析未请求此区块。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '新建包含该层级的分析' })).toHaveAttribute('href', '/ai?create=1');
  });

  it('RETRYING 仅由 REST 呈现：attempt_no 与 next_retry_at 展示，时间线不伪造', async () => {
    getTaskMock.mockResolvedValue(envelope(makeTask('RETRYING')));
    renderPage();

    expect(await screen.findByText('重试中')).toBeInTheDocument();
    expect(screen.getByText('第 2 次')).toBeInTheDocument();
    expect(screen.getByText(/下次重试/)).toBeInTheDocument();
    // RETRYING 无对应 SSE 事件：时间线不伪造失败/重试事件，仍为空
    expect(screen.getByText('暂无进度事件')).toBeInTheDocument();
  });

  it('取消：显示取消请求中，最终以 REST 状态为准，重复取消幂等（按钮消失）', async () => {
    const user = userEvent.setup();
    let resolveCancel!: (value: unknown) => void;
    cancelMock.mockImplementation(() => new Promise((resolve) => { resolveCancel = resolve; }));

    renderPage();
    await screen.findByText('运行中');
    await user.click(screen.getByRole('button', { name: '取消任务' }));

    expect(screen.getByRole('button', { name: '取消请求中…' })).toBeInTheDocument();
    expect(cancelMock).toHaveBeenCalledWith(TASK_ID);

    await act(async () => {
      resolveCancel(envelope(makeTask('CANCELLED')));
      await Promise.resolve();
    });
    expect(await screen.findByText('已取消')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument();
  });

  it('终态任务可删除：确认后调用 DELETE 并返回任务中心', async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue(envelope({ deleted: true, resource_id: TASK_ID }));
    getTaskMock.mockResolvedValue(envelope(makeTask('SUCCEEDED')));
    renderPage();

    await screen.findByText('最新报告');
    await user.click(screen.getByRole('button', { name: '删除任务' }));
    // 弹窗确认（最后一个"删除"= destructive 确认按钮）
    const confirmButtons = screen.getAllByRole('button', { name: '删除' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(TASK_ID));
    // 删除成功后跳转任务中心
    expect(screen.getByTestId('location').dataset.pathname).toBe('/ai/tasks');
  });

  it('非终态任务不显示删除入口（先取消）', async () => {
    renderPage();
    await screen.findByText('运行中');
    expect(screen.queryByRole('button', { name: '删除任务' })).not.toBeInTheDocument();
  });

  it('收到 completed 业务帧：立即失效 Task Query 收口，流关闭', async () => {
    getTaskMock
      .mockResolvedValueOnce(envelope(makeTask('RUNNING')))
      .mockResolvedValueOnce(envelope(makeTask('SUCCEEDED')));
    renderPage();

    await screen.findByText('运行中');
    const es = FakeEventSource.instances[0];

    act(() => {
      es.emit('completed', JSON.stringify({
        task_id: TASK_ID,
        attempt_no: 1,
        occurred_at: '2026-09-05T09:20:00Z',
        schema_version: 'v1',
        report_id: 'r-1',
        duration_ms: 1000,
      }), '9-0');
    });

    expect(await screen.findByText('最新报告')).toBeInTheDocument();
  });
});

describe('AiTaskDetailPage 执行调用日志区块', () => {
  it('运行中挂载执行日志面板（时间线之后、报告区之前）', async () => {
    renderPage();

    await screen.findByText('运行中');
    const panel = await screen.findByTestId('execution-logs-panel');
    expect(screen.getByText('暂无执行日志')).toBeInTheDocument();
    // 面板位于时间线区块之后
    const timelineText = screen.getByText('暂无进度事件');
    expect(panel.compareDocumentPosition(timelineText) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('拓扑面板挂载于时间线之后、执行日志面板之前', async () => {
    renderPage();

    await screen.findByText('运行中');
    const topology = await screen.findByTestId('graph-topology-panel');
    const panel = screen.getByTestId('execution-logs-panel');
    const timelineText = screen.getByText('暂无进度事件');
    // 拓扑在时间线之后、日志面板之前
    expect(topology.compareDocumentPosition(timelineText) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(panel.compareDocumentPosition(topology) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('SUCCEEDED 终态：面板位于报告区之前，静态展示', async () => {
    getTaskMock.mockResolvedValue(envelope(makeTask('SUCCEEDED')));
    renderPage();

    await screen.findByText('最新报告');
    const panel = screen.getByTestId('execution-logs-panel');
    const reportSection = document.getElementById('report');
    expect(reportSection).not.toBeNull();
    expect(panel.compareDocumentPosition(reportSection!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('终态翻转（false→true）瞬间补拉一次执行日志', async () => {
    const { queryClient } = renderPage();
    await screen.findByText('运行中');
    await waitFor(() => expect(getLogsMock).toHaveBeenCalledTimes(1));

    // 任务翻转为终态：REST 轮询返回 SUCCEEDED
    getTaskMock.mockResolvedValue(envelope(makeTask('SUCCEEDED')));
    act(() => {
      void queryClient.invalidateQueries({ queryKey: ['analysis-task', 'detail', TASK_ID] });
    });
    await screen.findByText('最新报告');

    // 翻转瞬间 executionLogs key 被失效 → 主动补拉一次（终态后 refetchInterval 已为 false）
    await waitFor(() => expect(getLogsMock).toHaveBeenCalledTimes(2));
  });

  it('终态翻转（false→true）瞬间同时补拉一次拓扑状态', async () => {
    const { queryClient } = renderPage();
    await screen.findByText('运行中');
    await waitFor(() => expect(getTopologyMock).toHaveBeenCalledTimes(1));

    getTaskMock.mockResolvedValue(envelope(makeTask('SUCCEEDED')));
    act(() => {
      void queryClient.invalidateQueries({ queryKey: ['analysis-task', 'detail', TASK_ID] });
    });
    await screen.findByText('最新报告');

    // 翻转瞬间 graphTopology key 被失效 → 主动补拉一次（防尾部滞留，与 executionLogs 同理由）
    await waitFor(() => expect(getTopologyMock).toHaveBeenCalledTimes(2));
  });
});


// ---------- 单Agent重跑（单Agent重跑与提示词编辑方案 3.6） ----------

const RERUN_DTO = {
  ...makeTask('PENDING'),
  attempt_no: 2,
  rerun_from_node_id: 'market:CN Tech Analyst',
};

describe('单Agent重跑', () => {
  beforeEach(() => {
    getTaskMock.mockResolvedValue(envelope(makeTask('FAILED')));
    getTopologyMock.mockResolvedValue(envelope({
      task_id: TASK_ID, attempt_no: 1, available: true,
      generated_at: '2026-09-09T10:00:00Z',
      nodes: [
        { id: 'market:CN Tech Analyst', label: 'CN Tech Analyst', layer: 'market', row: 0, order: 3, status: 'executed', invocation_count: 1, dirs: [], rerun_available: true },
        { id: 'market:CN News Analyst', label: 'CN News Analyst', layer: 'market', row: 0, order: 2, status: 'executed', invocation_count: 1, dirs: [], rerun_available: false },
      ],
      edges: [],
    }));
    getLogsMock.mockResolvedValue(envelope({
      task_id: TASK_ID, attempt_no: 1, available: true, generated_at: 'x', layers: [],
    }));
  });

  it('终态任务节点弹窗显示「重跑此Agent」，rerun_available=false 禁用并提示', async () => {
    renderPage();
    await screen.findByText('任务状态');

    // 点击 rerun_available=false 的节点（通过拓扑图 mock 注入 click）
    const { default: ReactECharts } = await import('echarts-for-react');
    const mockedChart = ReactECharts as unknown as ReturnType<typeof vi.fn>;
    await waitFor(() => expect(mockedChart.mock.calls.length).toBeGreaterThan(0));
    const onEvents = mockedChart.mock.calls.at(-1)![0].onEvents as {
      click: (p: { dataType: string; data: { id: string } }) => void;
    };
    act(() => onEvents.click({ dataType: 'node', data: { id: 'market:CN News Analyst' } }));
    await screen.findByText(/该节点无检查点/);
    expect(screen.getByRole('button', { name: '重跑此Agent' })).toBeDisabled();

    act(() => onEvents.click({ dataType: 'node', data: { id: 'market:CN Tech Analyst' } }));
    await screen.findByText(/重跑将重新执行该节点及全部下游/);
    expect(screen.getByRole('button', { name: '重跑此Agent' })).toBeEnabled();
  });

  it('确认重跑 → POST rerun → setQueryData 恢复轮询（终态→非终态翻转）', async () => {
    const user = userEvent.setup();
    rerunMock.mockResolvedValue(envelope(RERUN_DTO));
    const { queryClient } = renderPage();
    await screen.findByText('任务状态');

    const { default: ReactECharts } = await import('echarts-for-react');
    const mockedChart = ReactECharts as unknown as ReturnType<typeof vi.fn>;
    await waitFor(() => expect(mockedChart.mock.calls.length).toBeGreaterThan(0));
    const onEvents = mockedChart.mock.calls.at(-1)![0].onEvents as {
      click: (p: { dataType: string; data: { id: string } }) => void;
    };
    act(() => onEvents.click({ dataType: 'node', data: { id: 'market:CN Tech Analyst' } }));
    await screen.findByText(/重跑将重新执行该节点及全部下游/);
    await user.click(screen.getByRole('button', { name: '重跑此Agent' }));

    await screen.findByText('开始重跑');
    await user.click(screen.getByRole('button', { name: '开始重跑' }));

    await waitFor(() => {
      expect(rerunMock).toHaveBeenCalledWith(TASK_ID, { node_id: 'market:CN Tech Analyst' });
    });
    // setQueryData 后 status=PENDING：缓存中任务为非终态
    const cached = queryClient.getQueryData(['analysis-task', 'detail', TASK_ID]) as { status: string };
    expect(cached.status).toBe('PENDING');
    // 状态卡展示"本次从节点续跑"行
    await screen.findByText(/从节点 market:CN Tech Analyst 续跑/);
  });

  it('重跑后轮询恢复：Task Query 的 refetchInterval 按状态回调（PENDING→5000，终态→false）', async () => {
    rerunMock.mockResolvedValue(envelope(RERUN_DTO));
    const { queryClient } = renderPage();
    await screen.findByText('任务状态');

    const observer = queryClient
      .getQueryCache()
      .find({ queryKey: ['analysis-task', 'detail', TASK_ID] })
      ?.observers[0];
    const interval = observer?.options.refetchInterval;
    // refetchInterval 为回调函数（queries.ts）：非终态 5000 / 终态 false
    expect(typeof interval).toBe('function');
    const fn = interval as (
      q: { state: { data?: { status?: string } | null } | null }
    ) => number | false;
    // setQueryData(PENDING) 后：真实 Query 对象（react-query 回调签名）即非终态 → 5000
    const query = queryClient
      .getQueryCache()
      .find({ queryKey: ['analysis-task', 'detail', TASK_ID] })!;
    act(() => {
      queryClient.setQueryData(['analysis-task', 'detail', TASK_ID], RERUN_DTO);
    });
    expect(fn(query as unknown as Parameters<typeof fn>[0])).toBe(5000);
    // 终态数据则返回 false（轮询停止）
    act(() => {
      queryClient.setQueryData(['analysis-task', 'detail', TASK_ID], makeTask('SUCCEEDED'));
    });
    expect(fn(query as unknown as Parameters<typeof fn>[0])).toBe(false);
  });
});
