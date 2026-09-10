import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../../test/utils';
import { ReviewTab } from './ReviewTab';

vi.mock('../../../../api/generated/services/EventStudiesService', () => ({
  EventStudiesService: {
    listPendingEventsApiV1EventStudiesReviewPendingEventsGet: vi.fn(),
    prelabelApiV1EventStudiesReviewPrelabelPost: vi.fn(),
    submitBatchApiV1EventStudiesReviewBatchPost: vi.fn(),
    computeApiV1EventStudiesReviewEventsEventIdComputePost: vi.fn(),
    listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet: vi.fn(),
    confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost: vi.fn(),
    refreshApiV1EventStudiesReviewRefreshPost: vi.fn(),
  },
}));

import { EventStudiesService } from '../../../../api/generated/services/EventStudiesService';

const pendingMock = EventStudiesService.listPendingEventsApiV1EventStudiesReviewPendingEventsGet as Mock;
const impactMock = EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet as Mock;
const refreshMock = EventStudiesService.refreshApiV1EventStudiesReviewRefreshPost as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  pendingMock.mockResolvedValue(envelope({ items: [] }));
  impactMock.mockResolvedValue(envelope({ items: [] }));
  // 默认：拉取完成但无新草稿——挂载自动拉取不触发预填、无错误文案
  refreshMock.mockResolvedValue(envelope({ fetched: 0, new_drafts: 0, skipped_reason: null }));
});

afterEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

describe('ReviewTab', () => {
  it('默认渲染待审核事件 Tab；切到影响结果确认渲染 Tab2', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ReviewTab />);
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.queryByText('暂无影响结果草稿')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '影响结果确认' }));
    expect(await screen.findByText('暂无影响结果草稿')).toBeInTheDocument();
    expect(screen.queryByText('暂无待审核事件')).not.toBeInTheDocument();
  });
});
