import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import AiTasksPage from './AiTasksPage';

vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: { listTasksApiV1AnalysisTasksGet: vi.fn(), createTaskApiV1AnalysisTasksPost: vi.fn(), getTaskApiV1AnalysisTasksTaskIdGet: vi.fn(), cancelTaskApiV1AnalysisTasksTaskIdCancelPost: vi.fn(), deleteTaskApiV1AnalysisTasksTaskIdDelete: vi.fn() },
}));

import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';

const fetchMock = AnalysisTasksService.listTasksApiV1AnalysisTasksGet as Mock;
const deleteTaskMock = AnalysisTasksService.deleteTaskApiV1AnalysisTasksTaskIdDelete as Mock;

interface TaskItem {
  id: string;
  task_type: 'SINGLE_STOCK' | 'MARKET_WIDE';
  ticker: string | null;
  effective_trade_date: string | null;
  status: string;
  attempt_no: number;
  error_code: string | null;
  error_summary: string | null;
  created_at: string;
  updated_at: string;
}

function makeTask(id: string, status: string, updatedAt: string, extra: Record<string, unknown> = {}): TaskItem {
  return {
    id,
    task_type: 'SINGLE_STOCK',
    ticker: '000001.SZ',
    effective_trade_date: '2026-09-03',
    status,
    attempt_no: 1,
    error_code: null,
    error_summary: null,
    created_at: updatedAt,
    updated_at: updatedAt,
    ...extra,
  };
}

function envelope(items: TaskItem[], nextCursor: string | null) {
  return {
    data: { items },
    meta: { request_id: 'r', schema_version: 'v1', next_cursor: nextCursor },
  };
}

// 服务端语义的分页 mock：按 cursor 切片、next_cursor 续页
function mockPaged(pages: { items: TaskItem[]; next_cursor: string | null }[]) {
  fetchMock.mockImplementation(async (cursor?: string) => {
    const index = cursor ? Number(cursor) : 0;
    const page = pages[index];
    return envelope(page.items, page.next_cursor);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  deleteTaskMock.mockReset();
  deleteTaskMock.mockResolvedValue({
    data: { deleted: true, resource_id: 'task-1' },
    meta: { request_id: 'r', schema_version: 'v1' },
  });
  vi.stubGlobal('EventSource', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AiTasksPage 列表与分页', () => {
  it('仅渲染 TaskListItemDTO 允许字段，不渲染敏感额外字段', async () => {
    mockPaged([
      {
        items: [
          makeTask('task-1', 'FAILED', '2026-09-05T09:15:00Z', {
            error_code: 'PROVIDER_UNAVAILABLE',
            error_summary: '行情数据暂不可用',
            // 契约外的敏感字段：列表不得渲染
            request_params: 'SECRET-REQUEST-PARAMS',
            idempotency_key: 'SECRET-KEY',
            lease_token: 'SECRET-LEASE',
          }),
        ],
        next_cursor: null,
      },
    ]);
    renderWithRouter(<AiTasksPage />);

    const row = await screen.findByRole('link', { name: /行情数据暂不可用/ });
    expect(within(row).getByText('失败')).toBeInTheDocument();
    expect(within(row).getByText('第 1 次尝试')).toBeInTheDocument();
    expect(within(row).getByText('000001.SZ')).toBeInTheDocument();

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('SECRET-REQUEST-PARAMS');
    expect(bodyText).not.toContain('SECRET-KEY');
    expect(bodyText).not.toContain('SECRET-LEASE');
  });

  it('加载更多按服务端顺序追加；无 next_cursor 隐藏加载更多', async () => {
    const user = userEvent.setup();
    mockPaged([
      { items: [makeTask('task-1', 'RUNNING', '2026-09-05T09:30:00Z')], next_cursor: '1' },
      { items: [makeTask('task-2', 'SUCCEEDED', '2026-09-05T09:00:00Z')], next_cursor: null },
    ]);
    renderWithRouter(<AiTasksPage />);

    expect(await screen.findByText('第 1 次尝试')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '加载更多' }));

    const successRow = await screen.findByRole('link', { name: /成功/ });
    expect(within(successRow).getByText('成功')).toBeInTheDocument();
    // 两页均在，且第一页在前（updated_at DESC 顺序由服务端保证，前端只追加）
    const rows = screen.getAllByRole('link').filter((el) => el.textContent?.includes('000001.SZ'));
    expect(rows).toHaveLength(2);
    // 无下一页后入口隐藏
    await waitFor(() => expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument());
    // 分页请求携带 cursor=1
    expect(fetchMock.mock.calls[1][0]).toBe('1');
  });

  it('下一页失败保留已加载结果，重试后恢复', async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (cursor?: string) => {
      if (cursor === '1') {
        throw new ApiError({ code: 'INTERNAL_ERROR', message: '服务端暂时不可用', retryable: true, status: 500 });
      }
      return cursor
        ? envelope([makeTask('task-2', 'SUCCEEDED', '2026-09-05T09:00:00Z')], null)
        : envelope([makeTask('task-1', 'RUNNING', '2026-09-05T09:30:00Z')], '1');
    });
    renderWithRouter(<AiTasksPage />);

    expect(await screen.findByText('第 1 次尝试')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '加载更多' }));

    // 下一页失败：已加载结果保留 + 就地重试入口
    expect(await screen.findByText(/下一页加载失败/)).toBeInTheDocument();
    expect(screen.getByText('第 1 次尝试')).toBeInTheDocument();

    fetchMock.mockImplementation(async () => envelope([makeTask('task-2', 'SUCCEEDED', '2026-09-05T09:00:00Z')], null));
    await user.click(screen.getByRole('button', { name: '重试' }));

    const successRow = await screen.findByRole('link', { name: /成功/ });
    expect(within(successRow).getByText('成功')).toBeInTheDocument();
    expect(screen.queryByText(/下一页加载失败/)).not.toBeInTheDocument();
  });
});

