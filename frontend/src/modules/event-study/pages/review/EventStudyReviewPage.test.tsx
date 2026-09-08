import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../../test/utils';
import EventStudyReviewPage from './EventStudyReviewPage';

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
const impactMock = EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

beforeEach(() => {
  vi.clearAllMocks();
  pendingMock.mockResolvedValue(envelope({ items: [] }));
  impactMock.mockResolvedValue(envelope({ items: [] }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('EventStudyReviewPage', () => {
  it('默认渲染待审核事件 Tab；切到影响结果确认渲染 Tab2', async () => {
    const user = userEvent.setup();
    renderWithRouter(<EventStudyReviewPage />);
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.queryByText('暂无影响结果草稿')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '影响结果确认' }));
    expect(await screen.findByText('暂无影响结果草稿')).toBeInTheDocument();
    expect(screen.queryByText('暂无待审核事件')).not.toBeInTheDocument();
  });
});
