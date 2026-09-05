import { expect, test } from '@playwright/test'

const workspace = { id: 'workspace-a', name: 'Research', slug: 'research', active: true, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }
const conversation = { id: 'conversation-a', workspace_id: workspace.id, title: null, status: 'active', context_binding: {}, revision: 0, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }

test('agent conversation session sends, restores, continues, and confirms a proposal', async ({ page }) => {
  const turns = []
  let created = false
  let confirmationCount = 0

  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token')
  })
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply({ subject: 'test-user', email: null, name: 'Test User', username: 'test', picture: null, is_platform_admin: true, auth_method: 'test' })
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'GET') return reply(created ? [conversation] : [])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'POST') {
      created = true
      return reply(conversation)
    }
    if (url.pathname.endsWith('/chat/sessions/conversation-a') && route.request().method() === 'GET') return reply({ ...conversation, turns })
    if (url.pathname.endsWith('/chat/sessions/conversation-a/messages')) {
      const request = route.request().postDataJSON()
      const sequence = turns.length + 1
      const proposal = sequence === 2
        ? { type: 'proposal', proposal: { tool: 'update_provider', args: {}, summary: '更新模型连接', diff: 'enabled: false -> true', work_item_id: 'work-item-a', workspace_id: workspace.id, proposal_version: 'v1' } }
        : { type: 'message', content: '已恢复并继续处理。' }
      const turn = { sequence, request_id: request.request_id, status: proposal.type === 'proposal' ? 'proposal' : 'completed', user_content: request.content, response: proposal, context_binding: request.context, tool_trace: [], created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }
      turns.push(turn)
      return reply({ conversation_id: conversation.id, turn })
    }
    if (url.pathname.endsWith('/chat/confirm')) {
      confirmationCount += 1
      return reply({ ok: true })
    }
    return reply({})
  })

  await page.goto('/dashboard?workspace=workspace-a')
  await page.getByRole('button', { name: 'Agent', exact: true }).click()
  await page.getByLabel('给全局 Agent 的消息').fill('恢复这个会话')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('已恢复并继续处理。')).toBeVisible()
  await expect.poll(() => page.evaluate(() => localStorage.getItem('opencli:agent-session:workspace-a'))).toBe('conversation-a')

  await page.reload()
  await page.getByRole('button', { name: 'Agent', exact: true }).click()
  await expect(page.getByText('恢复这个会话')).toBeVisible()
  await expect(page.getByText('已恢复并继续处理。')).toBeVisible()

  await page.getByLabel('给全局 Agent 的消息').fill('生成变更提案')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('待确认操作')).toBeVisible()
  await page.getByRole('button', { name: '确认执行' }).click()
  await expect.poll(() => confirmationCount).toBe(1)
})

test('agent conversation reports a create-session API failure', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token')
  })
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply({ subject: 'test-user', email: null, name: 'Test User', username: 'test', picture: null, is_platform_admin: true, auth_method: 'test' })
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'GET') return reply([])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'POST') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'conversation service unavailable' }) })
    }
    return reply({})
  })

  await page.goto('/dashboard?workspace=workspace-a')
  await page.getByRole('button', { name: 'Agent', exact: true }).click()
  await page.getByLabel('给全局 Agent 的消息').fill('创建会话')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('conversation service unavailable')).toBeVisible()
})

test('agent URL intent opens the requested session without falling back across a project boundary', async ({ page }) => {
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token')
    localStorage.setItem('opencli:agent-session:workspace-a', 'conversation-a')
  })
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply({ subject: 'test-user', email: null, name: 'Test User', username: 'test', picture: null, is_platform_admin: true, auth_method: 'test' })
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces/workspace-a/projects')) return reply([])
    if (url.pathname.endsWith('/chat/sessions')) return reply([conversation])
    if (url.pathname.endsWith('/chat/sessions/missing-session')) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
    return reply({})
  })

  await page.goto('/studio?workspace=workspace-a&project=project-b&agent=1&conversation=missing-session')
  await expect(page.getByRole('dialog', { name: '全局 Agent' })).toBeVisible()
  await expect(page.getByText('指定的 Agent 会话不存在，或你无权访问。没有打开其他会话。')).toBeVisible()
  await expect(page.getByLabel('选择 Agent 会话')).toHaveValue('')
  expect(pageErrors).toEqual([])
})

test('agent URL intent fetches an older exact session outside the session-list page', async ({ page }) => {
  const older = { ...conversation, id: 'conversation-older', context_binding: { project_id: 'project-a' }, turns: [{ sequence: 1, request_id: 'older-request', status: 'completed', user_content: '继续旧项目', response: { type: 'message', content: '已恢复旧会话。' }, context_binding: { project_id: 'project-a' }, tool_trace: [] }] }
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply({ subject: 'test-user', name: 'Test User', username: 'test', is_platform_admin: true, auth_method: 'test' })
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/workspaces/workspace-a/projects')) return reply([])
    if (url.pathname.endsWith('/chat/sessions')) return reply([conversation])
    if (url.pathname.endsWith('/chat/sessions/conversation-older')) return reply(older)
    return reply({})
  })
  await page.goto('/studio?workspace=workspace-a&project=project-a&agent=1&conversation=conversation-older')
  await expect(page.getByText('继续旧项目')).toBeVisible()
  await expect(page.getByText('已恢复旧会话。')).toBeVisible()
  expect(pageErrors).toEqual([])
})
