// test-catalog-begin
// {
//   "purpose": "行情刷新 / market-refresh：stale → queued → partial → complete, page reload reuses the running job；failure and offline status preserve the existing chart and history selection",
//   "keywords": [
//     "行情刷新",
//     "历史审计",
//     "市场分析",
//     "选择范围",
//     "状态",
//     "market-refresh",
//     "history",
//     "market",
//     "refresh",
//     "selection",
//     "status"
//   ],
//   "covers": [
//     "frontend/src/modules/market/components/MarketRefreshStatus.tsx",
//     "frontend/src/modules/market/pages/refreshQueries.ts"
//   ],
//   "environment": [
//     "browser",
//     "app"
//   ]
// }
// test-catalog-end

/// <reference lib="dom" />
import { expect, test, type Page } from '@playwright/test';

test.use({ channel: process.env.PLAYWRIGHT_CHANNEL });
type Stage = 'stale' | 'queued' | 'partial' | 'complete' | 'failed' | 'offline';
async function isolate(page: Page) {
  let stage: Stage = 'stale';
  let submissions = 0;
  const unexpected: string[] = [];
  const errors: string[] = [];
  const requestedDates: Array<string | null> = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    let data: unknown;
    if (path.endsWith('/market-data/refresh-status')) {
      const complete = stage === 'complete'; const partial = stage === 'partial';
      const jobStatus = stage === 'queued' ? 'QUEUED' : partial ? 'RUNNING' : complete ? 'SUCCEEDED' : 'FAILED';
      data = {
        server_time: '2026-09-23T01:00:00Z', refresh_available: stage !== 'offline', worker_online: stage !== 'offline', concept_display_date: '2026-09-22',
        groups: [{
          resource: 'CN_INDEX_BARS', market: 'CN', market_date: '2026-09-23', calendar_status: 'OK', supported_through: '2026-12-31',
          expected_trade_date: '2026-09-22', next_ready_at: null, latest_observed_date: complete ? '2026-09-22' : '2026-09-21', complete_through_date: complete ? '2026-09-22' : '2026-09-21',
          freshness: complete ? 'FRESH' : partial ? 'PARTIAL' : 'STALE', expected_count: 11, available_count: complete ? 11 : partial ? 7 : 0, exempt_count: 0, missing_count: complete ? 0 : partial ? 4 : 11,
          window_coverage: { from: '2026-09-18', to: '2026-09-22', expected_count: 33, available_count: complete ? 33 : 22, exempt_count: 0, missing_count: complete ? 0 : 11 },
          data_version: partial ? 'v2' : complete ? 'v3' : 'v1',
          auto_eligibility: { allowed: stage === 'stale', reason: 'fixture', next_retry_at: null },
          manual_eligibility: { allowed: stage === 'failed', reason: 'fixture', next_retry_at: null },
          job: stage === 'stale' || stage === 'offline' ? null : { id: 'shared-job', resource: 'CN_INDEX_BARS', target_trade_date: '2026-09-22', status: jobStatus, attempt: 1, processed: complete ? 11 : partial ? 7 : 0, total: 11, started_at: null, heartbeat_at: null, error_code: stage === 'failed' ? 'SOURCE_ERROR' : null, error_summary: stage === 'failed' ? '测试数据源暂不可用' : null },
        }],
      };
    } else if (path.endsWith('/market-data/refresh') && request.method() === 'POST') {
      expect(request.postDataJSON().mode).toBe('auto');
      submissions++; stage = 'queued';
      data = { decisions: [{ resource: 'CN_INDEX_BARS', decision: 'QUEUED', job_id: 'shared-job', reason: 'fixture', next_retry_at: null }] };
    } else if (path.includes('/market-data/indices/') && path.endsWith('/bars')) {
      data = { asset: {}, interval: '1d', from: url.searchParams.get('from'), to: url.searchParams.get('to'), bars: Array.from({ length: 60 }, (_, i) => ({ timestamp: new Date(Date.UTC(2026, 6, i + 1)).toISOString(), open: 100 + i, close: 102 + i, low: 98 + i, high: 104 + i, volume: 1000 })), indicators: null, source: 'isolated-refresh-fixture', as_of: stage === 'complete' ? '2026-09-22' : '2026-09-21', source_updated_at: null, freshness_status: stage === 'complete' ? 'FRESH' : 'STALE', market_session_status: 'CLOSED', market_closed_reason: '收盘' };
    } else if (path.includes('/market-data/trends/')) data = { series: [], from: url.searchParams.get('from'), to: url.searchParams.get('to'), as_of: null, freshness_status: 'UNAVAILABLE' };
    else if (path.endsWith('/market-data/concepts/tree')) {
      const date = url.searchParams.get('as_of'); requestedDates.push(date);
      data = { items: [], as_of: date ?? '2026-09-22', requested_as_of: date, date_mode: date ? 'HISTORICAL' : 'LATEST', coverage: { boards: { expected_count: 0, available_count: 0, exempt_count: 0, missing_count: 0 }, members: null }, freshness_status: 'UNAVAILABLE', algorithm_version: 'heat_v1', result_status: 'NO_HOT_CONCEPTS', source: 'dc', source_updated_at: null };
    } else if (path.endsWith('/macro-information')) data = { items: [] };
    else { unexpected.push(`${request.method()} ${path}`); await route.abort('blockedbyclient'); return; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data, meta: { request_id: 'isolated-market-refresh', schema_version: 'v1' } }) });
  });
  return {
    setStage: (next: Stage) => { stage = next; },
    submissions: () => submissions,
    requestedDates,
    verify: () => { expect(unexpected).toEqual([]); expect(errors).toEqual([]); },
  };
}

