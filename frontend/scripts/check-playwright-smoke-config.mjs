import assert from 'node:assert/strict'
import { test } from 'node:test'

const config = (await import('../playwright.config.mjs')).default
const expectedUrl = 'http://127.0.0.1:3101'

test('Playwright smoke uses a dedicated managed server by default', () => {
  assert.equal(config.use.baseURL, expectedUrl)
  assert.equal(config.webServer.url, expectedUrl)
  assert.equal(config.webServer.command, 'node scripts/start-standalone.mjs')
  assert.equal(config.webServer.env.PORT, '3101')
  assert.equal(config.webServer.reuseExistingServer, false)
})

test('Playwright smoke only reuses an explicitly requested server', async () => {
  const previous = process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER
  process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER = '1'
  try {
    const reuseConfig = (await import(`../playwright.config.mjs?reuse=${Date.now()}`)).default
    assert.equal(reuseConfig.webServer.reuseExistingServer, true)
  } finally {
    if (previous === undefined) delete process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER
    else process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER = previous
  }
})

test('Playwright smoke rejects invalid port input before constructing the command', async () => {
  const previous = process.env.PLAYWRIGHT_SMOKE_PORT
  process.env.PLAYWRIGHT_SMOKE_PORT = '3101 && injected-command'
  try {
    await assert.rejects(
      import(`../playwright.config.mjs?invalid-port=${Date.now()}`),
      /PLAYWRIGHT_SMOKE_PORT must be an integer from 1 through 65535/,
    )
  } finally {
    if (previous === undefined) delete process.env.PLAYWRIGHT_SMOKE_PORT
    else process.env.PLAYWRIGHT_SMOKE_PORT = previous
  }
})
