// test-catalog-begin
// {
//   "purpose": "行情界面 / MarketRefreshStatus：状态接口失败但留有缓存时标明仅是上次核验结果；已更新时只展示精简状态，不在区块里重复旧任务进度；终态核验计数格式无效也不暴露已过期的处理计数",
//   "keywords": [
//     "行情界面",
//     "市场分析",
//     "行情刷新",
//     "状态",
//     "market_refresh_status",
//     "market",
//     "refresh",
//     "status"
//   ],
//   "covers": [
//     "frontend/src/api/generated/index.ts",
//     "frontend/src/modules/market/components/MarketRefreshStatus.tsx",
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
import { MarketRefreshStatus } from '../../../../../frontend/src/modules/market/components/MarketRefreshStatus';
import type { useMarketRefresh } from '../../../../../frontend/src/modules/market/pages/refreshQueries';

function renderStatus(job: NonNullable<RefreshGroup['job']>, nextRetryAt: string | null = null, overrides: Partial<RefreshGroup> = {}, error = false) {
  const group: RefreshGroup = {
    resource: Resource.CN_INDEX_BARS,
    market: RefreshGroupModel.market.CN,
    market_date: '2026-09-23',
    calendar_status: RefreshGroupModel.calendar_status.OK,
    supported_through: '2027-12-31',
    expected_trade_date: '2026-09-23',
    next_ready_at: null,
    latest_observed_date: '2026-09-23',
    complete_through_date: '2026-09-23',
    expected_count: 11,
    available_count: 11,
    exempt_count: 0,
    missing_count: 0,
    freshness: RefreshGroupModel.freshness.FRESH,
    window_coverage: { from: '2026-09-21', to: '2026-09-23', expected_count: 33, available_count: 33, exempt_count: 0, missing_count: 0 },
    data_version: 'coverage-test',
    auto_eligibility: { allowed: false, reason: 'UP_TO_DATE', next_retry_at: null },
    manual_eligibility: { allowed: false, reason: 'JOB_ACTIVE', next_retry_at: nextRetryAt },
    job,
    ...overrides,
  };
  const status: RefreshStatusData = {
    server_time: '2026-09-23T14:00:00Z',
    refresh_available: true,
    worker_online: true,
    concept_display_date: null,
    groups: [group],
  };
  const refresh = { status, error, pending: false, retry: vi.fn() } as unknown as ReturnType<typeof useMarketRefresh>;
  render(<MarketRefreshStatus refresh={refresh} resources={[group.resource]} />);
}

describe('MarketRefreshStatus', () => {
  it('状态接口失败但留有缓存时标明仅是上次核验结果', () => {
    renderStatus({
      id: 'job-1', resource: Resource.CN_INDEX_BARS, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.SUCCEEDED, attempt: 1, processed: 0, total: 11,
      started_at: null, heartbeat_at: null, error_code: null, error_summary: null,
    }, null, {}, true);
    expect(screen.getByText(/上次核验，当前状态暂不可用/)).toBeInTheDocument();
  });

  it('已更新时只展示精简状态，不在区块里重复旧任务进度', () => {
    renderStatus({
      id: 'job-1', resource: Resource.CN_INDEX_BARS, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.SUCCEEDED, attempt: 1, processed: 0, total: 22,
      started_at: null, heartbeat_at: null, error_code: null, error_summary: null,
      result: { total: 22, completed: 22, freshness: 'FRESH' },
    });

    expect(screen.getByText('已更新')).toBeInTheDocument();
    expect(screen.queryByText(/22\/22/)).not.toBeInTheDocument();
  });

  it('终态核验计数格式无效也不暴露已过期的处理计数', () => {
    renderStatus({
      id: 'job-3', resource: Resource.CN_INDEX_BARS, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.SUCCEEDED, attempt: 1, processed: 0, total: 22,
      started_at: null, heartbeat_at: null, error_code: null, error_summary: null,
      result: { total: '22', completed: 22, freshness: 'FRESH' },
    });

    expect(screen.getByText('已更新')).toBeInTheDocument();
    expect(screen.queryByText(/0\/22/)).not.toBeInTheDocument();
  });

  it('等待重试时把进度明确标为已处理，而非已覆盖', () => {
    renderStatus({
      id: 'job-2', resource: Resource.CN_INDEX_BARS, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.RETRY_WAIT, attempt: 1, processed: 2062, total: 2062,
      started_at: null, heartbeat_at: null, error_code: 'UPSTREAM_NOT_READY',
      error_summary: '仍有行情缺口，已保存成功入库的数据',
      result: { total: 2062, completed: 2060, freshness: 'PARTIAL' },
    }, '2026-09-23T14:45:37Z');

    expect(screen.getByText(/等待重试 · 已处理 2062\/2062/)).toBeInTheDocument();
    expect(screen.queryByText('等待重试 2062/2062')).not.toBeInTheDocument();
  });

  it('补齐后不再展示旧的部分完成任务，并解释历史缺口', () => {
    renderStatus({
      id: 'job-old', resource: Resource.CN_SECTOR_DAILY, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.PARTIAL, attempt: 1, processed: 1031, total: 1031,
      started_at: null, heartbeat_at: null, error_code: 'UPSTREAM_NOT_READY',
      error_summary: '仍有行情缺口，已保存成功入库的数据',
      result: { total: 1031, completed: 1030, freshness: 'PARTIAL' },
    }, null, {
      resource: Resource.CN_SECTOR_DAILY,
      expected_count: 1031, available_count: 1031,
      warnings: ['HISTORY_GAP'],
    });

    expect(screen.getByText('板块日线')).toBeInTheDocument();
    expect(screen.getByText('已更新')).toBeInTheDocument();
    expect(screen.queryByText(/部分完成 1030\/1031/)).not.toBeInTheDocument();
    expect(screen.queryByText(/较早历史仍有缺口/)).not.toBeInTheDocument();
  });

  it('目录缺少生命周期数据时说明上游资料未齐，不再误导为只需初始化目录', () => {
    renderStatus({
      id: 'job-4', resource: Resource.CN_STOCK_DAILY, target_trade_date: '2026-09-23',
      status: RefreshJobModel.status.FAILED, attempt: 1, processed: 0, total: 3,
      started_at: null, heartbeat_at: null, error_code: 'CATALOG_INCOMPLETE',
      error_summary: null, result: { total: 3, completed: 0, freshness: 'UNAVAILABLE' },
    }, null, {
      resource: Resource.CN_STOCK_DAILY,
      freshness: RefreshGroupModel.freshness.STALE,
      manual_eligibility: { allowed: false, reason: 'CATALOG_INCOMPLETE', next_retry_at: null },
    });

    expect(screen.getByText(/股票目录缺少上市资料/)).toBeInTheDocument();
    expect(screen.queryByText('股票目录信息不完整，请先运行目录初始化。')).not.toBeInTheDocument();
  });
});
