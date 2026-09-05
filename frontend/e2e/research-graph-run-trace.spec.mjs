import { expect, test } from '@playwright/test'

const studioUrl = process.env.STUDIO_TRACER_URL
const bootstrapToken = process.env.STUDIO_TRACER_BOOTSTRAP_TOKEN


test('Studio Run Trace advances a proposed research claim through real graph mutations', async ({ page }) => {
  test.setTimeout(90_000)

  test.skip(
    !studioUrl || !bootstrapToken,
    'STUDIO_TRACER_URL and STUDIO_TRACER_BOOTSTRAP_TOKEN provision the isolated Studio stack',
  )

  await page.goto(studioUrl)
  await page.waitForTimeout(3_000)
  const bootstrapLogin = page.getByPlaceholder('BOOTSTRAP_ADMIN_TOKEN')
  if (await bootstrapLogin.isVisible()) {
    await bootstrapLogin.fill(bootstrapToken)
    await page.getByRole('button', { name: '使用管理员令牌登录', exact: true }).click()
    await page.waitForTimeout(1_000)
    await page.goto(studioUrl)
  }
  const runTrace = page.getByLabel('运行追踪')
  const run = page.getByRole('button', { name: '运行', exact: true })
  await expect(run).toBeVisible({ timeout: 30_000 })
  await run.click()
  await expect(runTrace).toBeVisible()
  await expect(runTrace.getByText('成功', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(3_000)
  await expect(runTrace.getByText('Workflow evidence batches failed (404)', { exact: true })).toHaveCount(0)

  await runTrace.getByPlaceholder('Proposal title').fill('Browser proposal')
  await runTrace.getByPlaceholder('Source ID').fill('source:studio-deterministic-source')
  await runTrace.getByPlaceholder('Evidence ID').fill('evidence:synthetic-funding-support')
  await runTrace.getByRole('button', { name: 'Propose claim', exact: true }).click()

  await expect(runTrace.getByText('Browser proposal', { exact: true })).toBeVisible()
  await expect(runTrace.getByRole('button', { name: 'Verify', exact: true })).toBeVisible()
  await expect(runTrace.getByRole('button', { name: 'Reject', exact: true })).toBeVisible()

  await runTrace.getByRole('button', { name: 'Verify', exact: true }).click()
  await expect(runTrace.getByText('verified', { exact: true })).toBeVisible()
  await expect(runTrace.getByRole('button', { name: 'Retract', exact: true })).toBeVisible()

  await runTrace.getByRole('button', { name: 'Retract', exact: true }).click()
  await expect(runTrace.getByText('retracted', { exact: true })).toBeVisible()
})
