import { expect, test } from '@playwright/test'

test('work views survive reload, close independently and stay inside their workspace', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (path.endsWith('/auth/me')) return reply({ subject: 'tabs-user', auth_method: 'test', name: 'Tester', is_platform_admin: true })
    if (path.endsWith('/workspaces')) return reply([{ id: 'w', name: 'Research', active: true }, { id: 'other', name: 'Other', active: true }])
    if (path.endsWith('/projects') || path.endsWith('/workflows') || path.endsWith('/chat/sessions')) return reply([])
    return reply({})
  })
  await page.goto('/studio/projects/p?workspace=w')
  const tabs = page.getByRole('navigation', { name: '工作标签', exact: true })
  await expect(tabs.getByRole('link')).toHaveCount(1)
  await page.goto('/studio/projects/p/operations?workspace=w&workflow=f&run=r&trace=t')
  await expect(tabs.getByRole('link')).toHaveCount(2)
  await page.reload()
  await expect(tabs.getByRole('link')).toHaveCount(2)
  await tabs.getByRole('link', { name: '项目 · p', exact: true }).click()
  await expect(page).toHaveURL(/\/studio\/projects\/p\?/)
  await expect(tabs.getByRole('link', { name: '项目 · p', exact: true })).toHaveAttribute('aria-current', 'page')
  await tabs.getByRole('button', { name: '关闭工作标签：项目 · p', exact: true }).click()
  await expect(page).toHaveURL(/\/operations\?/)
  await expect(tabs.getByRole('link')).toHaveCount(1)
  expect(new URL(page.url()).searchParams.get('run')).toBe('r')
  expect(new URL(page.url()).searchParams.get('trace')).toBe('t')
  await page.goBack()
  await expect(tabs.getByRole('link')).toHaveCount(1)
  await expect(tabs.getByRole('link', { name: '项目 · p', exact: true })).toHaveCount(0)
  await page.goto('/studio?workspace=other')
  await expect(tabs).toHaveCount(0)
  await page.goto('/studio?workspace=w')
  await expect(tabs.getByRole('link')).toHaveCount(1)
  await tabs.getByRole('button', { name: '关闭工作标签：运行 · r', exact: true }).click()
  await expect(tabs).toHaveCount(0)
  await page.reload()
  await expect(tabs).toHaveCount(0)
  expect(errors).toEqual([])
})

test('an Agent deep link waits for identity recovery before opening the dock', async ({ page }) => {
  let releaseIdentity
  const identityReady = new Promise((resolve) => { releaseIdentity = resolve })
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (path.endsWith('/auth/me')) {
      await identityReady
      return reply({ subject: 'tabs-user', auth_method: 'test', name: 'Tester', is_platform_admin: true })
    }
    if (path.endsWith('/workspaces')) return reply([{ id: 'w', name: 'Research', active: true }])
    if (path.endsWith('/projects') || path.endsWith('/chat/sessions')) return reply([])
    return reply({})
  })
  await page.goto('/studio?workspace=w&agent=1')
  await expect(page.getByLabel('给全局 Agent 的消息')).toHaveCount(0)
  releaseIdentity()
  await expect(page.getByLabel('给全局 Agent 的消息')).toBeVisible()
})
