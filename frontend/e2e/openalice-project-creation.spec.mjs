import { expect, test } from '@playwright/test'

const workspace = { id: '392b8818-d617-4f08-93d4-d7c8e60d8693', name: 'Research', slug: 'research', active: true }
const secondWorkspace = { ...workspace, id: '492b8818-d617-4f08-93d4-d7c8e60d8693', name: 'Other' }
const projectId = '5f105431-15b4-45a6-9ca1-1572f2732668'
const workflowId = '1c79187f-07a9-4973-a995-798108edbee6'

async function fixtures(page, { failFirst = false } = {}) {
  const requests = []
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-token'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const reply = (data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data }) })
    if (path.endsWith('/auth/me')) return reply({ subject: 'test-user', name: 'Tester', is_platform_admin: true })
    if (path.endsWith('/workspaces')) return reply([workspace, secondWorkspace])
    if (path.endsWith('/projects')) return reply([])
    if (path.endsWith('/chat/sessions')) return reply([])
    if (path.endsWith('/generate-workflow')) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: 'fixture: model unavailable' }) })
    if (path.endsWith('/projects/bootstrap')) {
      const body = route.request().postDataJSON()
      requests.push({ path, body })
      if (failFirst && requests.length === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '创建服务暂时不可用' }) })
      return reply({ project: { ...body.project, id: projectId, workspace_id: workspace.id }, primary_workflow: { ...body.workflow, id: workflowId }, draft: { graph: body.workflow.graph, revision: 1 } })
    }
    return reply({})
  })
  return { requests, pageErrors }
}

test('shared blank creation keeps the name on failure and submits the canonical bootstrap payload on retry', async ({ page }) => {
  const state = await fixtures(page, { failFirst: true })
  await page.goto(`/studio?workspace=${workspace.id}`)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await page.getByRole('menuitem', { name: '创建空白工作流' }).click()
  await page.getByLabel('项目名称', { exact: true }).fill('公开新闻采集')
  await page.getByRole('button', { name: '创建并打开' }).click()
  await expect(page.getByRole('alert').filter({ hasText: '创建服务暂时不可用' })).toBeVisible()
  await expect(page.getByLabel('项目名称', { exact: true })).toHaveValue('公开新闻采集')
  // Keep this test on the creation surface; the target editor has its own lifecycle suite.
  await page.route('**/studio/workflow?**', (route) => route.fulfill({ contentType: 'text/html', body: '<main>已打开工作流草稿</main>' }))
  await page.getByRole('button', { name: '创建并打开' }).click()
  await expect(page).toHaveURL(new RegExp(`workspace=${workspace.id}&project=${projectId}&workflow=${workflowId}`))
  expect(state.requests).toHaveLength(2)
  expect(state.requests[1].body.project.name).toBe('公开新闻采集')
  expect(state.requests[1].body.workflow.graph.nodes.length).toBeGreaterThan(0)
  expect(state.pageErrors).toEqual([])
})

test('mobile goal creation preserves input across views and binds the saved draft to its original workspace', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await fixtures(page)
  await page.goto(`/studio/new?workspace=${workspace.id}`)
  const input = page.getByPlaceholder('描述你希望项目持续完成的工作…')
  await expect(input).toBeVisible()
  await input.fill('每天采集 https://example.com/news 保存到数据记录')
  await page.getByRole('button', { name: '工作流方案', exact: true }).click()
  await expect(input).toBeHidden()
  await page.getByRole('button', { name: 'Agent 对话', exact: true }).click()
  await expect(input).toHaveValue('每天采集 https://example.com/news 保存到数据记录')
  await page.getByRole('button', { name: '发送需求', exact: true }).click()
  await expect.poll(() => state.requests.length).toBe(1)
  await expect(page.getByText('Project Draft r1 已保存', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '选择项目 Workspace' })).toBeDisabled()
  expect(state.requests[0].path).toContain(`/workspaces/${workspace.id}/projects/bootstrap`)
  expect(state.requests[0].body.project.app_type).toBe('agent')
  await page.getByRole('button', { name: '工作流方案', exact: true }).click()
  await expect(page.getByRole('button', { name: '打开正式编辑器' })).toBeVisible()
  const canvas = page.locator('.react-flow').first()
  const bounds = await canvas.boundingBox()
  const nodeBoxes = await page.locator('.react-flow__node').evaluateAll((nodes) => nodes.map((node) => {
    const { x, width } = node.getBoundingClientRect()
    return { x, width }
  }))
  expect(nodeBoxes.length).toBeGreaterThan(0)
  for (const node of nodeBoxes) {
    expect(node.width).toBeGreaterThan(170)
    expect(node.x).toBeGreaterThanOrEqual(bounds.x)
    expect(node.x + node.width).toBeLessThanOrEqual(bounds.x + bounds.width)
  }
  await expect(page.locator('.react-flow__minimap')).toBeHidden()
  expect(state.pageErrors).toEqual([])
  await expect(page.getByText('第一个有效意图已创建 durable Project Draft', { exact: true })).toBeHidden({ timeout: 10000 })
  await page.screenshot({ path: 'test-results/openalice-project-mobile.png', fullPage: true })
})
