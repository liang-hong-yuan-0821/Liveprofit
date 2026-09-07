import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { configDefaults, defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // 开发期代理 /api 到本地后端（非容器开发模式 API 监听 127.0.0.1:8000）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/testSetup.ts'],
    css: false,
    // E2E 由 Playwright 执行，排除其 spec 文件
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
});
