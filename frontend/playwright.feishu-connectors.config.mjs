import { defineConfig } from '@playwright/test'

const frontendUrl = process.env.OPENALICE_CONNECTOR_FRONTEND_URL ?? 'http://127.0.0.1:8051'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'feishu-connector-settings.spec.mjs',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report/feishu-connectors', open: 'never' }]],
  use: {
    baseURL: frontendUrl,
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
})
