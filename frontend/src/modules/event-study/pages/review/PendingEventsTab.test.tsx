import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import { PendingEventsTab } from './PendingEventsTab';

vi.mock('../../../../api/generated/services/EventStudiesService', () => ({
  EventStudiesService: {
    listPendingEventsApiV1EventStudiesReviewPendingEventsGet: vi.fn(),
    prelabelApiV1EventStudiesReviewPrelabelPost: vi.fn(),
    submitBatchApiV1EventStudiesReviewBatchPost: vi.fn(),
    computeApiV1EventStudiesReviewEventsEventIdComputePost: vi.fn(),
    listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet: vi.fn(),
    confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost: vi.fn(),
  },
}));

import { EventStudiesService } from '../../../../api/generated/services/EventStudiesService';

const pendingMock = EventStudiesService.listPendingEventsApiV1EventStudiesReviewPendingEventsGet as Mock;
const prelabelMock = EventStudiesService.prelabelApiV1EventStudiesReviewPrelabelPost as Mock;
const batchMock = EventStudiesService.submitBatchApiV1EventStudiesReviewBatchPost as Mock;
const computeMock = EventStudiesService.computeApiV1EventStudiesReviewEventsEventIdComputePost as Mock;
const impactMock = EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function draftItem(draftId: number, title = `事件 ${draftId}`) {
  return {
    draft_id: draftId,
    title,
    announced_at: `2026-08-0${draftId}T09:00:00+08:00`,
    source: '金十数据',
    content: '正文',
    source_url: null,
    importance_hint: 4,
    ai_suggestions: {
      event_type: '宏观数据',
      event_subtype: 'CPI',
      event_condition: '超预期',
      importance: 5,
      expected_value: 1.9,
      actual_value: 2.1,
      previous_value: 1.5,
    },
  };
}

