import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import AiDashboardPage from './AiDashboardPage';

vi.mock('../../../../api/generated/services/AnalysisDashboardService', () => ({
  AnalysisDashboardService: { getDashboardApiV1AnalysisDashboardGet: vi.fn() },
}));
vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: { createTaskApiV1AnalysisTasksPost: vi.fn() },
}));

import { AnalysisDashboardService } from '../../../../api/generated/services/AnalysisDashboardService';

const fetchMock = AnalysisDashboardService.getDashboardApiV1AnalysisDashboardGet as Mock;

const fullDashboard = {
  pending_actions: [
    {
      kind: 'FAILED_TASK',
      task_id: 'task-failed',
      task_type: 'SINGLE_STOCK',
      ticker: '000001.SZ',
      effective_trade_date: '2026-09-03',
      updated_at: '2026-09-05T09:15:00Z',
      error_code: 'PROVIDER_UNAVAILABLE',
      error_summary: '行情数据暂不可用',
      unavailable_blocks: null,
      retryable: false,
    },
  ],
  active_tasks: [
    {
      task_id: 'task-active',
      task_type: 'SINGLE_STOCK',
      ticker: '600000.SH',
      effective_trade_date: '2026-09-04',
      status: 'RETRYING',
      attempt_no: 2,
      updated_at: '2026-09-05T09:30:00Z',
      next_retry_at: '2026-09-05T09:32:00Z',
    },
  ],
  recent_conclusions: [
    {
      task_id: 'task-done',
      task_type: 'SINGLE_STOCK',
      ticker: '000858.SZ',
      effective_trade_date: '2026-09-04',
      completed_at: '2026-09-05T09:05:00Z',
      conclusion_summary: '估值偏低，情绪面偏多',
      risk_flag: false,
      risk_hint: null,
      has_report: true,
      updated_at: '2026-09-05T09:05:00Z',
    },
  ],
  generated_at: '2026-09-05T10:00:00Z',
};

const emptyDashboard = {
  pending_actions: [],
  active_tasks: [],
  recent_conclusions: [],
  generated_at: '2026-09-05T10:00:00Z',
};

function envelope(data: unknown) {
  return { data, meta: { request_id: 'req-1', schema_version: 'v1' } };
}

beforeEach(() => {
  fetchMock.mockReset();
  // 三个区块独立 Query：按渲染顺序 pending → active → recent 各请求一次
  fetchMock
    .mockImplementationOnce(async () => envelope(fullDashboard))
    .mockImplementationOnce(async () => {
      throw new ApiError({ code: 'MARKET_DATA_UPSTREAM_UNAVAILABLE', message: '上游不可用', retryable: true, status: 503 });
    })
    .mockImplementationOnce(async () => envelope(fullDashboard))
    .mockImplementation(async () => envelope(fullDashboard));
  // 断言页面从不创建 EventSource（看板不建立 SSE）
  vi.stubGlobal('EventSource', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AiDashboardPage', () => {
  it('三区块独立加载/失败：active_tasks 失败仅重试自身，其余区块正常展示', async () => {
    renderWithRouter(<AiDashboardPage />);

    expect(await screen.findByText('行情数据暂不可用')).toBeInTheDocument();
    expect(screen.getByText('估值偏低，情绪面偏多')).toBeInTheDocument();
    // active_tasks 区块显示独立错误态与重试入口
    expect(screen.getByText('上游不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();

    // 重试仅重新请求 active_tasks 区块（三个独立 Query Key）
    const callsBefore = fetchMock.mock.calls.length;
    await userEvent.setup().click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('第 2 次尝试')).toBeInTheDocument();
    expect(fetchMock.mock.calls.length).toBe(callsBefore + 1);
  });

  it('URL 恢复创建面板；关闭面板清理 create 参数', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AiDashboardPage />, { initialEntries: ['/ai?create=1'] });

    expect(await screen.findByRole('heading', { name: '新建分析' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '关闭' }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: '新建分析' })).not.toBeInTheDocument());

    const location = screen.getByTestId('location');
    expect(location.dataset.search).not.toContain('create');
  });

  it('主 CTA 打开创建面板：恒为全市场调研，默认勾选市场/板块/筛选且可独立取消，无标的输入', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AiDashboardPage />);

    await user.click(screen.getAllByRole('button', { name: '新建分析' })[0]);
    expect(await screen.findByRole('heading', { name: '新建分析' })).toBeInTheDocument();
    for (const name of ['市场', '板块', '筛选']) {
      expect(screen.getByRole('checkbox', { name: new RegExp('^' + name) })).toBeChecked();
    }
    expect(screen.queryByLabelText(/标的/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: /^板块/ }));
    expect(screen.getByRole('checkbox', { name: /^板块/ })).not.toBeChecked();
  });

  it('所有区块均为空时展示整体空态并引导新建分析', async () => {
    const user = userEvent.setup();
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => envelope(emptyDashboard));
    renderWithRouter(<AiDashboardPage />);

    expect(await screen.findByText('当前没有待处理事项、进行中任务和结论')).toBeInTheDocument();
    // 进行中区块即使为空也保留卡片占位
    expect(screen.getByText('无进行中任务')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: '新建分析' })[1]);
    expect(await screen.findByRole('heading', { name: '新建分析' })).toBeInTheDocument();
  });

  it('看板从不创建 EventSource', async () => {
    renderWithRouter(<AiDashboardPage />);
    await screen.findByText('行情数据暂不可用');
    expect(EventSource).not.toHaveBeenCalled();
  });
});


