// test-catalog-begin
// {
//   "purpose": "行情界面 / MarketRefreshInline：标题旁行内状态——已更新到目标交易日只显示 MM.DD；未更新且任务在跑显示「MM.DD 更新中（x%）」；排队显示排队中；无任务未达标显示待补齐 x/y 并可重试；中国行两资源短名并列；状态接口失败标明只是上次核验、不伪造日期",
//   "keywords": [
//     "行情界面",
//     "市场分析",
//     "行情刷新",
//     "状态",
//     "market_refresh_inline",
//     "market"
//   ],
//   "covers": [
//     "frontend/src/api/generated/index.ts",
//     "frontend/src/modules/market/components/MarketRefreshInline.tsx",
//     "frontend/src/modules/market/pages/refreshQueries.ts"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RefreshGroup as RefreshGroupModel, RefreshJob as RefreshJobModel, Resource, type RefreshGroup, type RefreshStatusData } from '../../../../../frontend/src/api/generated';
import { MarketRefreshInline, formatTargetDate, type MarketRefreshState } from '../../../../../frontend/src/modules/market/components/MarketRefreshInline';

function group(overrides: Partial<RefreshGroup> = {}): RefreshGroup {
  return {
    resource: Resource.US_INDEX_BARS,
    market: RefreshGroupModel.market.US,
    market_date: '2026-10-02',
    calendar_status: RefreshGroupModel.calendar_status.OK,
    supported_through: '2027-12-31',
    expected_trade_date: '2026-10-02',
    next_ready_at: null,
    latest_observed_date: '2026-10-02',
    complete_through_date: '2026-10-02',
    expected_count: 3,
    available_count: 3,
    exempt_count: 0,
    missing_count: 0,
    freshness: RefreshGroupModel.freshness.FRESH,
    window_coverage: { from: '2026-09-28', to: '2026-10-02', expected_count: 15, available_count: 15, exempt_count: 0, missing_count: 0 },
    data_version: 'coverage-test',
    auto_eligibility: { allowed: false, reason: 'UP_TO_DATE', next_retry_at: null },
    manual_eligibility: { allowed: false, reason: 'UP_TO_DATE', next_retry_at: null },
    job: null,
    ...overrides,
  };
}

function renderInline(groups: RefreshGroup[], options: { error?: boolean; pending?: boolean; resources?: Resource[]; named?: boolean } = {}) {
  const status: RefreshStatusData = {
    server_time: '2026-10-02T14:00:00Z',
    refresh_available: true,
    worker_online: true,
    concept_display_date: null,
    groups,
  };
  const refresh = {
    status, error: options.error ? new Error('状态接口失败') : null,
    pending: options.pending ?? false, retry: vi.fn(),
  } as unknown as MarketRefreshState;
  render(<MarketRefreshInline refresh={refresh} resources={options.resources ?? [Resource.US_INDEX_BARS]} named={options.named} />);
  return refresh;
}

const runningJob = (processed: number, total: number) => ({
  id: 'job-1', resource: Resource.US_INDEX_BARS, target_trade_date: '2026-09-24',
  status: RefreshJobModel.status.RUNNING, attempt: 1, processed, total,
  started_at: null, heartbeat_at: null, error_code: null, error_summary: null,
});

