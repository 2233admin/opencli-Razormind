import { expect, test } from '@playwright/test'

const envelope = data => ({ contentType: 'application/json', body: JSON.stringify({ success: true, data }) })
const image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6HAAAAABJRU5ErkJggg=='

test('操作失败后刷新按钮能恢复，且显示处理中反馈', async ({ page }) => {
  const actions = []
  const account = { id: 'recovery', label: '恢复测试', platform: 'example.org', status: 'unconfirmed', login_url: 'https://example.org' }
  const frame = { image, width: 1280, height: 800, origin: 'https://example.org', title: 'Login', suggested_name: '' }
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) return route.fulfill(envelope({ subject: 'admin', is_platform_admin: true, auth_method: 'bootstrap' }))
    if (path.endsWith('/browser-accounts')) return route.fulfill(envelope({ accounts: [account], available_instances: [] }))
    if (path.endsWith('/input')) {
      actions.push(route.request().postDataJSON().kind)
      if (actions.length === 1) return route.fulfill({ status: 503, ...envelope(null), body: JSON.stringify({ detail: '连接中断' }) })
      await new Promise(resolve => setTimeout(resolve, 400))
      return route.fulfill(envelope(frame))
    }
    if (path.endsWith('/frame') || path.endsWith('/login')) return route.fulfill(envelope(frame))
    return route.fulfill(envelope([]))
  })
  await page.goto('/browser-accounts')
  await page.getByRole('button', { name: '继续登录', exact: true }).click()
  await page.getByRole('img', { name: '实时网站登录画面，可直接扫码' }).click()
  await expect(page.getByRole('alert')).toContainText('后续输入已暂停')
  await page.getByRole('button', { name: '刷新登录画面', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('正在操作网站')
  await expect.poll(() => actions).toEqual(['click', 'reload'])
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '刷新登录画面', exact: true })).toBeEnabled()
})

test('网址登录、内嵌画面操作、保存和真实删除', async ({ page }) => {
  let accounts = []
  const actions = []
  const deletions = []
  const frame = { image, width: 1280, height: 800, title: '网站登录', origin: 'https://example.org', suggested_name: '' }
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/auth/me')) return route.fulfill(envelope({ subject: 'admin', name: 'Admin', is_platform_admin: true, auth_method: 'bootstrap' }))
    if (path.endsWith('/browser-accounts/websites')) return route.fulfill(envelope([{ label: '抖音', url: 'https://www.douyin.com/' }, { label: 'GitHub', url: 'https://github.com/login' }]))
    if (path.endsWith('/browser-accounts') && request.method() === 'GET') return route.fulfill(envelope({ accounts, archived_accounts: [], available_instances: [] }))
    if (path.endsWith('/browser-accounts') && request.method() === 'POST') {
      expect(request.postDataJSON()).toEqual({ site_url: 'example.org/login' })
      const account = { id: String(accounts.length + 1), platform: 'example.org', label: '自动生成的账号', status: 'unconfirmed', login_url: 'https://example.org/login' }
      accounts.push(account)
      return route.fulfill(envelope(account))
    }
    if (path.endsWith('/input')) { actions.push(request.postDataJSON()); return route.fulfill(envelope(frame)) }
    if (path.endsWith('/login') || path.endsWith('/frame')) return route.fulfill(envelope(frame))
    if (path.endsWith('/confirmation')) { accounts[0].status = 'confirmed'; return route.fulfill(envelope(accounts[0])) }
    if (request.method() === 'DELETE') { deletions.push(request.postDataJSON()); accounts = []; return route.fulfill(envelope(null)) }
    return route.fulfill(envelope([]))
  })
  await page.goto('/browser-accounts')
  for (const keep of [false, true]) {
    await page.getByRole('button', { name: '添加账号', exact: true }).click()
    let dialog = page.getByRole('dialog')
    await expect(dialog.getByLabel('账号名称', { exact: true })).toHaveCount(0)
    await expect(dialog.getByLabel('登录环境', { exact: true })).toHaveCount(0)
    await dialog.getByLabel('网站地址', { exact: true }).fill('example.org/login')
    await dialog.getByRole('button', { name: '继续登录', exact: true }).click()
    dialog = page.getByRole('dialog')
    const screen = dialog.getByRole('img', { name: '实时网站登录画面，可直接扫码' })
    await expect(screen).toBeVisible()
    await expect(dialog.getByRole('link')).toHaveCount(0)
    await screen.click({ position: { x: 10, y: 10 } })
    await expect.poll(() => actions.filter(a => a.kind === 'click').length).toBeGreaterThan(0)
    await dialog.getByLabel('发送到登录页的文字').fill('测试输入')
    await dialog.getByRole('button', { name: '输入到页面' }).click()
    await expect.poll(() => actions.some(a => a.text === '测试输入')).toBe(true)
    await dialog.getByRole('button', { name: '已完成登录，保存账号' }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByText('已确认登录', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '删除 自动生成的账号', exact: true }).click()
    if (keep) await page.getByRole('radio', { name: /保留登录数据/ }).check()
    await page.getByRole('button', { name: '确认删除', exact: true }).click()
    await expect(page.getByText('登录你的第一个账号', { exact: true })).toBeVisible()
  }
  expect(deletions).toEqual([{ clear_login_data: true }, { clear_login_data: false }])
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '添加账号', exact: true }).click()
  await page.getByLabel('网站地址', { exact: true }).fill('example.org/login')
  await page.getByRole('button', { name: '继续登录', exact: true }).click()
  await expect(page.getByRole('img', { name: '实时网站登录画面，可直接扫码' })).toBeVisible()
  const bounds = await page.getByRole('dialog').boundingBox()
  expect(bounds.y).toBeGreaterThanOrEqual(0)
  expect(bounds.height).toBeLessThanOrEqual(844)
  await page.getByRole('button', { name: '已完成登录，保存账号' }).scrollIntoViewIfNeeded()
  await expect(page.getByRole('button', { name: '已完成登录，保存账号' })).toBeInViewport()
})
