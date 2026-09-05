import { defineConfig } from '@playwright/test'
import workspaceConfig from './playwright.openalice-workspace.config.mjs'

const frontendPort = process.env.OPENALICE_FRONTEND_PORT ?? '8051'

export default defineConfig({
  ...workspaceConfig,
  testDir: './e2e',
  testMatch: 'feishu-connector-settings.spec.mjs',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report/feishu-connectors', open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: workspaceConfig.webServer.map((server, index) => ({
    ...server,
    ...(index === 1 ? { url: `http://127.0.0.1:${frontendPort}` } : {}),
    env: {
      ...server.env,
      OPENALICE_CONNECTOR_E2E: '1',
      OPENALICE_FRONTEND_PORT: frontendPort,
      OPENCLI_NEXT_DIST_DIR: process.env.OPENCLI_NEXT_DIST_DIR ?? '.next-connector-e2e',
    },
  })),
})
