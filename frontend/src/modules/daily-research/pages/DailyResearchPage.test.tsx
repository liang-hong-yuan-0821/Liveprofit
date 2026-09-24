import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderWithRouter } from '../../../test/utils';
import DailyResearchPage from './DailyResearchPage';

vi.mock('../../../api/generated/services/DailyResearchService', () => ({
  DailyResearchService: {
    createManualResearchRunApiV1DailyResearchRunsPost: vi.fn(),
    listResearchRunsApiV1DailyResearchRunsGet: vi.fn(),
    listDisputedEventAssessmentsApiV1DailyResearchAssessmentsDisputedGet: vi.fn(),
    reviewDisputedEventAssessmentApiV1DailyResearchAssessmentsAssessmentIdReviewPost: vi.fn(),
    getResearchRunApiV1DailyResearchRunsTaskIdGet: vi.fn(),
  },
}));
vi.mock('../../market/pages/refreshQueries', () => ({
  useMarketRefresh: vi.fn(() => ({
    status: { refresh_available: true, worker_online: true, groups: [{
      resource: 'CN_STOCK_DAILY', expected_count: 5568, available_count: 5556, exempt_count: 12,
      freshness: 'FRESH', expected_trade_date: '2026-09-23', job: null,
      manual_eligibility: { allowed: false, reason: 'UP_TO_DATE', next_retry_at: null },
    }] },
    error: false, pending: false, retry: vi.fn(), dates: {},
  })),
}));

import { DailyResearchService } from '../../../api/generated/services/DailyResearchService';
import { useMarketRefresh } from '../../market/pages/refreshQueries';

const createMock = DailyResearchService.createManualResearchRunApiV1DailyResearchRunsPost as Mock;
const listMock = DailyResearchService.listResearchRunsApiV1DailyResearchRunsGet as Mock;
const disputedMock = DailyResearchService.listDisputedEventAssessmentsApiV1DailyResearchAssessmentsDisputedGet as Mock;
const reviewMock = DailyResearchService.reviewDisputedEventAssessmentApiV1DailyResearchAssessmentsAssessmentIdReviewPost as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue(envelope({ items: [] }));
  disputedMock.mockResolvedValue(envelope({ items: [] }));
  reviewMock.mockResolvedValue(envelope({ item: {
    assessment_id: 'assessment-1', event_id: 7, fact_key: 'fact-1', revision: 2,
    review_status: 'accepted', task_id: 'task-review-1', task_status: 'PENDING',
    idempotent_replay: false,
  } }));
  createMock.mockResolvedValue(envelope({
    task_id: 'task-1',
    kind: 'news',
    status: 'PENDING',
  }));
});

describe('DailyResearchPage', () => {
  it('个股拉取状态只在量化扫描区域展示', async () => {
    const user = userEvent.setup();
    renderWithRouter(<DailyResearchPage />);
    expect(screen.queryByText('个股日线')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '量化策略扫描' }));
    expect(screen.getByText('个股日线')).toBeInTheDocument();
    expect(useMarketRefresh).toHaveBeenCalledWith('stock');
  });

  it('分别提交新闻分析和全策略量化扫描', async () => {
    const user = userEvent.setup();
    renderWithRouter(<DailyResearchPage />);

    await screen.findByText('新闻事件研究');
    await user.click(screen.getByRole('button', { name: '立即分析新闻事件' }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0][0]).toEqual({ kind: 'news' });
    expect(createMock.mock.calls[0][1]).toMatch(/[A-Za-z0-9_-]{1,128}/);

    await user.click(screen.getByRole('button', { name: '运行全部量化策略' }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    expect(createMock.mock.calls[1][0]).toEqual({ kind: 'quant' });
  });

  it('在人工复核队列中保存修订标签并创建新闻展望批次', async () => {
    const labels = {
      fact: { stage: 'published', event_type: '政策', event_condition: '未知', importance: 3 },
      targets: [{ target: 'market:CN', scope: 'market', scope_refs: [], horizons: [] }],
    };
    disputedMock.mockResolvedValue(envelope({ items: [{
      assessment_id: 'assessment-1', news_id: 'news-1', event_id: 7, fact_key: 'fact-1',
      revision: 2, novelty: 'update', review_status: 'disputed', labels,
      evidence: [{ evidence_id: 'e-1', quote: '原文依据' }], available_at: '2026-09-23T08:00:00Z',
      title: '政策更新', raw_content: '完整新闻正文', source: 'cls_telegraph', source_label: '财联社',
      source_url: null,
    }] }));
    const user = userEvent.setup();
    renderWithRouter(<DailyResearchPage />);

    const editor = await screen.findByRole('textbox', { name: '判断标签 JSON：政策更新' });
    const corrected = { ...labels, fact: { ...labels.fact, importance: 4 } };
    fireEvent.change(editor, { target: { value: JSON.stringify(corrected) } });
    await user.click(screen.getByRole('button', { name: '保存标签并确认' }));

    await waitFor(() => expect(reviewMock).toHaveBeenCalledTimes(1));
    expect(reviewMock.mock.calls[0][0]).toBe('assessment-1');
    expect(reviewMock.mock.calls[0][1]).toMatchObject({
      expected_revision: 2,
      review_status: 'accepted',
      labels: corrected,
      review_note: '人工复核确认',
    });
    expect(reviewMock.mock.calls[0][2]).toMatch(/[A-Za-z0-9_-]{1,128}/);
  });
});
