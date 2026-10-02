// Central tests use the repository root while dependencies stay in frontend.
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const projectRoot = resolve(frontendRoot, '..');
const [tool, ...args] = process.argv.slice(2);
const entries = {
  vitest: ['node_modules/vitest/vitest.mjs', 'vitest.config.ts'],
  playwright: ['node_modules/@playwright/test/cli.js', 'playwright.config.ts'],
};
if (!Object.hasOwn(entries, tool)) throw new Error('Expected vitest or playwright.');
const [entry, config] = entries[tool];
const cli = resolve(frontendRoot, entry);
process.chdir(projectRoot);
process.argv = [process.execPath, cli, ...args, '--config', resolve(frontendRoot, config)];
await import(pathToFileURL(cli).href);
