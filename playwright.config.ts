import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:3000';
const localWebServerURL = process.env.PLAYWRIGHT_LOCAL_WEB_SERVER_URL ?? 'http://127.0.0.1:3000';
const useLocalWebServer = process.env.PLAYWRIGHT_LOCAL_WEB_SERVER === 'true';

export default defineConfig({
  testDir: '.',
  testMatch: ['apps/web/tests/**/*.spec.ts'],
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL,
    trace: 'on-first-retry'
  },
  webServer: useLocalWebServer
    ? {
        command: 'make run-web-smoke',
        url: `${localWebServerURL.replace(/\/$/, '')}/api/health`,
        reuseExistingServer: false,
        timeout: 120_000,
        stdout: 'pipe',
        stderr: 'pipe'
      }
    : undefined,
  reporter: [['list']]
});