describe('AiTasksPage 状态筛选', () => {
  it('URL ?status=active 恢复筛选并以服务端 status 参数请求', async () => {
    mockPaged([{ items: [makeTask('task-1', 'RETRYING', '2026-09-05T09:30:00Z')], next_cursor: null }]);
    renderWithRouter(<AiTasksPage />, { initialEntries: ['/ai/tasks?status=active'] });

    expect(await screen.findByText('重试中')).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][2]).toBe('active');
  });

  it('切换筛选以服务端 status 参数刷新；全部回退 all 并清除 URL status', async () => {
    const user = userEvent.setup();
    mockPaged([{ items: [], next_cursor: null }]);
    renderWithRouter(<AiTasksPage />);

    await user.click(screen.getByRole('button', { name: '失败' }));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[2]).toBe('failed'));
    expect(screen.getByTestId('location').dataset.search).toContain('status=failed');

    await user.click(screen.getByRole('button', { name: '全部' }));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[2]).toBe('all'));
    expect(screen.getByTestId('location').dataset.search).not.toContain('status');
  });

  it('422 INVALID_TASK_FILTER 显示为不可重试筛选错误', async () => {
    fetchMock.mockRejectedValue(
      new ApiError({ code: 'INVALID_TASK_FILTER', message: '筛选值非法', retryable: false, status: 422 }),
    );
    renderWithRouter(<AiTasksPage />);

    expect(await screen.findByText('筛选值非法')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });
});

describe('AiTasksPage 边界', () => {
  it('新建分析入口仅跳转 /ai?create=1，页面无创建表单', async () => {
    mockPaged([{ items: [], next_cursor: null }]);
    renderWithRouter(<AiTasksPage />);

    expect(screen.getByRole('link', { name: '新建分析' })).toHaveAttribute('href', '/ai?create=1');
    expect(screen.queryByLabelText('股票代码')).not.toBeInTheDocument();
  });

  it('终态行可删除：确认后调用 DELETE 并刷新列表；非终态行无删除入口', async () => {
    const user = userEvent.setup();
    mockPaged([
      { items: [makeTask('task-1', 'FAILED', '2026-09-05T09:15:00Z'), makeTask('task-2', 'RUNNING', '2026-09-05T09:30:00Z')], next_cursor: null },
    ]);
    renderWithRouter(<AiTasksPage />);

    // FAILED 行（名称含"失败"徽标，与 RUNNING 行区分）
    const failedRow = await screen.findByRole('link', { name: /失败/ });
    expect(failedRow).toBeTruthy();

    // 两行：FAILED 行有删除，RUNNING 行没有
    const deleteButtons = screen.getAllByRole('button', { name: '删除' });
    expect(deleteButtons).toHaveLength(1);

    const listCallsBefore = fetchMock.mock.calls.length;
    await user.click(deleteButtons[0]);
    const confirmButtons = screen.getAllByRole('button', { name: '删除' });
    await user.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(deleteTaskMock).toHaveBeenCalledWith('task-1'));
    // 删除成功后列表精准失效重新请求
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(listCallsBefore));
  });

  it('任务中心从不创建 EventSource，页面文件无 useTaskEvents/EventSource 引用', async () => {
    mockPaged([{ items: [], next_cursor: null }]);
    renderWithRouter(<AiTasksPage />);
    await screen.findByText('暂无任务');

    expect(EventSource).not.toHaveBeenCalled();

    // 静态断言：任务中心源码不得引用 SSE/useTaskEvents
    for (const file of ['AiTasksPage.tsx', 'AnalysisTaskList.tsx', 'queries.ts']) {
      const source = readFileSync(join(process.cwd(), 'src', 'modules', 'analysis', 'pages', 'tasks', file), 'utf-8');
      expect(source).not.toMatch(/EventSource|useTaskEvents/);
    }
  });
});
