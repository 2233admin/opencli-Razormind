import { readFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

const TOKEN = 'openalice-e2e-token'
const WORKSPACE_ID = 'openalice-e2e-workspace'
const PROJECT_ALPHA = 'openalice-project-alpha'
const PROJECT_BETA = 'openalice-project-beta'
const WORKFLOW_ALPHA = 'openalice-workflow-alpha'
const WORKFLOW_BETA = 'openalice-workflow-beta'
const RUN_ALPHA_ONE = 'openalice-run-alpha-1'
const RUN_ALPHA_TWO = 'openalice-run-alpha-2'
const RUN_BETA_ONE = 'openalice-run-beta-1'
const ARTIFACT_ALPHA_ONE = 'openalice-session-alpha-1:report-alpha-one'
const ARTIFACT_ALPHA_TWO = 'openalice-session-alpha-2:report-alpha-two'
const ARTIFACT_BETA_ONE = 'openalice-session-beta-1:report-beta-one'
const CONVERSATION_ACTIVE = 'openalice-conversation-active'
const CONVERSATION_CLOSED = 'openalice-conversation-closed'

test.describe.configure({ mode: 'serial' })

function dataUrl({ projectId, workflowId, runId, artifactId, view = 'dataset' }) {
  const params = new URLSearchParams({
    workspace: WORKSPACE_ID,
    workflow: workflowId,
    run: runId,
    view,
  })
  if (artifactId) params.set('artifact', artifactId)
  return `/studio/projects/${projectId}/data?${params.toString()}`
}

async function apiJson(page, pathname, options = {}) {
  const response = await page.request.fetch(pathname, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, ...(options.headers ?? {}) },
  })
  expect(response.ok(), `${options.method ?? 'GET'} ${pathname} returned ${response.status()}`).toBeTruthy()
  return response.json()
}

async function downloadText(download) {
  const filePath = await download.path()
  expect(filePath).toBeTruthy()
  return readFile(filePath, 'utf8')
}

