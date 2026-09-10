import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import EventStudyPage from './EventStudyPage';

vi.mock('../../../api/generated/services/EventStudiesService', () => ({
  EventStudiesService: {
    listAssetsApiV1EventStudiesAssetsGet: vi.fn(),
    predictApiV1EventStudiesPredictionsPost: vi.fn(),
    listPendingEventsApiV1EventStudiesReviewPendingEventsGet: vi.fn(),
    prelabelApiV1EventStudiesReviewPrelabelPost: vi.fn(),
    submitBatchApiV1EventStudiesReviewBatchPost: vi.fn(),
    computeApiV1EventStudiesReviewEventsEventIdComputePost: vi.fn(),
    listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet: vi.fn(),
    confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost: vi.fn(),
    refreshApiV1EventStudiesReviewRefreshPost: vi.fn(),
  },
}));

import { EventStudiesService } from '../../../api/generated/services/EventStudiesService';

const assetsMock = EventStudiesService.listAssetsApiV1EventStudiesAssetsGet as Mock;
const pendingMock = EventStudiesService.listPendingEventsApiV1EventStudiesReviewPendingEventsGet as Mock;
const impactMock = EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet as Mock;
const refreshMock = EventStudiesService.refreshApiV1EventStudiesReviewRefreshPost as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  assetsMock.mockResolvedValue(envelope({ items: [] }));
  pendingMock.mockResolvedValue(envelope({ items: [] }));
  impactMock.mockResolvedValue(envelope({ items: [] }));
  // 默认：拉取完成但无新草稿——审核子 Tab 挂载的自动拉取不触发预填、无错误文案
  refreshMock.mockResolvedValue(envelope({ fetched: 0, new_drafts: 0, skipped_reason: null }));
});

afterEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

describe('EventStudyPage', () => {
  it('默认渲染影响预测 Tab', async () => {
    renderWithRouter(<EventStudyPage />);
    expect(await screen.findByLabelText('事件文本')).toBeInTheDocument();
    expect(screen.queryByText('暂无待审核事件')).not.toBeInTheDocument();
  });

  it('?tab=review 初始渲染审核 Tab', async () => {
    renderWithRouter(<EventStudyPage />, { initialEntries: ['/event-study?tab=review'] });
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.queryByLabelText('事件文本')).not.toBeInTheDocument();
  });

  it('按钮切换审核 Tab，URL 写入 tab=review', async () => {
    const user = userEvent.setup();
    renderWithRouter(<EventStudyPage />, { initialEntries: ['/event-study'] });
    await screen.findByLabelText('事件文本');

    await user.click(screen.getByRole('button', { name: '事件审核' }));
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveAttribute('data-search', '?tab=review');
    expect(screen.getByTestId('location')).toHaveAttribute('data-pathname', '/event-study');
  });

  it('切回预测 Tab 时清除 tab 参数', async () => {
    const user = userEvent.setup();
    renderWithRouter(<EventStudyPage />, { initialEntries: ['/event-study?tab=review'] });
    await screen.findByText('暂无待审核事件');

    await user.click(screen.getByRole('button', { name: '影响预测' }));
    expect(await screen.findByLabelText('事件文本')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveAttribute('data-search', '');
  });

  it('切换 Tab 保留其余参数（如宏观卡片跳转的 event_id）', async () => {
    const user = userEvent.setup();
    renderWithRouter(<EventStudyPage />, { initialEntries: ['/event-study?event_id=101'] });
    await screen.findByLabelText('事件文本');

    await user.click(screen.getByRole('button', { name: '事件审核' }));
    expect(await screen.findByText('暂无待审核事件')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveAttribute('data-search', '?event_id=101&tab=review');
  });
});
