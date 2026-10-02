import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const projectRoot = resolve(frontendRoot, '..');
const testsRoot = resolve(projectRoot, 'tests').replaceAll('\\', '/');

export default defineConfig({
  root: projectRoot,
  cacheDir: resolve(frontendRoot, 'node_modules/.vite-vitest'),
  plugins: [
    {
      name: 'central-test-dependencies',
      enforce: 'pre',
      async resolveId(source, importer) {
        if (importer && importer.replaceAll('\\', '/').startsWith(testsRoot + '/')
          && !source.startsWith('.') && !source.startsWith('/') && !source.startsWith('\0')
          && !source.startsWith('node:') && !/^[a-z]:/i.test(source)) {
          return this.resolve(source, resolve(frontendRoot, 'package.json'), { skipSelf: true });
        }
        return null;
      },
    },
    react(),
  ],
  test: {
    include: ['tests/frontend/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    globals: true,
    setupFiles: ['tests/support/frontend/setup.ts'],
    css: false,
  },
});