describe('MarketRefreshInline', () => {
  it('已更新到目标交易日只显示 MM.DD，不写「已更新」', () => {
    renderInline([group()]);

    expect(screen.getByText('10.02')).toBeInTheDocument();
    expect(screen.queryByText(/已更新/)).not.toBeInTheDocument();
  });

  it('未更新且任务在跑显示「目标日 + 更新中（完成百分比）」', () => {
    renderInline([group({
      expected_trade_date: '2026-09-24', freshness: RefreshGroupModel.freshness.PARTIAL,
      expected_count: 11, available_count: 0, missing_count: 11,
      job: runningJob(25, 100),
    })]);

    expect(screen.getByText('09.24')).toBeInTheDocument();
    expect(screen.getByText('更新中（25%）')).toBeInTheDocument();
    expect(screen.getByLabelText('行情拉取状态')).toHaveTextContent('09.24 更新中（25%）');
  });

  it('排队中不显示百分比，等重试沿用已处理计数', () => {
    renderInline([group({
      freshness: RefreshGroupModel.freshness.PARTIAL, job: { ...runningJob(0, 30), status: RefreshJobModel.status.QUEUED },
    })]);
    expect(screen.getByText('排队中')).toBeInTheDocument();
    expect(screen.queryByText(/更新中/)).not.toBeInTheDocument();
  });

  it('无活跃任务未达标时显示待补齐 x/y，并可手动重试', () => {
    const refresh = renderInline([group({
      freshness: RefreshGroupModel.freshness.PARTIAL, expected_count: 11,
      available_count: 4, exempt_count: 1, missing_count: 6,
      manual_eligibility: { allowed: true, reason: 'MISSING_DATA', next_retry_at: null },
    })]);

    expect(screen.getByText('待补齐 5/11')).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: '重试' });
    expect(retry).toBeEnabled();
    retry.click();
    expect(refresh.retry).toHaveBeenCalledWith(Resource.US_INDEX_BARS);
  });

  it('状态接口失败但留有缓存时标明只是上次核验，不把旧状态当现状', () => {
    renderInline([group({ freshness: RefreshGroupModel.freshness.PARTIAL, available_count: 1, missing_count: 2 })], { error: true });

    expect(screen.getByText('待补齐 1/3')).toBeInTheDocument();
    expect(screen.getByText('上次核验')).toBeInTheDocument();
    expect(screen.getByLabelText('行情拉取状态')).toHaveTextContent('10.02 待补齐 1/3 · 上次核验');
  });

  it('状态暂不可用时不猜测日期或进度', () => {
    renderInline([], { error: true });

    expect(screen.getByText('状态暂不可用')).toBeInTheDocument();
    expect(screen.queryByText(/更新中/)).not.toBeInTheDocument();
  });

  it('较早历史缺口随行标注，不读成目标日未更新', () => {
    renderInline([group({
      freshness: RefreshGroupModel.freshness.PARTIAL, expected_count: 3, available_count: 3,
      missing_count: 0, warnings: ['HISTORY_GAP'],
    })]);
    expect(screen.getByText('较早历史缺口')).toBeInTheDocument();
    expect(screen.getByLabelText('行情拉取状态')).toHaveTextContent('10.02 待补齐 3/3 · 较早历史缺口');
  });

  it('中国行两资源短名并列，各自显示目标日', () => {
    renderInline([
      group({ resource: Resource.CN_INDEX_BARS, market: RefreshGroupModel.market.CN, expected_trade_date: '2026-09-30' }),
      group({
        resource: Resource.CN_INDEX_FACTORS, market: RefreshGroupModel.market.CN, expected_trade_date: '2026-09-30',
        latest_observed_date: '2026-09-29', freshness: RefreshGroupModel.freshness.STALE,
        expected_count: 7, available_count: 6, missing_count: 1,
        manual_eligibility: { allowed: false, reason: 'JOB_ACTIVE', next_retry_at: null },
      }),
    ], { resources: [Resource.CN_INDEX_BARS, Resource.CN_INDEX_FACTORS] });

    expect(screen.getByText('指数')).toBeInTheDocument();
    expect(screen.getByText('指标')).toBeInTheDocument();
    expect(screen.getByLabelText('行情拉取状态')).toHaveTextContent('指数 09.30 · 指标 09.30 待补齐 6/7');
  });

  it('目标交易日缺失时不编造日期，只给状态', () => {
    renderInline([group({ expected_trade_date: null, freshness: RefreshGroupModel.freshness.UNKNOWN, expected_count: 3, available_count: 0, missing_count: 3 })]);

    expect(screen.getByText('待补齐 0/3')).toBeInTheDocument();
    expect(formatTargetDate(null)).toBeNull();
  });
});
