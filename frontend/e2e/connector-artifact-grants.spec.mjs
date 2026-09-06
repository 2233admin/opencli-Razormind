import { expect, test } from '@playwright/test'

import { signedEvent } from './openalice-workspace-support/feishu-event.mjs'

const TOKEN = process.env.OPENALICE_CONNECTOR_E2E_TOKEN ?? 'openalice-e2e-token'
const WORKSPACE_ID = process.env.OPENALICE_CONNECTOR_E2E_WORKSPACE ?? 'openalice-e2e-workspace'
const STUDIO_WORKSPACE_ID = 'openalice-e2e-studio'
const PROJECT_ID = 'openalice-project-alpha'
const WORKFLOW_ID = 'openalice-workflow-alpha'
const RUN_ID = 'openalice-run-alpha-1'
const ARTIFACT_ID = 'openalice-session-alpha-1:report-alpha-one'
const CONVERSATION_ID = 'openalice-conversation-active'
const INSTALLATION_ID = 'openalice-p2-connector'
const APP_ID = 'cli_e2e_delivery'
const TENANT_KEY = 'tenant_e2e_delivery'
const ENCRYPT_KEY = 'dummy-e2e-delivery-encrypt-key'
const VERIFICATION_TOKEN = 'dummy-e2e-delivery-verification-token'
const CHAT_ID = 'oc-e2e-delivery'
const DELIVERY_PATH = '/api/__test__/connector-deliveries'
const BACKEND_URL = process.env.OPENALICE_BACKEND_URL ?? 'http://127.0.0.1:8048'

test.describe.configure({ mode: 'serial' })

async function apiJson(page, pathname, options = {}) {
  const response = await page.request.fetch(pathname, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, ...(options.headers ?? {}) },
  })
  expect(response.ok(), `${options.method ?? 'GET'} ${pathname} returned ${response.status()}`).toBeTruthy()
  return response.json()
}

async function apiResponse(page, pathname, options = {}) {
  return page.request.fetch(pathname, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, ...(options.headers ?? {}) },
  })
}

function installationPath() {
  return `/api/v1/workspaces/${encodeURIComponent(WORKSPACE_ID)}/connector-installations/${encodeURIComponent(INSTALLATION_ID)}`
}

function dataUrl() {
  const params = new URLSearchParams({
    workspace: STUDIO_WORKSPACE_ID,
    workflow: WORKFLOW_ID,
    run: RUN_ID,
    view: 'dataset',
    artifact: ARTIFACT_ID,
  })
  return `/studio/projects/${PROJECT_ID}/data?${params.toString()}`
}

function signedMessage(marker, text, replyTo = null) {
  return signedEvent(ENCRYPT_KEY, {
    schema: '2.0',
    header: {
      event_id: `event-${marker}`,
      token: VERIFICATION_TOKEN,
      create_time: Date.now().toString(),
      event_type: 'im.message.receive_v1',
      tenant_key: TENANT_KEY,
      app_id: APP_ID,
    },
    event: {
      sender: { sender_id: { open_id: 'ou-e2e-user' }, sender_type: 'user', tenant_key: TENANT_KEY },
      message: {
        message_id: `message-${marker}`,
        chat_id: CHAT_ID,
        chat_type: 'p2p',
        message_type: 'text',
        content: JSON.stringify({ text }),
        ...(replyTo ? { parent_id: replyTo, root_id: replyTo } : {}),
      },
    },
  })
}

async function waitForDelivery(page, predicate, message) {
  let found = null
  await expect.poll(async () => {
    const response = await page.request.get(`${BACKEND_URL}${DELIVERY_PATH}`, { headers: { Authorization: `Bearer ${TOKEN}` } })
    if (!response.ok()) return false
    const payload = await response.json()
    found = payload.data.find(predicate) ?? null
    return Boolean(found)
  }, { timeout: 30_000, intervals: [250, 500, 1_000], message }).toBe(true)
  return found
}