function setupPending(items: unknown[], options: { items?: unknown[] } = {}) {
  pendingMock.mockResolvedValue(envelope({ items: options.items ?? items }));
  impactMock.mockResolvedValue(envelope({ items: [] }));
  renderWithRouter(<PendingEventsTab />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('PendingEventsTab', () => {
  it('行渲染：ai_suggestions 默认值 + 默认动作跳过', async () => {
    setupPending([draftItem(1)]);
    await screen.findByLabelText('第1行-事件类型');
    expect(screen.getByLabelText('第1行-事件类型')).toHaveValue('宏观数据');
    expect(screen.getByLabelText('第1行-事件子类型')).toHaveValue('CPI');
    expect(screen.getByLabelText('第1行-重要性')).toHaveValue('5');
    expect(screen.getByLabelText('第1行-预期值')).toHaveValue(1.9);
    expect(screen.getByLabelText('第1行-操作')).toHaveValue('skip');
    expect(screen.getByText('事件 1')).toBeInTheDocument();
  });

  it('空态：无草稿时不渲染表格与预填按钮可用性', async () => {
    setupPending([], { items: [] });
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.queryByLabelText('第1行-操作')).not.toBeInTheDocument();
  });

  it('503 REVIEW_UPSTREAM_UNAVAILABLE：错误横幅，不渲染空态', async () => {
    pendingMock.mockRejectedValue(
      new ApiError({ code: 'REVIEW_UPSTREAM_UNAVAILABLE', message: '审核草稿区（Redis）不可用，请稍后重试', retryable: true, status: 503 }),
    );
    impactMock.mockResolvedValue(envelope({ items: [] }));
    renderWithRouter(<PendingEventsTab />);
    expect(await screen.findByText('审核草稿区（Redis）不可用，请稍后重试')).toBeInTheDocument();
    expect(screen.queryByText('暂无待审核事件')).not.toBeInTheDocument();
  });

  it('二次确认：取消不提交；确认后提交且跳过行不进 payload', async () => {
    const user = userEvent.setup();
    setupPending([draftItem(1), draftItem(2)]);
    await screen.findByLabelText('第1行-操作');

    await user.click(screen.getByRole('button', { name: '🚀 批量提交' }));
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(batchMock).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText('第1行-操作'), 'approve');
    batchMock.mockResolvedValue(
      envelope({
        results: [{ draft_id: 1, ok: true, event_id: 10, error_code: null, error_message: null, compute_status: 'ok' }],
        summary: { approved: 1, ignored: 0, computed: 1 },
      }),
    );
    await user.click(screen.getByRole('button', { name: '🚀 批量提交' }));
    await user.click(screen.getByRole('button', { name: '提交' }));
    await screen.findByText('已处理 1/1 行');
    expect(batchMock).toHaveBeenCalledTimes(1);
    const [payload] = batchMock.mock.calls[0];
    expect(payload.items).toHaveLength(1);
    expect(payload.items[0]).toMatchObject({ draft_id: 1, action: 'approve', operator: 'admin' });
    expect(await screen.findByText('已通过')).toBeInTheDocument();
  });

  it('分块：12 行非跳过 → 两次调用 10+2，进度文案出现', async () => {
    const user = userEvent.setup();
    setupPending(Array.from({ length: 12 }, (_, i) => draftItem(i + 1)));
    const op = await screen.findByLabelText('第1行-操作');
    expect(op).toBeInTheDocument();
    for (let i = 1; i <= 12; i += 1) {
      fireEvent.change(screen.getByLabelText(`第${i}行-操作`), { target: { value: 'approve' } });
    }
    // 按块返回结果（mock 返回的 results 须与 payload 行数一致，否则汇总文案错位）
    batchMock.mockImplementation((payload: { items: { draft_id: number }[] }) =>
      Promise.resolve(
        envelope({
          results: payload.items.map((it) => ({
            draft_id: it.draft_id, ok: true, event_id: 100 + it.draft_id, error_code: null,
            error_message: null, compute_status: 'ok',
          })),
          summary: { approved: payload.items.length, ignored: 0, computed: payload.items.length },
        }),
      ),
    );
    await user.click(screen.getByRole('button', { name: '🚀 批量提交' }));
    await user.click(screen.getByRole('button', { name: '提交' }));
    await screen.findByText('已处理 12/12 行');
    expect(batchMock).toHaveBeenCalledTimes(2);
    expect(batchMock.mock.calls[0][0].items).toHaveLength(10);
    expect(batchMock.mock.calls[1][0].items).toHaveLength(2);
  });

  it('行结果：计算失败显示重试按钮并调用补算', async () => {
    const user = userEvent.setup();
    setupPending([draftItem(1)]);
    await screen.findByLabelText('第1行-操作');
    await user.selectOptions(screen.getByLabelText('第1行-操作'), 'approve');
    batchMock.mockResolvedValue(
      envelope({
        results: [{ draft_id: 1, ok: true, event_id: 10, error_code: null, error_message: null, compute_status: 'failed' }],
        summary: { approved: 1, ignored: 0, computed: 0 },
      }),
    );
    await user.click(screen.getByRole('button', { name: '🚀 批量提交' }));
    await user.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('已通过·影响计算失败')).toBeInTheDocument();

    computeMock.mockResolvedValue(envelope({ event_id: 10, status: 'ok', message: null }));
    await user.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(computeMock).toHaveBeenCalledWith(10, { operator: 'admin' }));
    expect(await screen.findByText('已触发重算')).toBeInTheDocument();
  });

  it('预填循环：remaining 收敛到 0 共 3 次调用', async () => {
    const user = userEvent.setup();
    setupPending([draftItem(1), draftItem(2)]);
    await screen.findByLabelText('第1行-操作');
    prelabelMock
      .mockResolvedValueOnce(envelope({ prelabeled: 10, remaining: 50 }))
      .mockResolvedValueOnce(envelope({ prelabeled: 20, remaining: 20 }))
      .mockResolvedValueOnce(envelope({ prelabeled: 20, remaining: 0 }));
    await user.click(screen.getByRole('button', { name: '🤖 AI 预填全部待审事件' }));
    await screen.findByText('已预填 50 条');
    expect(prelabelMock).toHaveBeenCalledTimes(3);
  });

  it('预填护栏：prelabeled=0 且 remaining>0 时停止并提示', async () => {
    const user = userEvent.setup();
    setupPending([draftItem(1), draftItem(2)]);
    await screen.findByLabelText('第1行-操作');
    prelabelMock.mockResolvedValue(envelope({ prelabeled: 0, remaining: 2 }));
    await user.click(screen.getByRole('button', { name: '🤖 AI 预填全部待审事件' }));
    expect(await screen.findByText(/LLM 不可用或全部预填失败，已停止/)).toBeInTheDocument();
    expect(prelabelMock).toHaveBeenCalledTimes(1);
  });
});