// ---------- 双 tab（任务 | Agent，单Agent重跑与提示词编辑方案 3.5） ----------

vi.mock('../../../../api/generated/services/AgentsService', () => ({
  AgentsService: {
    getAgentsTopologyApiV1AgentsTopologyGet: vi.fn(),
    listAgentPromptsApiV1AgentsPromptsGet: vi.fn(),
    upsertAgentPromptApiV1AgentsPromptsNodeIdPut: vi.fn(),
    resetAgentPromptApiV1AgentsPromptsNodeIdDelete: vi.fn(),
  },
}));
vi.mock('echarts-for-react', () => ({
  default: vi.fn(() => <div data-testid="echarts" />),
}));

const topologyMock = (
  await import('../../../../api/generated/services/AgentsService')
).AgentsService.getAgentsTopologyApiV1AgentsTopologyGet as Mock;
const promptsListMock = (
  await import('../../../../api/generated/services/AgentsService')
).AgentsService.listAgentPromptsApiV1AgentsPromptsGet as Mock;

const ENVELOPE = (data: unknown) => ({ data, meta: { request_id: 'r', schema_version: 'v1' } });

describe('AiDashboardPage 双 tab', () => {
  beforeEach(() => {
    fetchMock.mockResolvedValue(ENVELOPE({
      pending_actions: [], active_tasks: [], recent_conclusions: [], generated_at: 'x',
    }) as never);
    topologyMock.mockResolvedValue(ENVELOPE({
      nodes: [], edges: [], generated_at: 'x',
    }) as never);
    promptsListMock.mockResolvedValue(ENVELOPE({ items: [] }) as never);
  });

  it('默认渲染任务 tab（看板内容）', async () => {
    renderWithRouter(<AiDashboardPage />, { initialEntries: ['/ai'] });
    expect(await screen.findByText(/此刻该做什么/)).toBeInTheDocument();
  });

  it('URL ?tab=agents 直接进入 Agent 拓扑页', async () => {
    renderWithRouter(<AiDashboardPage />, { initialEntries: ['/ai?tab=agents'] });
    expect(await screen.findByText('Agent 架构拓扑')).toBeInTheDocument();
  });

  it('点击 Agent tab 按钮切换到拓扑页，点击任务切回', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AiDashboardPage />, { initialEntries: ['/ai'] });
    await screen.findByText(/此刻该做什么/);
    await user.click(screen.getByRole('button', { name: 'Agent' }));
    expect(await screen.findByText('Agent 架构拓扑')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '任务' }));
    expect(await screen.findByText(/此刻该做什么/)).toBeInTheDocument();
  });
});
