import { expect, test } from '@playwright/test';

// 本地冒烟（对已运行的前端 + 后端）：页面可达、区块结构、看板无 SSE、404 专用态。
// 运行：FRONTEND_BASE_URL=http://127.0.0.1:3000 pnpm e2e（compose）或默认 dev server。

test.describe('页面冒烟', () => {
  test('大盘：市场/板块/信息三固定区块', async ({ page }) => {
    await page.goto('/market');
    await expect(page.getByRole('heading', { name: '市场' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '板块' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '信息' })).toBeVisible();
    // 无页内 Tab
    await expect(page.getByRole('tab')).toHaveCount(0);
  });

  test('自选：分组与组合两区', async ({ page }) => {
    await page.goto('/watchlist');
    await expect(page.getByText('自选分组与标的')).toBeVisible();
    await expect(page.getByText('手工组合与持仓')).toBeVisible();
    await expect(page.getByText(/首期不会将分析报告中的建议仓位/)).toBeVisible();
  });

  test('AI 看板：不请求 SSE events 端点', async ({ page }) => {
    const eventRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/events')) eventRequests.push(request.url());
    });
    await page.goto('/ai');
    await expect(page.getByRole('heading', { name: 'AI 投研看板' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(eventRequests).toHaveLength(0);
  });

  test('任务中心：状态筛选可见，页面无创建表单', async ({ page }) => {
    await page.goto('/ai/tasks');
    await expect(page.getByRole('heading', { name: 'AI 任务中心' })).toBeVisible();
    for (const label of ['全部', '进行中', '成功', '失败', '已取消']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByLabel('股票代码')).toHaveCount(0);
  });

  test('事件研究：表单可见，无任务跳转', async ({ page }) => {
    await page.goto('/ai/event-study');
    await expect(page.getByLabel('事件文本')).toBeVisible();
    await expect(page.getByLabel('目标资产')).toBeVisible();
  });

  test('资源 404：显示专用不存在态与返回入口', async ({ page }) => {
    await page.goto('/ai/tasks/not-exist-task-id');
    await expect(page.getByRole('heading', { name: '资源不存在' })).toBeVisible();
    await expect(page.getByRole('link', { name: '返回 AI 投研看板' })).toBeVisible();
  });

  test('未匹配路由：NotFoundState 提供返回入口', async ({ page }) => {
    await page.goto('/no-such-page');
    await expect(page.getByRole('heading', { name: '资源不存在' })).toBeVisible();
    await expect(page.getByRole('link', { name: '返回大盘' })).toBeVisible();
  });
});