test('authorizes one bound connector, delivers activation and artifact claim, and keeps the grant scoped', async ({ page }) => {
  await page.addInitScript((token) => sessionStorage.setItem('opencli.bootstrapIdentityToken', token), TOKEN)

  const installation = await apiJson(page, `/api/v1/workspaces/${encodeURIComponent(WORKSPACE_ID)}/connector-installations`)
  expect(installation.data).toEqual(expect.arrayContaining([
    expect.objectContaining({ installation_public_id: INSTALLATION_ID, app_id: APP_ID, workspace_id: WORKSPACE_ID }),
  ]))

  const challengeResponse = await apiResponse(page, `${installationPath()}/binding-challenges`, { method: 'POST' })
  expect(challengeResponse.status()).toBe(201)
  const challenge = (await challengeResponse.json()).data
  expect(challenge.command_text).toBeTruthy()

  const callbackPath = `/api/v1/connectors/feishu/installations/${encodeURIComponent(INSTALLATION_ID)}/events`
  const bindCallback = signedMessage('binding', challenge.command_text)
  expect((await page.request.post(callbackPath, bindCallback)).status()).toBe(200)
  const binding = await apiJson(page, `${installationPath()}/my-binding`)
  expect(binding.data).toMatchObject({ active: true })
  const artifactDetail = await apiJson(
    page,
    `/api/v1/workspaces/${encodeURIComponent(STUDIO_WORKSPACE_ID)}/projects/${encodeURIComponent(PROJECT_ID)}/artifacts/${encodeURIComponent(ARTIFACT_ID)}?workflow_id=${encodeURIComponent(WORKFLOW_ID)}&run_id=${encodeURIComponent(RUN_ID)}`,
  )
  expect(artifactDetail.data).toMatchObject({ workspace_id: STUDIO_WORKSPACE_ID, conversation_id: CONVERSATION_ID })

  await page.goto(dataUrl())
  await expect(page.getByTestId('project-artifact-detail')).toBeVisible()
  await expect(page.getByText('Alpha report body')).toBeVisible()
  await expect(page.getByRole('heading', { name: '飞书回复与报告领取' })).toBeVisible()
  await expect(page.getByText('原会话与当前报告的 Studio Workspace 不一致。')).toHaveCount(0)

  const replyPanel = page.getByRole('heading', { name: '飞书回复与报告领取' }).locator('..').locator('..')
  const installationSelect = page.getByLabel('选择已绑定的飞书机器人')
  await expect(installationSelect).toBeVisible()
  await installationSelect.selectOption(INSTALLATION_ID)
  await page.getByText('我确认授权这个已绑定连接回复当前报告对应的同一原 Agent 会话。').click()

  const replyRequest = page.waitForRequest((request) => request.method() === 'POST' && request.url().includes('/connector-reply-grants'))
  const replyResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes('/connector-reply-grants'))
  await page.getByRole('button', { name: '授权原会话并发送激活' }).click()
  const replyBody = (await replyRequest).postDataJSON()
  expect((await replyResponse).status()).toBe(201)
  expect(replyBody).toMatchObject({ installation_public_id: INSTALLATION_ID, binding_public_id: binding.data.binding_public_id, conversation_id: CONVERSATION_ID })
  const replyCreated = await apiJson(page, `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/connector-reply-grants`, { method: 'POST', data: replyBody })
  expect(replyCreated.data).toMatchObject({ reply_grant_public_id: expect.any(String), created: false })
  expect(replyCreated.data.reply_grant_public_id).toBeTruthy()

  const replyGrantId = replyCreated.data.reply_grant_public_id
  const replyPath = `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/connector-reply-grants/${encodeURIComponent(replyGrantId)}`
  const wrongConnectorScope = await apiResponse(page, `/api/v1/workspaces/${encodeURIComponent(STUDIO_WORKSPACE_ID)}/projects/${PROJECT_ID}/connector-reply-grants/${encodeURIComponent(replyGrantId)}`)
  expect([403, 404]).toContain(wrongConnectorScope.status())
  await expect.poll(async () => (await apiJson(page, replyPath)).data.activation_delivery_status, { timeout: 30_000, intervals: [500, 1_000] }).toBe('sent')
  const activationDelivery = await waitForDelivery(page, (item) => item.chat_id === CHAT_ID && item.text && !item.reply_to, '激活消息没有出现在真实 SDK 出站接收器')
  expect(activationDelivery).toMatchObject({ chat_id: CHAT_ID, message_id: expect.any(String), uuid: expect.any(String) })
  expect(activationDelivery.text).toEqual(expect.any(String))

  const inboundReply = signedMessage('reply', '请继续说明这份报告', activationDelivery.message_id)
  expect((await page.request.post(callbackPath, inboundReply)).status()).toBe(200)
  const executionDelivery = await waitForDelivery(page, (item) => item.chat_id === CHAT_ID && item.reply_to === 'message-reply', '原会话回复没有通过真实 SDK 入站回调返回')
  expect(executionDelivery.text).toEqual(expect.any(String))

  await expect(page.getByRole('button', { name: '授权并发送领取指令' })).toBeVisible()
  const artifactRequest = page.waitForRequest((request) => request.method() === 'POST' && request.url().includes(`/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants`))
  const artifactResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants`))
  await page.getByRole('button', { name: '授权并发送领取指令' }).click()
  const artifactBody = (await artifactRequest).postDataJSON()
  expect(artifactBody).toMatchObject({ artifact_public_id: ARTIFACT_ID, workflow_id: WORKFLOW_ID, run_id: RUN_ID })
  expect((await artifactResponse).status()).toBe(201)
  const artifactCreated = await (await artifactResponse).json()
  expect(artifactCreated.data).toMatchObject({ created: true, artifact_public_id: ARTIFACT_ID, claim_text: expect.any(String) })
  const claimText = artifactCreated.data.claim_text
  const artifactGrantId = artifactCreated.data.artifact_grant_public_id
  expect(artifactGrantId).toBeTruthy()

  const artifactGrantPath = `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants/${encodeURIComponent(artifactGrantId)}`
  await expect.poll(async () => (await apiJson(page, artifactGrantPath)).data.offer_delivery_status, { timeout: 30_000, intervals: [500, 1_000] }).toBe('sent')
  const claimDelivery = await waitForDelivery(page, (item) => item.chat_id === CHAT_ID && item.message_id !== activationDelivery.message_id && item.message_id !== executionDelivery.message_id, '报告领取指令没有出现在真实 SDK 出站接收器')
  expect(claimDelivery.text).toContain(claimText)
  expect(claimDelivery.message_id).not.toBe(activationDelivery.message_id)
  await expect(page.getByText('这条领取指令只在本次新授权响应中显示一次：')).toBeVisible()

  const duplicateArtifact = await apiJson(page, `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants`, { method: 'POST', data: artifactBody })
  expect(duplicateArtifact.data).toMatchObject({ created: false, artifact_grant_public_id: artifactGrantId, claim_text: null })

  await page.getByRole('button', { name: '撤销领取授权' }).click()
  await expect(page.getByRole('button', { name: '重新授权领取' })).toBeVisible()
  expect((await apiJson(page, artifactGrantPath)).data).toMatchObject({ status: 'revoked' })

  const revokedClaimCallback = signedMessage('claim-revoked', claimText, claimDelivery.message_id)
  expect((await page.request.post(callbackPath, revokedClaimCallback)).status()).toBe(200)
  await page.waitForTimeout(1_500)
  const afterRevokedClaim = await apiJson(page, artifactGrantPath)
  expect(afterRevokedClaim.data).toMatchObject({ status: 'revoked', delivery_status: null })
  const deliveriesAfterRevokedClaim = await (await page.request.get(`${BACKEND_URL}${DELIVERY_PATH}`, { headers: { Authorization: `Bearer ${TOKEN}` } })).json()
  expect(deliveriesAfterRevokedClaim.data.some((item) => item.reply_to === 'message-claim-revoked')).toBe(false)

  const artifactRequest2 = page.waitForRequest((request) => request.method() === 'POST' && request.url().includes(`/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants`))
  const artifactResponse2 = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(`/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants`))
  await page.getByRole('button', { name: '重新授权领取' }).click()
  const artifactBody2 = (await artifactRequest2).postDataJSON()
  expect(artifactBody2.request_id).not.toBe(artifactBody.request_id)
  expect((await artifactResponse2).status()).toBe(201)
  const artifactCreated2 = await (await artifactResponse2).json()
  expect(artifactCreated2.data).toMatchObject({ created: true, artifact_public_id: ARTIFACT_ID, claim_text: expect.any(String) })
  const claimText2 = artifactCreated2.data.claim_text
  const artifactGrantId2 = artifactCreated2.data.artifact_grant_public_id
  const artifactGrantPath2 = `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/connector-reply-grants/${encodeURIComponent(replyGrantId)}/artifact-grants/${encodeURIComponent(artifactGrantId2)}`
  await expect.poll(async () => (await apiJson(page, artifactGrantPath2)).data.offer_delivery_status, { timeout: 30_000, intervals: [500, 1_000] }).toBe('sent')
  const claimDelivery2 = await waitForDelivery(page, (item) => item.chat_id === CHAT_ID && item.message_id !== activationDelivery.message_id && item.message_id !== executionDelivery.message_id && item.message_id !== claimDelivery.message_id, '重新授权后的报告领取指令没有送达')
  expect(claimDelivery2.text).toContain(claimText2)

  const validClaimCallback = signedMessage('claim-valid', claimText2, claimDelivery2.message_id)
  expect((await page.request.post(callbackPath, validClaimCallback)).status()).toBe(200)
  const artifactDelivery = await waitForDelivery(page, (item) => item.chat_id === CHAT_ID && item.reply_to === 'message-claim-valid', '领取指令没有返回真实报告内容')
  expect(artifactDelivery.text).toContain('Alpha report body')
  await expect.poll(async () => (await apiJson(page, artifactGrantPath2)).data.status, { timeout: 30_000, intervals: [500, 1_000] }).toBe('redeemed')
  expect((await apiJson(page, artifactGrantPath2)).data.delivery_status).toBe('sent')

  await page.reload()
  await expect(page.getByTestId('project-artifact-detail')).toBeVisible()
  await expect(page.getByText('这条领取指令只在本次新授权响应中显示一次：')).toHaveCount(0)
  await expect(page.getByText('报告领取授权已恢复', { exact: false })).toHaveCount(0)
  const restoredGrant = await apiJson(page, artifactGrantPath2)
  expect(restoredGrant.data).toMatchObject({ artifact_grant_public_id: artifactGrantId2, artifact_public_id: ARTIFACT_ID, workflow_id: WORKFLOW_ID, run_id: RUN_ID, status: 'redeemed', delivery_status: 'sent' })

  await page.setViewportSize({ width: 375, height: 800 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(await replyPanel.evaluate((element) => element.textContent?.includes('飞书回复与报告领取'))).toBe(true)

  const membersPath = `/api/v1/workspaces/${encodeURIComponent(WORKSPACE_ID)}/members`
  const secondAdmin = await apiJson(page, membersPath, {
    method: 'POST',
    data: { subject: 'openalice-e2e-second-admin', email: 'second-admin@example.test', display_name: 'OpenAlice E2E Second Admin', role: 'admin' },
  })
  expect(secondAdmin.data).toMatchObject({ role: 'admin' })
  const viewer = await apiJson(page, `${membersPath}/${encodeURIComponent('openalice-e2e-user')}`, { method: 'PATCH', data: { role: 'viewer' } })
  expect(viewer.data).toMatchObject({ user_id: 'openalice-e2e-user', role: 'viewer' })

  await page.reload()
  await expect(page.getByText('当前账号只能查看已有授权，暂不能创建新的回复或报告领取授权。')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新授权领取' })).toBeDisabled()
  const viewerReply = await apiResponse(page, `/api/v1/workspaces/${encodeURIComponent(WORKSPACE_ID)}/projects/${encodeURIComponent(PROJECT_ID)}/connector-reply-grants`, {
    method: 'POST',
    data: { ...replyBody, request_id: `viewer-${replyBody.request_id}` },
  })
  expect(viewerReply.status()).toBe(403)
})
