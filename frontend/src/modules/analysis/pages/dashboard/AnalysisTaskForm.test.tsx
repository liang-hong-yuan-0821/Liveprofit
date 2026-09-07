import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import { AnalysisTaskForm } from './AnalysisTaskForm';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: { createTaskApiV1AnalysisTasksPost: vi.fn() },
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';

const createMock = AnalysisTasksService.createTaskApiV1AnalysisTasksPost as Mock;

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

  it('仓位可选（随筛选，默认不勾选；取消筛选时联动取消并禁用）', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    const position = screen.getByRole('checkbox', { name: /^仓位/ });
    expect(position).not.toBeChecked();
    expect(position).toBeEnabled();
    await user.click(position);
    expect(position).toBeChecked();

    // 取消筛选：仓位联动取消并禁用
    await user.click(screen.getByRole('checkbox', { name: /^筛选/ }));
    expect(position).not.toBeChecked();
    expect(position).toBeDisabled();
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

  it('勾选仓位后提交：层级含 position', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalysisTaskForm onCancel={() => {}} />);

    await user.click(screen.getByRole('checkbox', { name: /^仓位/ }));
    await user.click(screen.getByRole('button', { name: '提交分析' }));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(createMock.mock.calls[0][0].selected_layers).toEqual(['market', 'sector', 'screening', 'position']);
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

    // 修改层级（勾选仓位）→ 新键
    await user.click(screen.getByRole('checkbox', { name: /^仓位/ }));
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
