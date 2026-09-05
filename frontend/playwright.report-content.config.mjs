import { defineConfig } from '@playwright/test'

const port = process.env.REPORT_CONTENT_PORT ?? '8043'
if (!/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535) throw new Error('Invalid report test port')
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  testMatch: 'report-content.spec.mjs',
  workers: 1,
  use: { baseURL, browserName: 'chromium' },
  webServer: {
    command: `node node_modules/next/dist/bin/next dev e2e/report-content-harness --webpack --hostname 127.0.0.1 --port ${port}`,
    url: baseURL,
    timeout: 60_000,
    reuseExistingServer: false,
  },
})