test('stale → queued → partial → complete, page reload reuses the running job', async ({ page }) => {
  const fixture = await isolate(page);
  await page.clock.install({ time: new Date('2026-09-23T01:00:00Z') });
  await page.goto('/market');
  const state = page.getByRole('complementary', { name: '行情更新状态' });
  await expect(state).toContainText('排队中');
  expect(fixture.submissions()).toBe(1);
  expect(fixture.requestedDates[0]).toBeNull();
  await page.reload();
  await expect(state).toContainText('排队中');
  expect(fixture.submissions()).toBe(1);
  await page.getByRole('button', { name: '上证综指 展开 K 线' }).click();
  await expect(page.getByRole('button', { name: '上证综指 收起 K 线' })).toBeVisible();
  await page.getByLabel('榜单日期').fill('2026-08-31');
  await expect(page.getByText('榜单 2026-08-31 · heat_v1')).toBeVisible();
  fixture.setStage('partial');
  await page.clock.runFor(10_100);
  await expect(state).toContainText('7/11');
  fixture.setStage('complete');
  await page.clock.runFor(10_100);
  await expect(state).toContainText('已更新');
  await expect(page.getByRole('button', { name: '上证综指 收起 K 线' })).toBeVisible();
  await expect(page.getByLabel('榜单日期')).toHaveValue('2026-08-31');
  expect(fixture.submissions()).toBe(1);
  fixture.verify();
});

test('failure and offline status preserve the existing chart and history selection', async ({ page }) => {
  const fixture = await isolate(page);
  await page.clock.install({ time: new Date('2026-09-23T01:00:00Z') });
  await page.goto('/market');
  const state = page.getByRole('complementary', { name: '行情更新状态' });
  await expect(state).toContainText('排队中');
  await page.getByRole('button', { name: '上证综指 展开 K 线' }).click();
  const chart = page.locator('[id="chart-CN-000001.SH"]');
  await expect(chart.locator('canvas').first()).toBeVisible();
  await chart.evaluate(element => element.setAttribute('data-retained', 'true'));
  await page.getByLabel('榜单日期').fill('2026-08-31');
  fixture.setStage('failed');
  await page.clock.runFor(20_100);
  await expect(state).toContainText('测试数据源暂不可用');
  await expect(state.getByRole('button', { name: '重试中国指数' })).toBeEnabled();
  fixture.setStage('offline');
  await page.clock.runFor(310_100);
  await expect(state).toContainText('行情补齐暂不可用');
  await expect(chart).toHaveAttribute('data-retained', 'true');
  await expect(chart.locator('canvas').first()).toBeVisible();
  await expect(page.getByLabel('榜单日期')).toHaveValue('2026-08-31');
  expect(fixture.submissions()).toBe(1);
  fixture.verify();
});
