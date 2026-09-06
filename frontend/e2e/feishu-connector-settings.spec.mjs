import { expect, test } from '@playwright/test'
import { signedEvent } from './openalice-workspace-support/feishu-event.mjs'

const TOKEN = process.env.OPENALICE_CONNECTOR_E2E_TOKEN ?? 'openalice-e2e-token'
const WORKSPACE_ID = process.env.OPENALICE_CONNECTOR_E2E_WORKSPACE ?? 'openalice-e2e-workspace'
const OTHER_WORKSPACE_ID = 'openalice-e2e-other'
const VIEWER_WORKSPACE_ID = 'openalice-e2e-viewer'

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
  const form = page.getByRole('dialog', { name: '安装飞书消息连接' })
  await form.getByLabel('名称', { exact: true }).fill(name)
  await form.getByLabel('App ID', { exact: true }).fill(appId)
  await form.getByLabel('Tenant Key', { exact: true }).fill(`tenant_e2e_${marker}`)
  await form.getByLabel('App Secret', { exact: true }).fill(secrets.appSecret)
  await form.getByLabel('Encrypt Key', { exact: true }).fill(secrets.encryptKey)
  await form.getByLabel('Verification Token', { exact: true }).fill(secrets.verificationToken)
  await expect(form.getByRole('checkbox')).toHaveCount(0)

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

  const card = page.getByTestId(`feishu-connector-installation-${installationId}`)
  await expect(card).toBeVisible()
  await expect(page.getByText('消息回复与产物领取暂不可用')).toBeVisible()
  await expect(card.getByText('当前用户绑定', { exact: true })).toBeVisible()
  await expect(card.getByText('未绑定', { exact: true })).toBeVisible()

  const challengeResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/binding-challenges`))
  await page.getByRole('button', { name: '生成本人绑定指令' }).click()
  const challengeResult = await challengeResponse
  expect(challengeResult.status()).toBe(201)
  const challenge = (await challengeResult.json()).data
  await expect(page.getByTestId('feishu-connector-binding-challenge')).toBeVisible()
  await expect(page.getByText(secrets.appSecret)).toHaveCount(0)
  await expect(page.getByText(secrets.encryptKey)).toHaveCount(0)
  await expect(page.getByText(secrets.verificationToken)).toHaveCount(0)

  const callback = signedEvent(secrets.encryptKey, {
    schema: '2.0',
    header: {
      event_id: `event-${marker}`, token: secrets.verificationToken,
      create_time: Date.now().toString(), event_type: 'im.message.receive_v1',
      tenant_key: `tenant_e2e_${marker}`, app_id: appId,
    },
    event: {
      sender: { sender_id: { open_id: 'ou-e2e-user' }, sender_type: 'user', tenant_key: `tenant_e2e_${marker}` },
      message: {
        message_id: `message-${marker}`, chat_id: 'oc-e2e-private', chat_type: 'p2p',
        message_type: 'text', content: JSON.stringify({ text: challenge.command_text }),
      },
    },
  })
  const callbackPath = `/api/v1/connectors/feishu/installations/${installationId}/events`
  expect((await page.request.post(callbackPath, callback)).status()).toBe(200)
  expect((await page.request.post(callbackPath, callback)).status()).toBe(200)
  await card.getByRole('button', { name: '刷新状态' }).click()
  await expect(card.getByText('已绑定', { exact: true })).toBeVisible()
  const binding = await apiJson(page, bindingPath)
  expect(binding.data?.active).toBe(true)
  expect(Object.hasOwn(binding.data, 'open_id')).toBe(false)
  expect(Object.hasOwn(binding.data, 'chat_id')).toBe(false)

  const revokeResponse = page.waitForResponse((response) => response.request().method() === 'DELETE' && response.url().includes('/connector-bindings/'))
  await card.getByRole('button', { name: '撤销本人绑定' }).click()
  expect((await revokeResponse).status()).toBe(200)
  await expect(card.getByText('已撤销', { exact: true })).toBeVisible()
  const revoked = await apiJson(page, bindingPath)
  expect(revoked.data).toMatchObject({ binding_public_id: binding.data.binding_public_id, active: false })

  await card.getByRole('button', { name: '编辑安装' }).click()
  const editForm = page.getByRole('dialog', { name: '编辑飞书消息连接' })
  for (const label of ['App Secret', 'Encrypt Key', 'Verification Token']) {
    await expect(editForm.getByLabel(label, { exact: true })).toHaveValue('')
  }
  await editForm.getByRole('checkbox').uncheck()
  const updateResponse = page.waitForResponse((response) => response.request().method() === 'PATCH' && response.url().includes(installationPath()))
  await editForm.getByRole('button', { name: '保存更改' }).click()
  expect((await updateResponse).status()).toBe(200)
  await expect(card.getByRole('button', { name: '生成本人绑定指令' })).toBeDisabled()
  expect((await apiJson(page, healthPath)).data.callback_ready).toBe(false)

  await page.setViewportSize({ width: 375, height: 800 })
  await page.reload()
  await expect(page.getByTestId(`feishu-connector-installation-${installationId}`)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.goto(`/providers/catalog?workspace=${encodeURIComponent(OTHER_WORKSPACE_ID)}`)
  await expect(page.getByLabel('飞书消息连接 Workspace')).toHaveValue(OTHER_WORKSPACE_ID)
  await expect(page.getByText(name)).toHaveCount(0)
  const crossScope = await page.request.get(`/api/v1/workspaces/${OTHER_WORKSPACE_ID}/connector-installations/${installationId}/health`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  })
  expect(crossScope.status()).toBe(404)

  await page.goto('/providers/catalog?workspace=not-authorized-workspace')
  await expect(page.getByText('URL 中的 Workspace 不在当前授权范围内；未读取该范围的连接。')).toBeVisible()
  await expect(page.getByRole('button', { name: '安装飞书消息连接' })).toHaveCount(0)
})

test('workspace viewers can bind themselves while configuration stays unavailable', async ({ page }) => {
  await page.addInitScript((token) => sessionStorage.setItem('opencli.bootstrapIdentityToken', token), TOKEN)
  await page.goto(`/providers/catalog?workspace=${VIEWER_WORKSPACE_ID}`)
  const card = page.getByTestId('feishu-connector-installation-openalice-viewer-connector')
  await expect(card).toBeVisible()
  await expect(page.getByRole('button', { name: '安装飞书消息连接' })).toHaveCount(0)
  await expect(card.getByRole('button', { name: '编辑安装' })).toHaveCount(0)
  await expect(card.getByRole('button', { name: '生成本人绑定指令' })).toBeEnabled()
  await card.getByRole('button', { name: '生成本人绑定指令' }).click()
  await expect(card.getByTestId('feishu-connector-binding-challenge')).toBeVisible()
  const denied = await page.request.patch(`/api/v1/workspaces/${VIEWER_WORKSPACE_ID}/connector-installations/openalice-viewer-connector`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { enabled: false },
  })
  expect(denied.status()).toBe(403)
})
