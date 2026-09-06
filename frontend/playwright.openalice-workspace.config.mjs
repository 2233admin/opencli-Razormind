import { defineConfig } from '@playwright/test'

const frontendPort = process.env.OPENALICE_FRONTEND_PORT ?? '8049'
const backendPort = process.env.OPENALICE_BACKEND_PORT ?? '8048'
const webServer = process.env.OPENALICE_REUSE_SERVERS === '1' ? undefined : [
  {
    command: 'node e2e/openalice-workspace-support/start-backend.mjs',
    url: `http://127.0.0.1:${backendPort}/health`,
    timeout: 60_000,
    reuseExistingServer: false,
  },
  {
    command: 'node e2e/openalice-workspace-support/start-frontend.mjs',
    url: `http://127.0.0.1:${frontendPort}`,
    timeout: 120_000,
    reuseExistingServer: false,
    env: {
      BACKEND_URL: `http://127.0.0.1:${backendPort}`,
      NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV: 'false',
      OPENALICE_FRONTEND_PORT: frontendPort,
      OPENCLI_NEXT_DIST_DIR: '.next-alice-chat-e2e',
    },
  },
]

export default defineConfig({
  testDir: './e2e',
  testMatch: process.env.OPENALICE_CHAT_E2E === '1'
    ? /openalice-chat-workspace\.spec\.mjs/
    : /openalice-workspace\.spec\.mjs/,
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report/openalice-workspace', open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer,
})
