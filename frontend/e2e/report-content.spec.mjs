import { expect, test } from "@playwright/test"

test("actual report viewer sanitizes hostile Markdown and keeps HTML isolated", async ({ page, baseURL }) => {
  const requests = []
  const intercepted = []
  const failures = []
  page.on("request", (request) => requests.push(request.url()))
  page.on('requestfailed', (request) => failures.push({ url: request.url(), error: request.failure()?.errorText }))
  await page.route('https://outside.invalid/**', (route) => { intercepted.push(route.request().url()); return route.abort() })
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: baseURL,
  })
  const response = await page.goto('/')
  expect(response.status()).toBe(200)
  const markdown = page.getByTestId("report-content-markdown")
  await expect(markdown.getByRole("heading", { name: "Hostile report" })).toBeVisible()
  await expect(markdown.locator("form, style, script, iframe, input, audio, video, svg")).toHaveCount(0)
  await expect(markdown.locator('a[href^="javascript:"]')).toHaveCount(0)
  await expect(markdown.locator('a[href="https://example.com/source"]')).toHaveAttribute("target", "_blank")

  await markdown.getByRole("button", { name: /copy javascript code/i }).click()
  await expect(markdown.locator("[data-copy-report-code]")).toHaveText(/copied/i)
  expect((await page.evaluate(() => navigator.clipboard.readText())).replaceAll('\r\n', '\n')).toBe('const answer = 42\n')

  const frame = page.getByTestId("report-content-html").locator("iframe")
  await expect(frame).toHaveAttribute("sandbox", "")
  await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
  await expect(frame.contentFrame().getByRole("heading", { name: "Safe report" })).toBeVisible()
  await expect(frame.contentFrame().locator("form, input, script")).toHaveCount(0)
  await expect(frame.contentFrame().locator('a')).not.toHaveAttribute('href')
  await expect(page.getByTestId("report-content-table").locator("th")).toHaveText(["name", "count", "note"])
  await expect(page.getByTestId("report-content-table").locator("[data-report-kind=table]")).toHaveCSS("overflow-x", "auto")
  expect(await page.evaluate(() => window.reportScriptRan === true)).toBe(false)
  // Chromium emits request events for CSS resources that CSP blocks before
  // network interception. Every attempted resource must fail the CSP check.
  expect(intercepted).toEqual([])
  for (const url of requests.filter((value) => value.includes('outside.invalid'))) {
    expect(failures).toContainEqual({ url, error: 'csp' })
  }
})

test('CSV, TSV and object columns retain their actual data', async ({ page }) => {
  await page.goto('/')
  const csv = page.getByTestId('report-content-csv')
  await expect(csv.locator('th')).toHaveText(['id', 'note'])
  await expect(csv.locator('td')).toHaveText(['001', 'comma, tab\t and\nnewline'])
  await expect(page.getByTestId('report-content-tsv').locator('td')).toHaveText(['002', 'comma, kept'])
  await expect(page.getByTestId('report-content-table').locator('td')).toHaveText(['first', '1', '', 'second', '2', 'new column'])
})

test('the reader remains usable on a narrow screen and exposes fallback states', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Hostile report' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  for (const state of ['loading', 'empty', 'error', 'unsupported']) {
    await expect(page.locator(`[data-report-state="${state}"]`)).toBeVisible()
  }
})
