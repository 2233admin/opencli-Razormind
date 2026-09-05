import { expect, test } from "@playwright/test"

const harnessUrl = process.env.REPORT_CONTENT_HARNESS_URL ?? "http://127.0.0.1:8042/report-content"

test("actual report viewer sanitizes hostile Markdown and keeps HTML isolated", async ({ page }) => {
  const requests = []
  await page.on("request", (request) => requests.push(request.url()))
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: new URL(harnessUrl).origin,
  })
  await page.goto(harnessUrl)
  const markdown = page.getByTestId("report-content-markdown")
  await expect(markdown.getByRole("heading", { name: "Hostile report" })).toBeVisible()
  await expect(markdown.locator("form, style, script, iframe, input")).toHaveCount(0)
  await expect(markdown.locator('a[href^="javascript:"]')).toHaveCount(0)
  await expect(markdown.locator('a[href="https://example.com/source"]')).toHaveAttribute("target", "_blank")

  await markdown.getByRole("button", { name: /copy javascript code/i }).click()
  await expect(markdown.locator("[data-copy-report-code]")).toHaveText(/copied/i)

  const frame = page.getByTestId("report-content-html").locator("iframe")
  await expect(frame).toHaveAttribute("sandbox", "")
  await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
  await expect(frame.contentFrame().getByRole("heading", { name: "Safe report" })).toBeVisible()
  await expect(frame.contentFrame().locator("form, input, script")).toHaveCount(0)
  await expect(page.getByTestId("report-content-table").locator("th")).toHaveText(["name", "count", "note"])
  await expect(page.getByTestId("report-content-table").locator("[data-report-kind=table]")).toHaveCSS("overflow-x", "auto")
  expect(await page.evaluate(() => window.reportScriptRan === true)).toBe(false)
  expect(requests.some((url) => url.includes("outside.invalid"))).toBe(false)
})
