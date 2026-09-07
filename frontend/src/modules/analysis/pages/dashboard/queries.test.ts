import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, type Mock } from 'vitest';
import { queryKeys } from '../../../../api/queryKeys';
import { createTestQueryClient, withQueryClient } from '../../../../test/utils';
import { useCreateAnalysisTaskMutation, useDashboardSectionQuery } from './queries';

vi.mock('../../../../api/generated/services/AnalysisDashboardService', () => ({
  AnalysisDashboardService: { getDashboardApiV1AnalysisDashboardGet: vi.fn() },
}));
vi.mock('../../../../api/generated/services/AnalysisTasksService', () => ({
  AnalysisTasksService: { createTaskApiV1AnalysisTasksPost: vi.fn() },
}));

import { AnalysisDashboardService } from '../../../../api/generated/services/AnalysisDashboardService';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';

const fetchMock = AnalysisDashboardService.getDashboardApiV1AnalysisDashboardGet as Mock;
const createMock = AnalysisTasksService.createTaskApiV1AnalysisTasksPost as Mock;

const fixture = {
  pending_actions: [
    {
      kind: 'FAILED_TASK',
      task_id: 't-1',
      task_type: 'SINGLE_STOCK',
      ticker: '000001.SZ',
      effective_trade_date: null,
      updated_at: '2026-09-05T09:00:00Z',
      error_code: 'X',
      error_summary: 'x',
      unavailable_blocks: null,
      retryable: false,
    },
  ],
  active_tasks: [],
  recent_conclusions: [],
  generated_at: '2026-09-05T10:00:00Z',
};

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ data: fixture, meta: { request_id: 'r', schema_version: 'v1' } });
  createMock.mockReset();
  createMock.mockResolvedValue({
    data: {
      task_id: 't-new',
      status: 'PENDING',
      requested_trade_date: null,
      effective_trade_date: null,
      date_correction: null,
      events_url: '/api/v1/analysis-tasks/t-new/events',
      report_url: '/api/v1/analysis-tasks/t-new/report',
    },
    meta: { request_id: 'r', schema_version: 'v1' },
  });
});

describe('useDashboardSectionQuery', () => {
  it('三个区块使用独立 Query Key（同域 detail(section)）', () => {
    const pending = queryKeys.analysisDashboard.detail('pending_actions');
    const active = queryKeys.analysisDashboard.detail('active_tasks');
    const recent = queryKeys.analysisDashboard.detail('recent_conclusions');

    expect(pending).not.toEqual(active);
    expect(active).not.toEqual(recent);
    expect(pending[0]).toBe('analysis-dashboard');
  });

  it('select 默认投影出对应区块数组', async () => {
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDashboardSectionQuery('pending_actions'), {
      wrapper: withQueryClient(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(
      expect.arrayContaining([expect.objectContaining({ task_id: 't-1' })]),
    );
  });
});

describe('useCreateAnalysisTaskMutation', () => {
  it('创建成功后精准失效看板三区块 Query', async () => {
    const queryClient = createTestQueryClient();

    for (const section of ['pending_actions', 'active_tasks', 'recent_conclusions'] as const) {
      const { result } = renderHook(() => useDashboardSectionQuery(section), {
        wrapper: withQueryClient(queryClient),
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
    }
    const fetchesBefore = fetchMock.mock.calls.length;

    const { result } = renderHook(() => useCreateAnalysisTaskMutation(), {
      wrapper: withQueryClient(queryClient),
    });

    act(() => {
      result.current.mutate({
        request: {
          task_type: 'SINGLE_STOCK',
          ticker: '000001.SZ',
          requested_trade_date: '2026-09-04',
          selected_layers: ['market', 'sector', 'stock'],
        },
        idempotencyKey: 'key-1',
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // 精准失效 = 三个活跃区块 Query 被重新请求（各一次）
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(fetchesBefore + 3));
    // 创建请求经 generated client 携带幂等键
    expect(createMock).toHaveBeenCalledWith(expect.any(Object), 'key-1');
  });
});
