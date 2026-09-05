import { expect, test } from '@playwright/test'

const workspace = { id: 'workspace-a', name: 'Research', slug: 'research', active: true, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }
const identity = { subject: 'test-user', name: 'Test User', username: 'test', is_platform_admin: true, auth_method: 'test' }

function installAuth(page) {
  return page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
}

test('first Agent send renders its response and returns the composer to ready', async ({ page }) => {
  const conversation = { id: 'conversation-a', workspace_id: workspace.id, title: 'New work', status: 'active', context_binding: {}, revision: 0, created_at: workspace.created_at, updated_at: workspace.updated_at }
  await installAuth(page)
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply(identity)
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'GET') return reply([])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'POST') return reply(conversation)
    if (url.pathname.endsWith('/chat/sessions/conversation-a/messages')) return reply({ conversation_id: conversation.id, turn: { status: 'completed', response: { type: 'message', content: '已收到第一条需求。' } } })
    return reply({})
  })
  await page.goto('/dashboard?workspace=workspace-a&agent=1')
  await page.getByLabel('给全局 Agent 的消息').fill('建立项目')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('已收到第一条需求。')).toBeVisible()
  await expect(page.getByLabel('给全局 Agent 的消息')).toBeEnabled()
})

test('confirmed project continuation uses the server-bound project context', async ({ page }) => {
  const conversation = { id: 'conversation-a', workspace_id: workspace.id, title: 'Project work', status: 'active', context_binding: {}, revision: 0, created_at: workspace.created_at, updated_at: workspace.updated_at }
  let bound = false
  let followupContext
  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token')
    localStorage.setItem('opencli:agent-session:workspace-a', 'conversation-a')
  })
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (url.pathname.endsWith('/auth/me')) return reply(identity)
    if (url.pathname.endsWith('/governance/workspaces')) return reply([workspace])
    if (url.pathname.endsWith('/chat/sessions') && route.request().method() === 'GET') return reply([conversation])
    if (url.pathname.endsWith('/chat/sessions/conversation-a') && route.request().method() === 'GET') {
      const turns = bound ? [] : [{ sequence: 1, user_content: '创建项目', status: 'proposal', response: { type: 'proposal', proposal: { tool: 'create_project', args: {}, summary: '创建项目草稿', diff: 'draft', work_item_id: 'work-item-a', workspace_id: workspace.id, proposal_version: 'v1' } } }]
      return reply({ ...conversation, context_binding: bound ? { project_id: 'project-a', workflow_id: 'workflow-a' } : {}, turns })
    }
    if (url.pathname.endsWith('/chat/sessions/conversation-a/messages')) {
      followupContext = route.request().postDataJSON().context
      return reply({ conversation_id: conversation.id, turn: { status: 'completed', response: { type: 'message', content: '继续处理。' } } })
    }
    if (url.pathname.endsWith('/chat/confirm')) { bound = true; return reply({ applied: true, tool: 'create_project', workspace_id: workspace.id, project_id: 'project-a', workflow_id: 'workflow-a', draft_revision: 1 }) }
    return reply({})
  })
  await page.goto('/dashboard?workspace=workspace-a&agent=1')
  await expect(page.getByLabel('选择 Agent 会话')).toHaveValue('conversation-a')
  await page.getByRole('button', { name: '确认执行' }).click()
  await expect(page.getByText('打开 Agent 保存的工作流草稿')).toBeVisible()
  await page.getByLabel('给全局 Agent 的消息').fill('继续创建后的项目')
  await page.getByRole('button', { name: '发送' }).click()
  await expect.poll(() => followupContext).toMatchObject({ project_id: 'project-a', workflow_id: 'workflow-a' })
})
