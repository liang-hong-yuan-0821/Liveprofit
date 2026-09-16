import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import { AnalysisTaskForm } from './AnalysisTaskForm';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: { createTaskApiV1AnalysisTasksPost: vi.fn() },
}));
vi.mock('../../../../api/generated/services/QuantStrategiesService', () => ({
  QuantStrategiesService: { listStrategiesApiV1QuantStrategiesGet: vi.fn() },
}));
vi.mock('../../../../api/generated/services/PortfoliosService', () => ({
  PortfoliosService: { listPortfoliosApiV1PortfoliosGet: vi.fn() },
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import { QuantStrategiesService } from '../../../../api/generated/services/QuantStrategiesService';
import { PortfoliosService } from '../../../../api/generated/services/PortfoliosService';

const createMock = AnalysisTasksService.createTaskApiV1AnalysisTasksPost as Mock;
const listStrategiesMock = QuantStrategiesService.listStrategiesApiV1QuantStrategiesGet as Mock;
const listPortfoliosMock = PortfoliosService.listPortfoliosApiV1PortfoliosGet as Mock;

function quantEnvelope() {
  return {
    data: {
      items: [
        {
          id: 'strategy-1',
          name: '双均线',
          description: null,
          version: 1,
          created_at: '2026-09-16T00:00:00Z',
          updated_at: '2026-09-16T00:00:00Z',
          versions: [
            {
              id: 'version-1',
              strategy_id: 'strategy-1',
              version_no: 1,
              status: 'PUBLISHED',
              source_hash: 'a'.repeat(64),
              published_at: '2026-09-16T00:00:00Z',
              archived_at: null,
              version: 2,
              created_at: '2026-09-16T00:00:00Z',
              updated_at: '2026-09-16T00:00:00Z',
            },
          ],
        },
      ],
    },
    meta: { request_id: 'req-1', schema_version: 'v1' },
  };
}

function portfoliosEnvelope() {
  return {
    data: {
      items: [
        {
          id: 'portfolio-1',
          name: '核心仓',
          version: 1,
          position_count: 0,
          total_assets: 100000,
          available_cash: 35000,
          risk_per_trade_pct: 0.01,
          min_risk_reward_ratio: 2,
          max_total_position_pct: 0.8,
          max_single_stock_pct: 0.1,
          max_sector_pct: 0.3,
          created_at: '2026-09-16T00:00:00Z',
          updated_at: '2026-09-16T00:00:00Z',
        },
      ],
    },
    meta: { request_id: 'req-1', schema_version: 'v1', next_cursor: null },
  };
}

function successEnvelope(taskId: string) {
  return {
    data: {
      task_id: taskId,
      status: 'PENDING',
      requested_trade_date: '2026-09-04',
      effective_trade_date: null,
      date_correction: null,
      events_url: `/api/v1/analysis-tasks/${taskId}/events`,
      report_url: `/api/v1/analysis-tasks/${taskId}/report`,
    },
    meta: { request_id: 'req-1', schema_version: 'v1' },
  };
}

beforeEach(() => {
  createMock.mockReset();
  createMock.mockResolvedValue(successEnvelope('task-new'));
  listStrategiesMock.mockReset();
  listStrategiesMock.mockResolvedValue(quantEnvelope());
  listPortfoliosMock.mockReset();
  listPortfoliosMock.mockResolvedValue(portfoliosEnvelope());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('AnalysisTaskForm（恒为全市场调研）', () => {
  it('无目标标的输入框；市场/板块/筛选默认勾选且可独立取消', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    expect(screen.queryByLabelText(/标的/)).not.toBeInTheDocument();
    for (const name of ['市场', '板块', '筛选']) {
      const checkbox = screen.getByRole('checkbox', { name: new RegExp('^' + name) });
      expect(checkbox).toBeChecked();
      expect(checkbox).toBeEnabled();
    }
    await user.click(screen.getByRole('checkbox', { name: /^板块/ }));
    expect(screen.getByRole('checkbox', { name: /^板块/ })).not.toBeChecked();
  });

  it('层级自由组合：取消板块后提交，请求体只含所选层级', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('checkbox', { name: /^板块/ }));
    await user.click(screen.getByRole('button', { name: '提交分析' }));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const [body] = createMock.mock.calls[0];
    expect(body.task_type).toBe('MARKET_WIDE');
    expect(body.ticker).toBeNull();
    expect(body.selected_layers).toEqual(['market', 'screening']);
  });

  it('至少选择一个层级：全部取消后提交被阻止', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('checkbox', { name: /^市场/ }));
    await user.click(screen.getByRole('checkbox', { name: /^板块/ }));
    await user.click(screen.getByRole('checkbox', { name: /^筛选/ }));
    await user.click(screen.getByRole('button', { name: '提交分析' }));

    expect(await screen.findByText('请至少选择一个分析层级')).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('仓位独立可选（不随筛选）；勾选后出现策略/组合选择器', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    const position = screen.getByRole('checkbox', { name: /^仓位/ });
    expect(position).not.toBeChecked();
    expect(position).toBeEnabled();
    await user.click(position);
    expect(position).toBeChecked();

    // 决策 11：position 独立成任务——取消筛选不联动取消仓位
    await user.click(screen.getByRole('checkbox', { name: /^筛选/ }));
    expect(position).toBeChecked();

    // 选择器出现：已发布策略版本 + 组合
    expect(await screen.findByLabelText('已发布策略版本')).toBeInTheDocument();
    expect(screen.getByLabelText('组合（任务冻结提交时快照，行情执行时实时读取）')).toBeInTheDocument();

    // 取消仓位：清空量化参数（避免「落库未生效」）
    await user.click(position);
    expect(position).not.toBeChecked();
    expect(screen.queryByLabelText('已发布策略版本')).not.toBeInTheDocument();
  });

  it('提交恒为 MARKET_WIDE：ticker 为 null，层级为市场/板块/筛选', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('button', { name: '提交分析' }));

    expect(await screen.findByTestId('task-detail-sentinel')).toHaveTextContent('task-detail:task-new');
    const [body, idempotencyKey] = createMock.mock.calls[0];
    expect(body.task_type).toBe('MARKET_WIDE');
    expect(body.ticker).toBeNull();
    expect(body.selected_layers).toEqual(['market', 'sector', 'screening']);
    expect(idempotencyKey).toBeTruthy();
  });

  it('勾选仓位并选择策略/组合后提交：层级含 position 且携带量化三字段', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('checkbox', { name: /^仓位/ }));
    await user.selectOptions(await screen.findByLabelText('已发布策略版本'), 'version-1');
    await user.selectOptions(screen.getByLabelText('组合（任务冻结提交时快照，行情执行时实时读取）'), 'portfolio-1');
    await user.click(screen.getByRole('button', { name: '提交分析' }));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const body = createMock.mock.calls[0][0];
    expect(body.selected_layers).toEqual(['market', 'sector', 'screening', 'position']);
    expect(body.strategy_version_id).toBe('version-1');
    expect(body.portfolio_id).toBe('portfolio-1');
    expect(body.expected_portfolio_version).toBe(1);
  });

  it('未选策略/组合时提交被前端校验阻止', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('checkbox', { name: /^仓位/ }));
    await screen.findByLabelText('已发布策略版本');
    await user.click(screen.getByRole('button', { name: '提交分析' }));

    expect(await screen.findByText('请选择已发布策略版本')).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('同一意图重试复用幂等键；改变层级（仓位）后生成新键', async () => {
    const user = userEvent.setup();
    createMock.mockRejectedValue(
      new ApiError({ code: 'MARKET_DATA_UPSTREAM_UNAVAILABLE', message: '行情上游暂不可用', retryable: true, status: 503 }),
    );
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('button', { name: '提交分析' }));
    expect(await screen.findByText('行情上游暂不可用')).toBeInTheDocument();
    const firstKey = createMock.mock.calls[0][1];

    // 输入未修改 → 手动重试复用同一键
    await user.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    expect(createMock.mock.calls[1][1]).toBe(firstKey);

    // 修改层级（勾选仓位并选齐量化参数）→ 新键
    await user.click(screen.getByRole('checkbox', { name: /^仓位/ }));
    await user.selectOptions(await screen.findByLabelText('已发布策略版本'), 'version-1');
    await user.selectOptions(screen.getByLabelText('组合（任务冻结提交时快照，行情执行时实时读取）'), 'portfolio-1');
    await user.click(screen.getByRole('button', { name: '提交分析' }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(3));
    expect(createMock.mock.calls[2][1]).not.toBe(firstKey);
  });

  it('不可重试错误不显示重试按钮', async () => {
    const user = userEvent.setup();
    createMock.mockRejectedValue(
      new ApiError({ code: 'TASK_CREATE_INVALID', message: '参数不合法', retryable: false, status: 422 }),
    );
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('button', { name: '提交分析' }));
    expect(await screen.findByText('参数不合法')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });
});
