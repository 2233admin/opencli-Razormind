import { expect, test } from '@playwright/test'

const workspace = { id: '11111111-1111-4111-8111-111111111111', name: 'E2E Workspace', slug: 'e2e-workspace', active: true, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }
const projectId = '22222222-2222-4222-8222-222222222222'
const workflowId = '33333333-3333-4333-8333-333333333333'
const runId = '44444444-4444-4444-8444-444444444444'
const conversationId = '55555555-5555-4555-8555-555555555555'
const proposalId = '66666666-6666-4666-8666-666666666666'

test('inbox proposal restores its exact Agent conversation and remains reachable as a resolved project result', async ({ page }) => {
  let resolved = false
  let approvalPosts = 0
  let followUpBody = null
  const turns = [{ sequence: 1, request_id: 'request-e2e', status: 'proposal', user_content: '创建项目', response: { type: 'proposal', proposal: { tool: 'create_project', args: {}, summary: '创建项目工作流', diff: 'new workflow', work_item_id: proposalId, workspace_id: workspace.id, proposal_version: 'v1' } }, context_binding: {}, tool_trace: [] }]
  let pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'e2e-token'))
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data }) })
    if (url.pathname.endsWith('/auth/me')) return reply({ subject: 'e2e', name: 'E2E', username: 'e2e', is_platform_admin: true, auth_method: 'test' })
    if (url.pathname.endsWith('/governance/workspaces') || url.pathname.endsWith('/workspaces')) return reply([workspace])
    if (url.pathname.endsWith(`/workspaces/${workspace.id}/projects`)) return reply([{ id: projectId, workspace_id: workspace.id, name: 'E2E Project', primary_workflow_id: workflowId }])
    if (url.pathname.includes('/operations-inbox/')) { approvalPosts += 1; return reply({}) }
    if (url.pathname.endsWith(`/workspaces/${workspace.id}/operations-inbox`)) {
      const type = url.searchParams.get('type')
      const status = url.searchParams.get('status')
      const proposal = { id: proposalId, workspace_id: workspace.id, type: 'change_proposal', status: resolved ? 'resolved' : 'open', severity: 'low', priority: 'normal', owning_team_id: null, assignee_id: null, author_actor_type: 'agent', author_actor_id: 'agent-e2e', reason: '创建项目工作流并保存结果。', parent_id: null, proposal_id: proposalId, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:02:00Z', evidence: { conversation_id: conversationId, project_id: projectId, workflow_id: workflowId, run_id: runId, proposal_version: 'v1', ...(resolved ? { execution: { result: { summary: '项目与工作流已创建。', workspace_id: workspace.id, project_id: projectId, workflow_id: workflowId } } } : {}) } }
      return reply(type === 'change_proposal' && status === (resolved ? 'resolved' : 'open') ? [proposal] : [])
    }
    if (url.pathname.endsWith('/tasks') || url.pathname.endsWith('/notifications/logs') || url.pathname.endsWith('/control/actions')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: [], meta: { total: 0, page: 1, pages: 1, limit: 100 } }) })
    if (url.pathname.endsWith('/chat/sessions')) return reply([{ id: conversationId, workspace_id: workspace.id, title: '原始创建会话', status: 'active', context_binding: { project_id: projectId, workflow_id: workflowId, run_id: runId }, revision: 1, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z' }])
    if (url.pathname.endsWith(`/chat/sessions/${conversationId}/messages`) && route.request().method() === 'POST') {
      followUpBody = route.request().postDataJSON()
      const turn = { sequence: 2, request_id: followUpBody.request_id, status: 'completed', user_content: followUpBody.content, response: { type: 'message', content: '已根据当前运行上下文整理下一步。' }, context_binding: followUpBody.context, tool_trace: [] }
      turns.push(turn)
      return reply({ conversation_id: conversationId, turn })
    }
    if (url.pathname.endsWith(`/chat/sessions/${conversationId}`)) return reply({ id: conversationId, workspace_id: workspace.id, title: '原始创建会话', status: 'active', context_binding: { project_id: projectId, workflow_id: workflowId, run_id: runId }, revision: 1, turns })
    if (url.pathname.endsWith('/chat/confirm')) { resolved = true; return reply({ applied: true, workspace_id: workspace.id, project_id: projectId, workflow_id: workflowId }) }
    return reply([])
  })

  await page.goto(`/inbox?workspace=${workspace.id}`)
  await expect(page.getByRole('heading', { name: '等待确认的 Agent 提案' })).toBeVisible()
  await expect(page.getByTestId('inbox-conversation-thread')).toBeVisible()
  await page.getByLabel('向原 Agent 追问').fill('说明失败原因，并给出修复步骤')
  await page.getByRole('button', { name: '发送追问' }).click()
  await expect.poll(() => followUpBody?.content).toBe('说明失败原因，并给出修复步骤')
  expect(followUpBody.context).toEqual({ project_id: projectId, workflow_id: workflowId, run_id: runId, surface: 'inbox_result' })
  await expect(page.getByText('已根据当前运行上下文整理下一步。')).toBeVisible()
  await page.getByRole('link', { name: '继续原会话' }).click()
  await page.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByRole('dialog', { name: '全局 Agent' })).toBeVisible()
  await expect(page.getByLabel('选择 Agent 会话')).toHaveValue(conversationId)
  await page.getByRole('button', { name: '确认执行' }).click()
  await expect.poll(() => resolved).toBe(true)
  expect(approvalPosts).toBe(0)

  await page.goto(`/inbox?workspace=${workspace.id}`)
  await page.getByRole('button', { name: '项目动态' }).click()
  await expect(page.getByText('项目与工作流已创建。')).toBeVisible()
  await expect(page.getByRole('link', { name: '继续原会话' })).toHaveAttribute('href', new RegExp(`conversation=${conversationId}`))
  await expect(page.getByRole('link', { name: '查看项目' })).toHaveAttribute('href', new RegExp(projectId))
  expect(pageErrors).toEqual([])
})
