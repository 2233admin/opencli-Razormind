import { expect, test } from '@playwright/test'

const TOKEN = process.env.OPENALICE_CONNECTOR_E2E_TOKEN ?? 'openalice-e2e-token'
const WORKSPACE_ID = process.env.OPENALICE_CONNECTOR_E2E_WORKSPACE ?? 'openalice-e2e-workspace'
const OTHER_WORKSPACE_ID = process.env.OPENALICE_CONNECTOR_E2E_OTHER_WORKSPACE ?? ''
const REQUIRE_ACTIVE_BINDING = process.env.OPENALICE_CONNECTOR_EXPECT_BINDING === '1'

test.describe.configure({ mode: 'serial' })

async function apiJson(page, pathname, options = {}) {
  const response = await page.request.fetch(pathname, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, ...(options.headers ?? {}) },
  })
  expect(response.ok(), `${options.method ?? 'GET'} ${pathname} returned ${response.status()}`).toBeTruthy()
  return response.json()
}

function installationPath() {
  return `/api/v1/workspaces/${encodeURIComponent(WORKSPACE_ID)}/connector-installations`
}

test('installs a real workspace connector, reads health and binding status, and stays scoped', async ({ page }) => {
  await page.addInitScript((token) => sessionStorage.setItem('opencli.bootstrapIdentityToken', token), TOKEN)

  const marker = Date.now().toString(36)
  const name = `E2E 飞书消息连接 ${marker}`
  const appId = `cli_e2e_${marker}`
  const secrets = {
    appSecret: `e2e-app-secret-${marker}`,
    encryptKey: `e2e-encrypt-key-${marker}`,
    verificationToken: `e2e-verification-token-${marker}`,
  }

  await page.goto(`/providers/catalog?workspace=${encodeURIComponent(WORKSPACE_ID)}`)
  await expect(page.getByRole('heading', { name: '飞书消息连接' })).toBeVisible()
  const workspaceSelect = page.getByLabel('飞书消息连接 Workspace')
  await expect(workspaceSelect).toHaveValue(WORKSPACE_ID)

  await page.getByRole('button', { name: '安装飞书消息连接' }).first().click()
  await page.getByLabel('名称').fill(name)
  await page.getByLabel('App ID').fill(appId)
  await page.getByLabel('Tenant Key').fill(`tenant_e2e_${marker}`)
  await page.getByLabel('App Secret').fill(secrets.appSecret)
  await page.getByLabel('Encrypt Key').fill(secrets.encryptKey)
  await page.getByLabel('Verification Token').fill(secrets.verificationToken)

  const createResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(installationPath()))
  await page.getByRole('button', { name: '安装连接' }).click()
  expect((await createResponse).status()).toBe(201)

  const list = await apiJson(page, installationPath())
  const created = list.data.find((item) => item.name === name)
  expect(created).toMatchObject({ workspace_id: WORKSPACE_ID, app_id: appId, has_app_secret: true, has_encrypt_key: true, has_verification_token: true })
  expect(Object.hasOwn(created, 'app_secret')).toBe(false)
  expect(Object.hasOwn(created, 'encrypt_key')).toBe(false)
  expect(Object.hasOwn(created, 'verification_token')).toBe(false)

  const installationId = created.installation_public_id
  const healthPath = `${installationPath()}/${encodeURIComponent(installationId)}/health`
  const bindingPath = `${installationPath()}/${encodeURIComponent(installationId)}/my-binding`
  const health = await apiJson(page, healthPath)
  expect(health.data.installation_public_id).toBe(installationId)
  expect(health.data.binding_ready).toBe(true)

  await expect(page.getByTestId(`feishu-connector-installation-${installationId}`)).toBeVisible()
  await expect(page.getByText('消息回复与产物领取暂不可用')).toBeVisible()
  await expect(page.getByText('本人绑定')).toBeVisible()

  const challengeResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/binding-challenges`))
  await page.getByRole('button', { name: '生成本人绑定指令' }).click()
  expect((await challengeResponse).status()).toBe(201)
  await expect(page.getByTestId('feishu-connector-binding-challenge')).toBeVisible()
  await expect(page.getByText(secrets.appSecret)).toHaveCount(0)
  await expect(page.getByText(secrets.encryptKey)).toHaveCount(0)
  await expect(page.getByText(secrets.verificationToken)).toHaveCount(0)

  const binding = await apiJson(page, bindingPath)
  expect(Object.hasOwn(binding, 'data')).toBe(true)
  if (REQUIRE_ACTIVE_BINDING) expect(binding.data?.active).toBe(true)
  if (binding.data?.active) {
    const revokeResponse = page.waitForResponse((response) => response.request().method() === 'DELETE' && response.url().includes('/connector-bindings/'))
    await page.getByRole('button', { name: '撤销本人绑定' }).click()
    expect((await revokeResponse).status()).toBe(200)
    const revoked = await apiJson(page, bindingPath)
    expect(revoked.data).toMatchObject({ binding_public_id: binding.data.binding_public_id, active: false })
  }

  await page.setViewportSize({ width: 375, height: 800 })
  await page.reload()
  await expect(page.getByTestId(`feishu-connector-installation-${installationId}`)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  if (OTHER_WORKSPACE_ID) {
    await page.goto(`/providers/catalog?workspace=${encodeURIComponent(OTHER_WORKSPACE_ID)}`)
    await expect(page.getByLabel('飞书消息连接 Workspace')).toHaveValue(OTHER_WORKSPACE_ID)
    await expect(page.getByText(name)).toHaveCount(0)
  }

  await apiJson(page, `${installationPath()}/${encodeURIComponent(installationId)}`, {
    method: 'PATCH',
    data: { enabled: false },
  })
})
