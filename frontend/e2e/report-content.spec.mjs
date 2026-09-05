import { expect, test } from "@playwright/test"

test("static HTML report preview keeps active content inside a sandbox", async ({ page }) => {
  const requests = []
  await page.on("request", (request) => requests.push(request.url()))
  await page.setContent('<main><iframe id="report" title="HTML report preview" sandbox="" referrerpolicy="no-referrer"></iframe></main>')
  await page.locator("#report").evaluate((frame, srcdoc) => {
    frame.srcdoc = srcdoc
  }, "<!doctype html><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:\"><style>body{overflow-wrap:anywhere}</style><p>&lt;script&gt;blocked&lt;/script&gt;</p><h1>Safe report</h1>")

  const frame = page.locator("#report")
  await expect(frame).toHaveAttribute("sandbox", "")
  await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
  await expect(frame.contentFrame().getByRole("heading", { name: "Safe report" })).toBeVisible()
  await expect(frame.contentFrame().locator("form, input, script")).toHaveCount(0)
  expect(await page.evaluate(() => window.reportScriptRan === true)).toBe(false)
  expect(requests.some((url) => url.includes("outside.invalid"))).toBe(false)
})