test('real project, run, artifact, conversation, Inbox, and export journey stays scoped', async ({ page }) => {
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript((token) => sessionStorage.setItem('opencli.bootstrapIdentityToken', token), TOKEN)

  const alphaOneArtifacts = await apiJson(
    page,
    `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ALPHA}/artifacts?workflow_id=${WORKFLOW_ALPHA}&run_id=${RUN_ALPHA_ONE}`,
  )
  expect(alphaOneArtifacts.data).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: ARTIFACT_ALPHA_ONE, project_id: PROJECT_ALPHA, workflow_id: WORKFLOW_ALPHA, run_id: RUN_ALPHA_ONE }),
    expect.objectContaining({ id: 'evidence-batch:batch-alpha-1', project_id: PROJECT_ALPHA, workflow_id: WORKFLOW_ALPHA, run_id: RUN_ALPHA_ONE, kind: 'evidence_batch' }),
  ]))
  expect(alphaOneArtifacts.data.every((item) => item.project_id === PROJECT_ALPHA && item.run_id === RUN_ALPHA_ONE)).toBe(true)

  const betaArtifacts = await apiJson(
    page,
    `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_BETA}/artifacts?workflow_id=${WORKFLOW_BETA}&run_id=${RUN_BETA_ONE}`,
  )
  expect(betaArtifacts.data).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: ARTIFACT_BETA_ONE, project_id: PROJECT_BETA, run_id: RUN_BETA_ONE }),
  ]))
  expect(betaArtifacts.data.some((item) => item.project_id === PROJECT_ALPHA || item.run_id === RUN_ALPHA_ONE)).toBe(false)

  const alphaOneRecords = await apiJson(
    page,
    `/api/v1/records?project_id=${PROJECT_ALPHA}&workflow_id=${WORKFLOW_ALPHA}&workflow_run_id=${RUN_ALPHA_ONE}&limit=100`,
  )
  expect(alphaOneRecords.data.map((record) => record.id)).toEqual(['openalice-record-alpha-1'])
  expect(alphaOneRecords.data[0].normalized_data.title).toBe('Alpha run one record')

  const alphaTwoRecords = await apiJson(
    page,
    `/api/v1/records?project_id=${PROJECT_ALPHA}&workflow_id=${WORKFLOW_ALPHA}&workflow_run_id=${RUN_ALPHA_TWO}&limit=100`,
  )
  expect(alphaTwoRecords.data.map((record) => record.id)).toEqual(['openalice-record-alpha-2'])

  const alphaDetail = await apiJson(
    page,
    `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ALPHA}/artifacts/${encodeURIComponent(ARTIFACT_ALPHA_ONE)}?workflow_id=${WORKFLOW_ALPHA}&run_id=${RUN_ALPHA_ONE}`,
  )
  expect(alphaDetail.data.content.content).toContain('Alpha report body')
  expect(alphaDetail.data.conversation_id).toBe(CONVERSATION_ACTIVE)

  await page.goto(dataUrl({ projectId: PROJECT_ALPHA, workflowId: WORKFLOW_ALPHA, runId: RUN_ALPHA_ONE, artifactId: ARTIFACT_ALPHA_ONE, view: 'dataset' }))
  await expect(page.getByTestId('project-artifacts-panel')).toBeVisible()
  await expect(page.getByText('Alpha report body')).toBeVisible()
  await expect(page.getByTestId('inbox-conversation-thread')).toBeVisible()

  const followUp = page.getByLabel('向原 Agent 追问')
  await followUp.fill('请补充 Alpha run one 的来源说明')
  const followUpRequest = page.waitForRequest((request) =>
    request.method() === 'POST' && request.url().includes(`/chat/sessions/${CONVERSATION_ACTIVE}/messages`),
  )
  await page.getByRole('button', { name: '发送追问' }).click()
  const sentRequest = await followUpRequest
  expect(sentRequest.postDataJSON().context).toEqual({
    project_id: PROJECT_ALPHA,
    workflow_id: WORKFLOW_ALPHA,
    run_id: RUN_ALPHA_ONE,
    surface: 'project_artifact',
  })
  await expect(page.getByText('Deterministic E2E reply: 请补充 Alpha run one 的来源说明')).toBeVisible()

  const persistedConversation = await apiJson(page, `/api/v1/chat/sessions/${CONVERSATION_ACTIVE}`)
  expect(persistedConversation.data.turns.at(-1).user_content).toBe('请补充 Alpha run one 的来源说明')
  expect(persistedConversation.data.turns.at(-1).context_binding).toMatchObject({
    project_id: PROJECT_ALPHA,
    workflow_id: WORKFLOW_ALPHA,
    run_id: RUN_ALPHA_ONE,
  })

  await page.keyboard.press('Escape')
  await expect(page.getByTestId('project-artifact-detail')).toBeHidden()
  await page.getByRole('button', { name: '数据集' }).click()
  await expect(page).toHaveURL(/view=dataset/)
  expect(new URL(page.url()).searchParams.get('artifact')).toBeNull()
  await expect(page.getByText('Alpha run one record', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Alpha run two record', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: '导出数据' }).click()
  const exportRequest = page.waitForRequest((request) =>
    request.method() === 'GET' && request.url().includes('/api/v1/records') && request.url().includes(`workflow_run_id=${RUN_ALPHA_ONE}`),
  )
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: /CSV（Excel 可打开）/ }).click()
  const [download, exportHttpRequest] = await Promise.all([downloadPromise, exportRequest])
  expect(exportHttpRequest.url()).toContain(`project_id=${PROJECT_ALPHA}`)
  const exportedCsv = await downloadText(download)
  expect(exportedCsv).toContain('Alpha run one record')
  expect(exportedCsv).not.toContain('Alpha run two record')
  expect(exportedCsv).not.toContain('Beta isolated record')

  await page.goto(dataUrl({ projectId: PROJECT_ALPHA, workflowId: WORKFLOW_ALPHA, runId: RUN_ALPHA_TWO, artifactId: ARTIFACT_ALPHA_TWO }))
  await expect(page.getByTestId('project-artifact-detail')).toBeVisible()
  await expect(page.getByText('会话已关闭')).toBeVisible()
  await expect(page.getByLabel('向原 Agent 追问')).toBeDisabled()

  await page.goto(dataUrl({ projectId: PROJECT_BETA, workflowId: WORKFLOW_BETA, runId: RUN_BETA_ONE, artifactId: ARTIFACT_BETA_ONE }))
  await expect(page.getByTestId('project-artifact-detail')).toBeVisible()
  await expect(page.getByText('Beta project output')).toBeVisible()
  await expect(page.getByText('这个结果没有可验证的原 Agent 会话，因此无法在收件箱中追问。')).toBeVisible()

  const inbox = await apiJson(page, `/api/v1/workspaces/${WORKSPACE_ID}/operations-inbox?type=change_proposal&status=open&limit=100`)
  expect(inbox.data).toHaveLength(1)
  expect(inbox.data[0].evidence).toMatchObject({ project_id: PROJECT_ALPHA, workflow_id: WORKFLOW_ALPHA, run_id: RUN_ALPHA_ONE })

  await page.goto(`/inbox?workspace=${WORKSPACE_ID}`)
  await expect(page.getByRole('heading', { name: '任务与通知' })).toBeVisible()
  const proposalRow = page.getByRole('option', { name: /Alpha run one review proposal/ })
  await expect(proposalRow).toBeVisible()
  await proposalRow.click()
  await expect(page.getByRole('heading', { name: 'Alpha run one review proposal' })).toBeVisible()
  await expect(page.getByTestId('project-artifacts-panel')).toBeVisible()
  await expect(page.getByRole('heading', { name: '在当前结果上追问' })).toBeVisible()
  await expect(page.getByRole('link', { name: '继续原会话' })).toBeVisible()

  expect(pageErrors).toEqual([])
})
