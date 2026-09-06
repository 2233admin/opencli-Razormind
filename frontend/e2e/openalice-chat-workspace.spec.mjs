import { expect, test } from '@playwright/test'

const TOKEN = 'openalice-e2e-token'
const WORKSPACE = 'openalice-e2e-workspace'
const STUDIO = 'openalice-e2e-studio'
const ALPHA = 'openalice-project-alpha'
const BETA = 'openalice-project-beta'

test.skip(process.env.OPENALICE_CHAT_E2E !== '1', 'requires the isolated chat fixture, never a live model')
test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => sessionStorage.setItem('opencli.bootstrapIdentityToken', token), TOKEN)
})

async function api(page, path, data) {
  const response = await page.request.fetch(`/api/v1${path}`, {
    method: data === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${TOKEN}` },
    ...(data === undefined ? {} : { data }),
  })
  expect(response.ok(), `${path}: ${response.status()} ${await response.text()}`).toBeTruthy()
  return (await response.json()).data
}

async function createSession(page, title, context = {}) {
  const catalog = await api(page, `/chat/execution-targets?workspace_id=${WORKSPACE}`)
  const target = catalog.targets.find((item) => item.kind === 'provider')
  return api(page, '/chat/sessions', {
    title, workspace_id: context.project_id ? STUDIO : WORKSPACE, context,
    execution_target_id: target.id, model_id: target.default_model_id,
  })
}

function messages(page) { return page.getByRole('textbox', { name: '给全局 Agent 的消息' }) }
function messageResponse(page, conversationId) {
  return page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().includes(`/chat/sessions/${conversationId ?? ''}`)
    && response.url().endsWith('/messages'))
}

test('first utterance, background refresh, second turn and close/reopen use one durable session', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1397, height: 1272 })
  const catalog = await api(page, `/chat/execution-targets?workspace_id=${WORKSPACE}`)
  expect(catalog.background_execution.status).toBe('ready')
  const target = catalog.targets.find((item) => item.kind === 'provider')
  expect(target.readiness.status).toBe('unverified')
  await page.goto(`/chat?workspace=${WORKSPACE}`)
  await page.getByLabel('执行配置').selectOption(target.id)
  await page.getByLabel('模型', { exact: true }).selectOption('deterministic-chat')
  await page.getByLabel('执行方式').selectOption('background')
  await messages(page).fill('[slow] first browser turn')
  const createResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/chat/sessions'))
  const firstResponse = messageResponse(page)
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const created = (await (await createResponse).json()).data
  expect(created.execution_binding).toMatchObject({ target_id: target.id, model_id: 'deterministic-chat' })
  const accepted = await firstResponse
  expect(accepted.status()).toBe(202)
  const first = (await accepted.json()).data
  expect(first.conversation_id).toBe(created.id)
  await expect(page.getByText('读取会话资料', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText(/后台任务正在运行/)).toBeVisible()
  await expect(page.getByText('E2E reply (1 messages, deterministic-chat): [slow] first browser turn', { exact: true })).toBeVisible()
  const secondResponse = messageResponse(page, created.id)
  await messages(page).fill('second browser turn')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  expect((await secondResponse).ok()).toBeTruthy()
  await expect(page.getByText('E2E reply (3 messages, deterministic-chat): second browser turn', { exact: true })).toBeVisible()
  const detail = await api(page, `/chat/sessions/${created.id}`)
  expect(detail.turns).toHaveLength(2)
  expect(detail.turns.map((turn) => turn.sequence)).toEqual([1, 2])
  expect(new Set(detail.turns.map((turn) => turn.agent_run_id)).size).toBe(2)
  expect(detail.execution_binding).toEqual(created.execution_binding)
  for (const turn of detail.turns) {
    const events = await api(page, `/chat/sessions/${created.id}/turns/${turn.id}/events`)
    expect(events.terminal).toBe(true)
    expect(events.events.map((event) => event.sequence)).toEqual(events.events.map((_, index) => index + 1))
    expect(events.events.map((event) => event.type)).toEqual(expect.arrayContaining(['tool.started', 'tool.completed', 'reply']))
    const tail = await api(page, `/chat/sessions/${created.id}/turns/${turn.id}/events?after_sequence=${events.next_sequence}`)
    expect(tail.events).toEqual([])
  }
  await expect(page.getByText('已执行 1 项工具操作', { exact: true })).toHaveCount(2)
  await page.getByLabel('关闭当前 Agent 会话').click()
  await expect.poll(async () => (await api(page, `/chat/sessions/${created.id}`)).status).toBe('closed')
  await page.goto(`/chat?workspace=${WORKSPACE}&conversation=${created.id}`)
  await expect(messages(page)).toBeDisabled()
  const reopening = page.waitForResponse((response) => response.url().endsWith(`/sessions/${created.id}/reopen`))
  await page.getByRole('button', { name: '重新打开', exact: true }).click()
  expect((await reopening).ok()).toBeTruthy()
  await expect(messages(page)).toBeEnabled()
  const reopened = await api(page, `/chat/sessions/${created.id}`)
  expect(reopened.status).toBe('active')
  expect(reopened.turns.map((turn) => turn.agent_run_id)).toEqual(detail.turns.map((turn) => turn.agent_run_id))
  expect(errors).toEqual([])
})

test('project results follow selected Studio-bound conversation and clear for an unscoped one', async ({ page }) => {
  const alpha = await createSession(page, 'Browser Alpha scope', { project_id: ALPHA, workflow_id: 'openalice-workflow-alpha', run_id: 'openalice-run-alpha-1' })
  const beta = await createSession(page, 'Browser Beta scope', { project_id: BETA, workflow_id: 'openalice-workflow-beta', run_id: 'openalice-run-beta-1' })
  await createSession(page, 'Browser unscoped')
  expect(alpha.workspace_id).toBe(WORKSPACE)
  expect(alpha.context_binding.studio_workspace_id).toBe(STUDIO)
  await page.goto(`/chat?workspace=${WORKSPACE}`)
  await page.getByRole('button', { name: 'Browser Alpha scope 进行中', exact: true }).click()
  await expect(page.getByText('Alpha run one report', { exact: true })).toBeVisible()
  await expect(page.getByText('Beta isolated report', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '会话上下文链接' }).getByRole('link', { name: '项目', exact: true })).toHaveAttribute('href', new RegExp(`workspace=${STUDIO}`))
  await page.getByRole('button', { name: 'Browser Beta scope 进行中', exact: true }).click()
  await expect(page.getByText('Beta isolated report', { exact: true })).toBeVisible()
  await expect(page.getByText('Alpha run one report', { exact: true })).toHaveCount(0)
  const followUp = messageResponse(page, beta.id)
  await messages(page).fill('Beta follow-up stays scoped')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const sent = await followUp
  expect(sent.ok()).toBeTruthy()
  expect(sent.request().postDataJSON()).not.toHaveProperty('context')
  await expect(page.getByText('E2E reply (1 messages, deterministic-chat): Beta follow-up stays scoped', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Browser unscoped 进行中', exact: true }).click()
  await expect(page.getByTestId('project-artifacts-panel')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择项目并开始', exact: true })).toBeVisible()
  const persisted = await api(page, `/chat/sessions/${beta.id}`)
  expect(persisted.context_binding.project_id).toBe(BETA)
  expect(persisted.turns[0].context_binding).toMatchObject({ project_id: BETA, studio_workspace_id: STUDIO })
})

test('switching away from a background turn does not inject its reply into another session', async ({ page }) => {
  const running = await createSession(page, 'Browser running source')
  const other = await createSession(page, 'Browser other target')
  await page.goto(`/chat?workspace=${WORKSPACE}`)
  await page.getByRole('button', { name: 'Browser running source 进行中', exact: true }).click()
  const accepted = await api(page, `/chat/sessions/${running.id}/messages`, { request_id: 'background-switch', content: '[slow] background source only', context: {}, execution_mode: 'background' })
  expect(accepted.run.id).toBeTruthy()
  await page.reload()
  await expect(page.getByText(/后台任务正在运行/)).toBeVisible()
  await page.getByRole('button', { name: 'Browser other target 进行中', exact: true }).click()
  await expect(messages(page)).toBeEnabled()
  await expect.poll(async () => (await api(page, `/chat/sessions/${running.id}`)).turns[0].status).toBe('completed')
  await expect(page.getByText('E2E reply (1 messages, deterministic-chat): [slow] background source only', { exact: true })).toHaveCount(0)
  expect((await api(page, `/chat/sessions/${other.id}`)).turns).toHaveLength(0)
  await page.getByRole('button', { name: 'Browser running source 进行中', exact: true }).click()
  await expect(page.getByText('E2E reply (1 messages, deterministic-chat): [slow] background source only', { exact: true })).toBeVisible()
})

test('a transient event request failure recovers without resubmitting the running turn', async ({ page }) => {
  const conversation = await createSession(page, 'Browser temporary disconnect')
  const accepted = await api(page, `/chat/sessions/${conversation.id}/messages`, {
    request_id: 'network-recovery', content: '[slow] recover event polling', context: {}, execution_mode: 'background',
  })
  let interruptedOnce = false
  let messagePosts = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/messages')) messagePosts += 1
  })
  await page.route(`**/chat/sessions/${conversation.id}/turns/*/events?*`, async (route) => {
    if (!interruptedOnce) {
      interruptedOnce = true
      await route.abort('connectionreset')
    } else await route.continue()
  })
  await page.goto(`/chat?workspace=${WORKSPACE}&conversation=${conversation.id}`)
  await expect(page.getByText('E2E reply (1 messages, deterministic-chat): [slow] recover event polling', { exact: true })).toBeVisible()
  expect(interruptedOnce).toBe(true)
  expect(messagePosts).toBe(0)
  const detail = await api(page, `/chat/sessions/${conversation.id}`)
  expect(detail.turns).toHaveLength(1)
  expect(detail.turns[0].agent_run_id).toBe(accepted.run.id)
})

test('375px layout supports search and composer without horizontal overflow', async ({ page }) => {
  await createSession(page, 'Browser mobile search')
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto(`/chat?workspace=${WORKSPACE}`)
  await expect(page.getByLabel('执行配置')).toBeVisible()
  await expect(page.getByLabel('模型', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await page.getByLabel('搜索 Agent 会话').fill('Browser mobile search')
  await page.getByRole('button', { name: 'Browser mobile search 进行中', exact: true }).click()
  await page.getByLabel('切换会话侧栏').click()
  await expect(messages(page)).toBeVisible()
  await expect(messages(page)).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})
