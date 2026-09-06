import { defineConfig } from '@playwright/test'
import connectorConfig from './playwright.feishu-connectors.config.mjs'

const frontendPort = process.env.OPENALICE_FRONTEND_PORT ?? '8053'

export default defineConfig({
  ...connectorConfig,
  testMatch: 'connector-artifact-grants.spec.mjs',
  timeout: 120_000,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report/connector-artifacts', open: 'never' }]],
  use: { ...connectorConfig.use, baseURL: `http://127.0.0.1:${frontendPort}` },
  webServer: connectorConfig.webServer.map((server, index) => ({
    ...server,
    ...(index === 1 ? { url: `http://127.0.0.1:${frontendPort}` } : {}),
    env: {
      ...server.env,
      OPENALICE_CONNECTOR_E2E: '0',
      OPENALICE_CONNECTOR_P2_E2E: '1',
      OPENALICE_FRONTEND_PORT: frontendPort,
      OPENCLI_NEXT_DIST_DIR: process.env.OPENCLI_NEXT_DIST_DIR ?? '.next-connector-artifacts-e2e',
    },
  })),
})
