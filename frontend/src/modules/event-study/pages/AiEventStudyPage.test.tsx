import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../api/client';
import { renderWithRouter } from '../../../test/utils';
import AiEventStudyPage from './AiEventStudyPage';

vi.mock('../../../api/generated/services/EventStudiesService', () => ({
  EventStudiesService: {
    listAssetsApiV1EventStudiesAssetsGet: vi.fn(),
    predictApiV1EventStudiesPredictionsPost: vi.fn(),
  },
}));

import { EventStudiesService } from '../../../api/generated/services/EventStudiesService';

const assetsMock = EventStudiesService.listAssetsApiV1EventStudiesAssetsGet as Mock;
const predictMock = EventStudiesService.predictApiV1EventStudiesPredictionsPost as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

const predictionFixture = {
  prediction: { direction: 'up', predicted_return: 0.023, confidence: 0.61 },
  template_stats: { sample_count: 12, avg_car: 0.018, win_rate: 0.58 },
  supplement_events: [{ event_id: 101, title: '相似事件A', similarity: 0.87, weight: 0.31 }],
  note: null,
};

beforeEach(() => {
  assetsMock.mockReset();
  predictMock.mockReset();
  assetsMock.mockResolvedValue(
    envelope({ items: [{ ticker: '000001.SH', name: '上证指数', market: 'CN' }] }),
  );
  predictMock.mockResolvedValue(envelope(predictionFixture));
});

afterEach(() => {
  vi.clearAllMocks();
});

async function fillValidInputs(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByLabelText('事件文本');
  await user.type(screen.getByLabelText('事件文本'), '央行宣布降准');
  await user.type(screen.getByLabelText('目标资产'), '000001.SH');
}

describe('AiEventStudyPage', () => {
  it('事件文本必填与超长校验：阻止提交并保留输入', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AiEventStudyPage />);
    await screen.findByLabelText('事件文本');

    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('请输入事件文本')).toBeInTheDocument();
    expect(predictMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('事件文本'), { target: { value: 'x'.repeat(20001) } });
    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('事件文本不能超过 20000 字符')).toBeInTheDocument();
    expect(predictMock).not.toHaveBeenCalled();
  });

  it('成功预测：原页展示结果，不跳转任务详情；表单输入保留', async () => {
    const user = userEvent.setup();
    renderWithRouter(<AiEventStudyPage />);
    await fillValidInputs(user);

    await user.click(screen.getByRole('button', { name: '预测' }));

    expect(await screen.findByText('看多')).toBeInTheDocument();
    expect(screen.getByText('2.30%')).toBeInTheDocument();
    expect(screen.getByText('61%')).toBeInTheDocument();
    expect(screen.getByText('相似事件A')).toBeInTheDocument();
    expect(screen.getByText(/相似度 87%/)).toBeInTheDocument();
    // 不跳转任务详情、不伪装为分析报告
    expect(screen.queryByTestId('task-detail-sentinel')).not.toBeInTheDocument();
    expect(screen.getByLabelText('事件文本')).toHaveValue('央行宣布降准');
    // 请求体：窗口默认 post_event_5d、save 默认 false
    const [body] = predictMock.mock.calls[0];
    expect(body.window_type).toBe('post_event_5d');
    expect(body.save).toBe(false);
    expect(body.asset_ticker).toBe('000001.SH');
  });

  it('503 EVENT_STUDY_BUSY：保留输入，仅手动重试，不自动重试', async () => {
    const user = userEvent.setup();
    predictMock.mockRejectedValue(
      new ApiError({ code: 'EVENT_STUDY_BUSY', message: '事件研究请求繁忙，请稍后重试', retryable: true, status: 503 }),
    );
    renderWithRouter(<AiEventStudyPage />);
    await fillValidInputs(user);

    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('事件研究请求繁忙，请稍后重试')).toBeInTheDocument();
    expect(predictMock).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('事件文本')).toHaveValue('央行宣布降准');

    predictMock.mockResolvedValue(envelope(predictionFixture));
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('看多')).toBeInTheDocument();
    expect(predictMock).toHaveBeenCalledTimes(2);
  });

  it('504 EVENT_STUDY_TIMEOUT：保留输入，提示等待超时，仅手动重试', async () => {
    const user = userEvent.setup();
    predictMock.mockRejectedValue(
      new ApiError({ code: 'EVENT_STUDY_TIMEOUT', message: '本次等待超时', retryable: true, status: 504 }),
    );
    renderWithRouter(<AiEventStudyPage />);
    await fillValidInputs(user);

    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('本次等待超时')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByLabelText('目标资产')).toHaveValue('000001.SH');
  });

  it('样本不足/结果为空按 note 正常呈现，不生成前端结论', async () => {
    const user = userEvent.setup();
    predictMock.mockResolvedValue(
      envelope({
        prediction: { direction: 'neutral', predicted_return: null, confidence: 0 },
        template_stats: { sample_count: 0, avg_car: null, win_rate: null },
        supplement_events: [],
        note: '事件库暂无相似事件',
      }),
    );
    renderWithRouter(<AiEventStudyPage />);
    await fillValidInputs(user);

    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('事件库暂无相似事件')).toBeInTheDocument();
    expect(screen.getByText('中性')).toBeInTheDocument();
  });

  it('从宏观信息跳转：URL 携带 event_id + 站内 state 预填输入草稿，不自动提交', async () => {
    renderWithRouter(<AiEventStudyPage />, {
      initialEntries: [
        {
          pathname: '/ai/event-study',
          search: '?event_id=101',
          state: {
            prefill: {
              eventText: '央行宣布降准\n宏观事件摘要',
              eventType: '货币政策',
              assetTicker: '000001.SH',
            },
          },
        },
      ],
    });

    await screen.findByLabelText('事件文本');
    expect(screen.getByLabelText('事件文本')).toHaveValue('央行宣布降准\n宏观事件摘要');
    expect(screen.getByLabelText('事件类型（可选）')).toHaveValue('货币政策');
    expect(screen.getByLabelText('目标资产')).toHaveValue('000001.SH');
    // 绝不自动执行预测
    expect(predictMock).not.toHaveBeenCalled();
  });

  it('资产清单接口失败：表单常驻可自由输入并正常提交（契约 §7.2 降级）', async () => {
    assetsMock.mockRejectedValue(
      new ApiError({ code: 'INTERNAL_ERROR', message: '资产清单不可用', retryable: false, status: 500 }),
    );
    const user = userEvent.setup();
    renderWithRouter(<AiEventStudyPage />);
    await fillValidInputs(user);

    expect(screen.getByText('资产清单暂不可用，可自由输入目标资产代码')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '预测' }));
    expect(await screen.findByText('看多')).toBeInTheDocument();
    expect(predictMock).toHaveBeenCalledTimes(1);
  });

  it('资产下拉来自 assets 接口，不硬编码名单', async () => {
    renderWithRouter(<AiEventStudyPage />);

    const datalist = await screen.findByRole('listbox', { hidden: true });
    expect(datalist).toBeTruthy();
    // 表单常驻渲染，选项在 assets 加载后填充
    await waitFor(() => expect(datalist.querySelector('option')).toHaveValue('000001.SH'));
    // 自由输入仍允许（无接口名单硬编码：datalist 仅 1 项）
    expect(datalist.querySelectorAll('option')).toHaveLength(1);
  });
});
