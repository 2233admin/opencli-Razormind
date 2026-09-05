import { expect, test } from '@playwright/test'

const envelope = (data) => ({ success: true, data, meta: { total: data.length ?? 0, page: 1, pages: 1, limit: 100 } })
const json = (data) => ({ contentType: 'application/json', body: JSON.stringify(envelope(data)) })
async function fixtures(page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'smoothui-feedback-test')
  })
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname.endsWith('/auth/me')) return route.fulfill(json({ subject: 'feedback-test', name: 'Feedback Test', is_platform_admin: true, auth_method: 'bootstrap' }))
    if (url.pathname.endsWith('/system/config')) return route.fulfill(json({ app_name: 'OpenCLI Admin', api_auth_configured: true, collection_mode: 'local' }))
    if (url.pathname.endsWith('/control/kill-switch')) return route.fulfill(json({ engaged: false }))
    return route.fulfill(json([]))
  })
}

test('API copy has truthful pending, failed and successful states with keyboard and reduced motion', async ({ page }) => {
  test.setTimeout(60_000)
  await fixtures(page)
  await page.addInitScript(() => {
    globalThis.__clipboardWrites = []
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText(text) {
        globalThis.__clipboardWrites.push(text)
        return new Promise((resolve, reject) => { globalThis.__clipboardFinish = { resolve, reject } })
      },
    } })
  })
  await page.goto('/studio/projects/feedback-test/api')
  const button = page.getByRole('button', { name: '复制 MCP 地址', exact: true })
  await expect(button).toBeEnabled()
  await button.focus()
  await page.keyboard.press('Enter')
  const pending = page.getByRole('button', { name: 'MCP 地址：正在复制…', exact: true })
  await expect(pending).toBeDisabled()
  await expect(pending).toHaveAttribute('aria-busy', 'true')
  await page.keyboard.press('Enter')
  expect(await page.evaluate(() => globalThis.__clipboardWrites)).toEqual([`${new URL(page.url()).origin}/mcp`])
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await pending.locator('svg').evaluateAll((icons) => icons.every((icon) => getComputedStyle(icon).animationName === 'none'))).toBe(true)
  await page.evaluate(() => globalThis.__clipboardFinish.reject(new Error('denied')))
  const retry = page.getByRole('button', { name: 'MCP 地址：复制失败，重试', exact: true })
  await expect(retry).toBeEnabled()
  await expect(page.getByText('复制 MCP 地址失败，可手动选择页面中的地址或代码进行复制', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'MCP 地址：已复制', exact: true })).toHaveCount(0)
  await retry.click()
  await expect(pending).toBeDisabled()
  await page.evaluate(() => globalThis.__clipboardFinish.resolve())
  await expect(page.getByRole('button', { name: 'MCP 地址：已复制', exact: true })).toBeVisible()
  expect(await page.evaluate(() => globalThis.__clipboardWrites.length)).toBe(2)
  await expect(button).toBeEnabled()
})
