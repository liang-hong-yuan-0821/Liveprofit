import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../../api/client';
import { renderWithRouter } from '../../../../test/utils';
import { ImpactConfirmTab } from './ImpactConfirmTab';

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

const impactMock = EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet as Mock;
const confirmMock = EventStudiesService.confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function impactFixture() {
  return [
    {
      event_id: 101,
      title: 'CPI 公布',
      t0: '2026-08-03',
      computed_at: '2026-08-04T08:31:02',
      assets: {
        '000001.SH': {
          pre_event_5d: {
            window_days: 5,
            cumulative_abnormal_return: 0.0123,
            t_stat: 2.15,
            direction: 1,
            is_contaminated: false,
          },
          post_event_5d: {
            window_days: 5,
            cumulative_abnormal_return: -0.02,
            t_stat: -2.5,
            direction: -1,
            is_contaminated: true,
            error: 'not enough data',
          },
        },
      },
    },
  ];
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('ImpactConfirmTab', () => {
  it('行渲染：CAR 三位小数、t 两位、方向徽章、污染角标、备注', async () => {
    impactMock.mockResolvedValue(envelope({ items: impactFixture() }));
    renderWithRouter(<ImpactConfirmTab />);
    await screen.findAllByText('CPI 公布');
    expect(screen.getByText('1.230')).toBeInTheDocument();
    expect(screen.getByText('2.15')).toBeInTheDocument();
    expect(screen.getByText('利好 ↑')).toBeInTheDocument();
    expect(screen.getByText('利空 ↓')).toBeInTheDocument();
    expect(screen.getByText('⚠️')).toBeInTheDocument();
    expect(screen.getByText('not enough data')).toBeInTheDocument();
  });

  it('勾选确认：按事件分组逐事件调用 confirm 并汇总 inserted', async () => {
    const user = userEvent.setup();
    impactMock.mockResolvedValue(envelope({ items: impactFixture() }));
    confirmMock.mockResolvedValue(envelope({ event_id: 101, inserted: 1 }));
    renderWithRouter(<ImpactConfirmTab />);
    const checkbox = await screen.findByLabelText('勾选 101:000001.SH:pre_event_5d');
    await user.click(checkbox);
    await user.click(screen.getByRole('button', { name: '🚀 确认落表' }));
    await user.click(screen.getByRole('button', { name: '确认落表' }));
    await waitFor(() =>
      expect(confirmMock).toHaveBeenCalledWith(101, { tickers: ['000001.SH'], operator: 'admin' }),
    );
    expect(await screen.findByText('已确认落表 1 条')).toBeInTheDocument();
  });

  it('未勾选提交：提示且不调用 confirm', async () => {
    const user = userEvent.setup();
    impactMock.mockResolvedValue(envelope({ items: impactFixture() }));
    renderWithRouter(<ImpactConfirmTab />);
    await screen.findAllByText('CPI 公布');
    await user.click(screen.getByRole('button', { name: '🚀 确认落表' }));
    await user.click(screen.getByRole('button', { name: '确认落表' }));
    expect(await screen.findByText('请至少勾选一行')).toBeInTheDocument();
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it('503：错误横幅，不渲染空态', async () => {
    impactMock.mockRejectedValue(
      new ApiError({ code: 'REVIEW_UPSTREAM_UNAVAILABLE', message: '审核草稿区（Redis）不可用，请稍后重试', retryable: true, status: 503 }),
    );
    renderWithRouter(<ImpactConfirmTab />);
    expect(await screen.findByText('审核草稿区（Redis）不可用，请稍后重试')).toBeInTheDocument();
    expect(screen.queryByText('暂无影响结果草稿')).not.toBeInTheDocument();
  });

  it('空态：无草稿显示提示', async () => {
    impactMock.mockResolvedValue(envelope({ items: [] }));
    renderWithRouter(<ImpactConfirmTab />);
    expect(await screen.findByText('暂无影响结果草稿')).toBeInTheDocument();
  });

  it('error 行禁勾选且全选排除', async () => {
    const user = userEvent.setup();
    impactMock.mockResolvedValue(envelope({ items: impactFixture() }));
    renderWithRouter(<ImpactConfirmTab />);
    await screen.findAllByText('CPI 公布');
    expect(screen.getByLabelText('勾选 101:000001.SH:post_event_5d')).toBeDisabled();
    await user.click(screen.getByLabelText('全选'));
    expect(screen.getByLabelText('勾选 101:000001.SH:pre_event_5d')).toBeChecked();
    expect(screen.getByLabelText('勾选 101:000001.SH:post_event_5d')).not.toBeChecked();
  });
});
