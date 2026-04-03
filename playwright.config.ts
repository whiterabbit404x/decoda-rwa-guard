import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:3000';
const useLocalWebServer =
  process.env.PLAYWRIGHT_LOCAL_WEB_SERVER === 'true' &&
  /^https?:\/\/(127\.0\.0\.1|localhost):3000\/?$/.test(baseURL);

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
        command: 'npm run start --workspace apps/web',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 60_000
      }
    : undefined,
  reporter: [['list']]
});
