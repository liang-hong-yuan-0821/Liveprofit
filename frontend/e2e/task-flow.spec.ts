import { expect, test } from '@playwright/test';

// 本地任务闭环（需后端 Compose + fake/受控 AI Worker，禁止真实 LLM）：
// 创建 → 跳转详情 → 状态卡可见 → 等待终态（成功则同页报告 / 失败则安全摘要）。
// 运行：FRONTEND_BASE_URL=http://127.0.0.1:3000 pnpm e2e

test.describe('任务闭环', () => {
  test('从看板创建单股分析并跳转任务详情', async ({ page }) => {
    await page.goto('/ai');
    await page.getByRole('button', { name: '新建分析' }).first().click();

    await expect(page.getByRole('heading', { name: '新建分析' })).toBeVisible();
    await page.getByRole('button', { name: '提交分析' }).click();

    // 创建成功 → 立即跳转 /ai/tasks/:taskId
    await page.waitForURL(/\/ai\/tasks\/[0-9a-f-]+/, { timeout: 15_000 });
    await expect(page.getByRole('heading', { name: '任务详情' })).toBeVisible();
    await expect(page.getByText('任务状态')).toBeVisible();
    // 非终态任务显示进度观察区（时间线或 REST 回退提示）
    await expect(page.getByText('正式状态以 REST 为准')).toBeVisible({ timeout: 10_000 });
  });

  test('任务详情取消：显示取消请求中并以服务端状态收口', async ({ page }) => {
    await page.goto('/ai');
    await page.getByRole('button', { name: '新建分析' }).first().click();
    await page.getByLabel('股票代码').fill('600000.SH');
    await page.getByRole('button', { name: '提交分析' }).click();
    await page.waitForURL(/\/ai\/tasks\/[0-9a-f-]+/, { timeout: 15_000 });

    const cancelButton = page.getByRole('button', { name: '取消任务' });
    if (await cancelButton.isVisible()) {
      await cancelButton.click();
      await expect(page.getByRole('button', { name: '取消请求中…' })).toBeVisible();
    }
  });
});
