import { defineConfig } from '@playwright/test';

// 本地 loopback 单人应用：仅 chromium，单 worker，首期不并发。
// E2E 场景（任务闭环、市场三视图、自选写后刷新）在 T9 落地。
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    // dev server 默认；compose 运行用 FRONTEND_BASE_URL=http://127.0.0.1:3000 pnpm e2e
    baseURL: process.env.FRONTEND_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
