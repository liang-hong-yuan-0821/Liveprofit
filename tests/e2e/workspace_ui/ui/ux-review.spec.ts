// test-catalog-begin
// {
//   "purpose": "界面适配 / ux-review：market ${width} ${theme}；mobile navigation restores focus and honors reduced motion；real UUID task stays within mobile viewport and report precedes diagnostics",
//   "keywords": [
//     "界面适配",
//     "市场分析",
//     "选择范围",
//     "任务",
//     "ux-review",
//     "market",
//     "selection",
//     "task"
//   ],
//   "covers": [
//     "frontend/src/modules/analysis/pages/task-detail/AiTaskDetailPage.tsx",
//     "frontend/src/modules/event-study/pages/EventStudyPage.tsx",
//     "frontend/src/modules/market/pages/MarketOverviewPage.tsx"
//   ],
//   "environment": [
//     "browser",
//     "app"
//   ]
// }
// test-catalog-end

/// <reference lib="dom" />
import { test, expect, type Page } from '@playwright/test';
test.use({ channel: process.env.PLAYWRIGHT_CHANNEL });
const taskId = '49ba0ed3-e11f-4e40-bf73-e7307da5d15e';
const bars = Array.from({ length: 60 }, (_, i) => ({ timestamp: new Date(Date.UTC(2026, 6, i + 1)).toISOString(), open: 100 + i, close: 102 + i, low: 98 + i, high: 104 + i, volume: 1000 }));
const nodes = Array.from({ length: 14 }, (_, i) => ({ id: `market:node-${i}`, label: i === 2 ? 'An exceptionally long investment research analyst label with many words that must not overlap adjacent nodes' : `Research Analyst ${i}`, layer: 'market', row: i % 2, order: i, has_prompt: false, has_override: false }));
const drafts = [1, 2].map(id => ({ draft_id: id, title: id === 1 ? '<b>半导体政策事件</b>' : '消费市场事件', source: '测试数据', announced_at: '2026-09-21T10:00:00Z', content: '政策详情\n![远程图片](https://example.com/never.png)', source_url: 'https://example.com/news', importance_hint: 3, ai_suggestions: { event_type: '政策', event_scope: 'market', importance: 3 } }));
async function isolate(page: Page) {
  const unexpected: string[] = []; const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    let data: unknown;
    if (path.includes('/market-data/indices/') && path.endsWith('/bars')) data = { asset: {}, interval: '1d', from: url.searchParams.get('from'), to: url.searchParams.get('to'), bars, source: 'isolated-fixture', as_of: '2026-08-29', source_updated_at: null, freshness_status: 'STALE', market_session_status: 'CLOSED', market_closed_reason: '收盘' };
    else if (path.includes('/market-data/trends/')) data = { series: [], as_of: null, freshness_status: 'UNAVAILABLE', result_status: 'EMPTY' };
    else if (path.endsWith('/market-data/concepts/tree')) data = { items: [], as_of: '2026-09-22', freshness_status: 'UNAVAILABLE', algorithm_version: 'heat_v1' };
    else if (path.endsWith('/macro-information')) data = { items: [] };
    else if (path.endsWith('/analysis-dashboard')) data = { pending_actions: [], active_tasks: [], recent_conclusions: [], generated_at: '2026-09-22T00:00:00Z' };
    else if (path.endsWith('/agents/topology')) data = { nodes, edges: [], generated_at: '2026-09-22T00:00:00Z' };
    else if (path === `/api/v1/analysis-tasks/${taskId}`) data = { id: taskId, task_type: 'MARKET_WIDE', ticker: null, selected_layers: ['market'], requested_trade_date: '2026-09-21', effective_trade_date: '2026-09-21', status: 'SUCCEEDED', attempt_no: 1, created_at: '', updated_at: '', events_url: '', report_url: '', date_correction: null };
    else if (path === `/api/v1/analysis-tasks/${taskId}/report`) data = { schema_version: 'v1', report_version: 1, generated_at: '2026-09-22T00:00:00Z', task: { task_id: taskId, task_type: 'MARKET_WIDE' }, sections: [{ block: 'market', status: 'AVAILABLE', title: '市场分析', summary: '测试结论', content: '测试正文' }], data_sources: [], risk_note: null, quant_execution: null };
    else if (path.endsWith('/event-studies/assets')) data = { items: [{ ticker: '000001.SZ', name: '平安银行', market: 'CN' }] };
    else if (path.endsWith('/review/pending-events')) data = { items: drafts };
    else if (path.endsWith('/review/refresh') && request.method() === 'POST') data = { fetched: 0, new_drafts: 0, skipped_reason: null };
    else { unexpected.push(`${request.method()} ${path}`); await route.abort('blockedbyclient'); return; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data, meta: { request_id: 'isolated-ui-test' } }) });
  });
  return () => { expect(unexpected).toEqual([]); expect(errors).toEqual([]); };
}
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true); }
for (const [width, height] of [[1440,900], [1280,720], [1024,768], [390,844]]) for (const theme of ['dark', 'light']) {
  test(`market ${width} ${theme}`, async ({ page }) => {
    const verify = await isolate(page); await page.setViewportSize({ width, height });
    await page.addInitScript(mode => localStorage.setItem('liveprofit-ui-preferences', JSON.stringify({ state: { themeMode: mode, sidebarCollapsed: false }, version: 0 })), theme);
    await page.goto('/market'); await expect(page.getByRole('heading', { name: '市场全景' })).toBeVisible();
    await expect(page.getByRole('button', { name: /展开 K 线/ })).toHaveCount(11); await noOverflow(page);
    await page.getByRole('button', { name: '标普500 展开 K 线' }).click(); await expect(page.getByRole('button', { name: '标普500 收起 K 线' })).toBeVisible();
    await expect(page.getByText('来源 isolated-fixture', { exact: false })).toBeVisible(); await noOverflow(page);
    await page.screenshot({ path: `test-results/ux-market-${width}-${theme}.png` }); verify();
  });
}
test('mobile navigation restores focus and honors reduced motion', async ({ page }) => {
  const verify = await isolate(page); await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ reducedMotion: 'reduce' }); await page.goto('/market');
  const trigger = page.getByRole('button', { name: '打开导航菜单' }); await trigger.click(); await expect(page.getByRole('dialog')).toBeVisible(); await page.keyboard.press('Escape'); await expect(trigger).toBeFocused();
  expect(await page.locator('.app-main > *').evaluate(element => getComputedStyle(element).animationName)).toBe('none'); verify();
});
test('real UUID task stays within mobile viewport and report precedes diagnostics', async ({ page }) => {
  const verify = await isolate(page); await page.setViewportSize({ width: 390, height: 844 }); await page.goto(`/ai/tasks/${taskId}#report`);
  await expect(page.getByText('最新报告', { exact: true })).toBeVisible(); await page.getByText(/任务信息 ·/).click(); await expect(page.getByText(taskId, { exact: true })).toBeVisible(); await noOverflow(page);
  await expect(page.getByTestId('execution-logs-panel')).toHaveCount(0); verify();
});
test('wide topology scrolls internally and has accessible full node names', async ({ page }) => {
  const verify = await isolate(page); await page.setViewportSize({ width: 1024, height: 768 }); await page.goto('/ai?tab=agents');
  await expect(page.getByTestId('agent-topology-chart')).toBeVisible(); await noOverflow(page);
  expect(await page.getByTestId('agent-topology-chart').evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
  await page.getByText('按层查看全部节点').click(); await expect(page.getByRole('button', { name: /An exceptionally long/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/ux-topology.png' }); verify();
});
test('review edits one draft, preserves hidden selection and never loads remote images', async ({ page }) => {
  const verify = await isolate(page); let remoteImages = 0; await page.route('https://example.com/never.png', route => { remoteImages++; return route.abort(); });
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/event-study?tab=review');
  await expect(page.getByText('没有新事件（各源最新快讯均已存在）')).toBeVisible();
  await page.getByLabel('第1行-查看原文').click(); await expect(page.getByRole('dialog')).toBeVisible(); await page.getByLabel('事件类型', { exact: true }).fill('已修改分类'); await page.getByRole('combobox', { name: '操作', exact: true }).selectOption('approve'); await page.getByRole('button', { name: '返回列表' }).click();
  await page.getByLabel('搜索当前列表').fill('消费'); await expect(page.getByText(/筛选隐藏已选 1 条/)).toBeVisible();
  await page.getByRole('button', { name: /批量提交/ }).click(); await expect(page.getByRole('dialog')).toContainText('包含当前筛选隐藏的 1 条');
  await page.keyboard.press('Escape'); await noOverflow(page); expect(remoteImages).toBe(0); verify();
});
