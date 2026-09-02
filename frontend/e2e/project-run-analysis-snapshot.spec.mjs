import { expect, test } from '@playwright/test'

const WORKSPACE_ID = 'workspace-1'
const PROJECT_ID = 'project-1'
const WORKFLOW_ID = 'workflow-1'
const RUN_ID = 'run-completed'
const SNAPSHOT_BASE = `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/workflows/${WORKFLOW_ID}/runs/${RUN_ID}/analysis-snapshots`
const SOURCE_RANGE = {
  startAt: '2026-09-01T00:00:00.000Z',
  endAt: '2026-09-01T01:00:00.000Z',
}

const identity = {
  subject: 'snapshot-operator',
  email: null,
  name: 'Snapshot Operator',
  username: 'snapshot-operator',
  picture: null,
  is_platform_admin: true,
  auth_method: 'bootstrap',
}

const project = {
  id: PROJECT_ID,
  workspace_id: WORKSPACE_ID,
  name: 'Acquisition reliability',
  slug: 'acquisition-reliability',
  description: null,
  app_type: 'workflow',
  primary_workflow_id: WORKFLOW_ID,
  created_by_user_id: identity.subject,
  archived: false,
  created_at: SOURCE_RANGE.startAt,
  updated_at: SOURCE_RANGE.endAt,
}

const workflow = {
  id: WORKFLOW_ID,
  project_id: PROJECT_ID,
  name: 'Daily acquisition',
  description: null,
  current_published_version: 3,
  archived: false,
  created_at: SOURCE_RANGE.startAt,
  updated_at: SOURCE_RANGE.endAt,
}

const runLog = {
  run_id: RUN_ID,
  workflow_id: WORKFLOW_ID,
  workflow_name: workflow.name,
  workflow_version: 3,
  trace_id: 'trace-1',
  status: 'completed',
  trigger: 'manual',
  response_mode: 'async',
  event_count: 91,
  node_count: 4,
  error_count: 1,
  duration_ms: 3_600_000,
  started_at: SOURCE_RANGE.startAt,
  updated_at: SOURCE_RANGE.endAt,
}

const receipt = (overrides = {}) => ({
  snapshotId: 'snapshot-ready-1',
  runId: RUN_ID,
  runtime: 'questdb',
  status: 'completed',
  schemaVersion: 1,
  redactionVersion: 1,
  sourceRange: SOURCE_RANGE,
  rowCounts: {
    workflowTraceEvents: 7,
    acquisitionExecutionMetrics: 3,
    total: 10,
  },
  createdAt: '2026-09-01T01:01:00.000Z',
  completedAt: '2026-09-01T01:01:02.000Z',
  expiresAt: '2026-10-01T01:01:02.000Z',
  failureCode: null,
  ...overrides,
})

const summary = {
  snapshotId: 'snapshot-ready-1',
  sourceRange: SOURCE_RANGE,
  throughput: { total: 10, perMinute: 32.5 },
  latency: { sampleCount: 3, averageMs: 87, p95Ms: 144, maxMs: 169 },
  failureRate: { failed: 1, total: 10, rate: 0.1 },
  eventTypes: [
    { eventType: 'node.completed', count: 6 },
    { eventType: 'node.failed', count: 1 },
  ],
  nodes: [
    { nodeId: 'collector-1', eventCount: 5, failureCount: 1 },
    { nodeId: null, eventCount: 2, failureCount: 0 },
  ],
}

const response = (data, extra = {}) => ({
  contentType: 'application/json',
  body: JSON.stringify({ success: true, data, ...extra }),
})

async function mockOperationsPage(page, options = {}) {
  const {
    capabilityState = 'ready',
    capabilityReasonCode = capabilityState === 'ready' ? 'ready' : `${capabilityState}_by_policy`,
    capabilityExtra = {},
    receipts = [],
    previewRowCounts = {
      workflowTraceEvents: 7,
      acquisitionExecutionMetrics: 3,
      total: 10,
    },
    previewSourceRange = null,
    exportedReceipt = receipt(),
    receiptExtra = {},
    resolveReceipt = (selected) => selected,
    summaryPayload = summary,
    summaryFailure = null,
    holdExport = false,
    selectedRun = runLog,
  } = options

  const requests = {
    previewBodies: [],
    exportBodies: [],
    capabilityCalls: 0,
    receiptCalls: 0,
    summaryCalls: 0,
    traceCalls: 0,
  }
  let releaseExport = () => {}
  let markExportStarted = () => {}
  const exportStarted = new Promise((resolve) => { markExportStarted = resolve })
  const exportGate = new Promise((resolve) => { releaseExport = resolve })

  await page.addInitScript(() => {
    sessionStorage.setItem('opencli.bootstrapIdentityToken', 'snapshot-test-token')
  })

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path.endsWith('/auth/me')) {
      await route.fulfill(response(identity))
      return
    }
    if (path === `/api/v1/workspaces/${WORKSPACE_ID}/projects`) {
      await route.fulfill(response([project]))
      return
    }
    if (path === `/api/v1/workspaces/${WORKSPACE_ID}/projects/${PROJECT_ID}/workflows`) {
      await route.fulfill(response([workflow]))
      return
    }
    if (path.endsWith('/runtime-summary')) {
      await route.fulfill(response({
        total_runs: 1,
        successful_runs: selectedRun.status === 'completed' ? 1 : 0,
        failed_runs: 0,
        blocked_runs: 0,
        running_runs: 0,
        total_events: selectedRun.event_count,
        recent_logs: [selectedRun],
      }))
      return
    }
    if (path.endsWith('/runtime-logs')) {
      await route.fulfill(response([selectedRun], {
        meta: { total: 1, page: 1, limit: 20, pages: 1 },
      }))
      return
    }
    if (path.endsWith(`/runs/${selectedRun.run_id}/trace`)) {
      requests.traceCalls += 1
      await route.fulfill(response({
        workflow_version: 3,
        inputs: {},
        user: identity.username,
        response_mode: 'async',
        trace: {
          projection: {
            workflowId: WORKFLOW_ID,
            runId: selectedRun.run_id,
            traceId: selectedRun.trace_id,
            valid: true,
            status: selectedRun.status,
            startedAt: SOURCE_RANGE.startAt,
            updatedAt: SOURCE_RANGE.endAt,
            eventCount: 91,
            nodeStates: [],
            errors: [],
          },
          checkpoint: {
            checkpointId: 'checkpoint-1',
            workflowId: WORKFLOW_ID,
            runId: selectedRun.run_id,
            traceId: selectedRun.trace_id,
            status: selectedRun.status,
            valid: true,
            eventCount: 91,
            lastSequence: 91,
            updatedAt: SOURCE_RANGE.endAt,
            nodeStates: [],
            sourceOutputNodeIds: [],
            sourceOutputItemCount: 0,
            canContinueWithSourceOutputs: false,
            continuationPath: '',
            tracePath: '',
          },
          events: [],
          filters: { afterSequence: 0, limit: 50 },
          nextAfterSequence: 0,
        },
      }))
      return
    }
    if (path === `${SNAPSHOT_BASE}/capability`) {
      requests.capabilityCalls += 1
      await route.fulfill(response({
        runtime: 'questdb',
        state: capabilityState,
        reasonCode: capabilityReasonCode,
        ...capabilityExtra,
      }))
      return
    }
    if (path === `${SNAPSHOT_BASE}/preview` && method === 'POST') {
      const body = request.postDataJSON()
      requests.previewBodies.push(body)
      await route.fulfill(response({
        runId: RUN_ID,
        sourceRange: previewSourceRange ?? body,
        rowCounts: previewRowCounts,
        eligible: previewRowCounts.total > 0,
      }))
      return
    }
    if (path.endsWith('/summary')) {
      requests.summaryCalls += 1
      if (summaryFailure) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: summaryFailure }),
        })
      } else {
        await route.fulfill(response(summaryPayload))
      }
      return
    }
    if (path === SNAPSHOT_BASE && method === 'POST') {
      const body = request.postDataJSON()
      requests.exportBodies.push(body)
      markExportStarted()
      if (holdExport) await exportGate
      await route.fulfill(response({ ...exportedReceipt, sourceRange: body, ...receiptExtra }))
      return
    }
    if (path === SNAPSHOT_BASE && method === 'GET') {
      await route.fulfill(response(receipts.map((item) => ({ ...item, ...receiptExtra }))))
      return
    }
    if (path.startsWith(`${SNAPSHOT_BASE}/`) && method === 'GET') {
      const snapshotId = decodeURIComponent(path.slice(SNAPSHOT_BASE.length + 1))
      const selected = receipts.find((item) => item.snapshotId === snapshotId) ?? exportedReceipt
      requests.receiptCalls += 1
      await route.fulfill(response({ ...resolveReceipt(selected, requests.receiptCalls), ...receiptExtra }))
      return
    }

    await route.fulfill(response([]))
  })

  return { exportStarted, releaseExport, requests }
}

async function openRunTrace(page) {
  await page.goto(`/studio/projects/${PROJECT_ID}/operations?workspace=${WORKSPACE_ID}`)
  await expect(page.getByText('Daily acquisition')).toBeVisible()
  await page.getByRole('button', { name: 'Trace' }).click()
  await expect(page.getByRole('heading', { name: /Run Trace/ })).toBeVisible()
  return page.locator('section[aria-labelledby="run-analysis-snapshot-title"]')
}

test('completed run previews and exports an explicitly bounded UTC snapshot', async ({ page }) => {
  const harness = await mockOperationsPage(page, { holdExport: true })
  const panel = await openRunTrace(page)

  await expect(panel.getByRole('heading', { name: '分析快照' })).toBeVisible()
  await expect(panel.getByText('可创建', { exact: true })).toBeVisible()
  await panel.getByLabel('开始时间（UTC）').fill('2026-09-01T00:10')
  await panel.getByLabel('结束时间（UTC）').fill('2026-09-01T00:40')
  await panel.getByRole('button', { name: '预览范围' }).click()

  await expect(panel.getByText('共 10 行可导出')).toBeVisible()
  expect(harness.requests.previewBodies).toEqual([{
    startAt: '2026-09-01T00:10:00.000Z',
    endAt: '2026-09-01T00:40:00.000Z',
  }])

  await panel.getByRole('button', { name: '创建分析快照' }).click()
  await harness.exportStarted
  await expect(panel.getByText('正在导出已脱敏快照')).toBeVisible()
  harness.releaseExport()

  await expect(panel.getByText('快照已完成').last()).toBeVisible()
  await expect(panel.getByText('snapshot-ready-1')).toBeVisible()
  await expect(panel.getByText('32.5 / 分钟')).toBeVisible()
  await expect(panel.getByText('87 ms 平均')).toBeVisible()
  await expect(panel.getByText('10.0%')).toBeVisible()
  await expect(panel.getByText('node.completed')).toBeVisible()
  await expect(panel.getByText('collector-1')).toBeVisible()
  expect(harness.requests.exportBodies).toEqual(harness.requests.previewBodies)
  expect(harness.requests.traceCalls).toBeGreaterThan(0)
})

test('rounds precise Run bounds inward and rejects out-of-bounds milliseconds', async ({ page }) => {
  const preciseRun = {
    ...runLog,
    started_at: '2026-09-01T00:00:00.123456Z',
    updated_at: '2026-09-01T00:59:59.987654Z',
  }
  const harness = await mockOperationsPage(page, { selectedRun: preciseRun })
  const panel = await openRunTrace(page)
  const startInput = panel.getByLabel('开始时间（UTC）')
  const endInput = panel.getByLabel('结束时间（UTC）')
  const previewButton = panel.getByRole('button', { name: '预览范围' })

  await expect(startInput).toHaveAttribute('step', '0.001')
  await expect(startInput).toHaveAttribute('min', '2026-09-01T00:00:00.124')
  await expect(endInput).toHaveAttribute('max', '2026-09-01T00:59:59.987')
  await expect(startInput).toHaveValue('2026-09-01T00:00:00.124')
  await expect(endInput).toHaveValue('2026-09-01T00:59:59.987')

  await startInput.fill('2026-09-01T00:00:00.123')
  const rangeAlert = panel.getByRole('alert')
  await expect(rangeAlert).toContainText('开始时间不能早于此 Run 的开始时间')
  await expect(startInput).toHaveAttribute('aria-invalid', 'true')
  const rangeAlertId = await rangeAlert.getAttribute('id')
  expect((await startInput.getAttribute('aria-describedby'))?.split(' ')).toContain(rangeAlertId)
  await expect(previewButton).toBeDisabled()

  await startInput.fill('2026-09-01T00:00:00.124')
  await endInput.fill('2026-09-01T00:59:59.988')
  await expect(panel.getByRole('alert')).toContainText('结束时间不能晚于此 Run 的结束时间')
  await expect(previewButton).toBeDisabled()

  await endInput.fill('2026-09-01T00:59:59.987')
  await previewButton.click()
  await expect(panel.getByText('共 10 行可导出')).toBeVisible()
  expect(harness.requests.previewBodies).toEqual([{
    startAt: '2026-09-01T00:00:00.124Z',
    endAt: '2026-09-01T00:59:59.987Z',
  }])
})

test('requires a preview that matches the current range before export', async ({ page }) => {
  await mockOperationsPage(page, { previewSourceRange: SOURCE_RANGE })
  const panel = await openRunTrace(page)

  await panel.getByLabel('开始时间（UTC）').fill('2026-09-01T00:10')
  await panel.getByLabel('结束时间（UTC）').fill('2026-09-01T00:40')
  await panel.getByRole('button', { name: '预览范围' }).click()

  await expect(panel.getByText('范围已更改')).toBeVisible()
  await expect(panel.getByText('当前预览不再匹配输入范围')).toBeVisible()
  await expect(panel.getByRole('button', { name: '创建分析快照' })).toBeDisabled()
  await expect(panel.getByText('共 10 行可导出')).toHaveCount(0)
})

test('restores an authoritative receipt and keeps a summary failure section-local', async ({ page }) => {
  const restored = receipt()
  await mockOperationsPage(page, {
    receipts: [restored],
    capabilityExtra: {
      endpoint: 'https://private-runtime.invalid',
      credential: 'capability-secret',
    },
    receiptExtra: {
      rawPayload: 'payload-secret',
      message: 'receipt-secret',
      token: 'token-secret',
    },
    summaryFailure: 'summary-secret-database-detail',
  })
  const panel = await openRunTrace(page)

  await expect(panel.getByText('已恢复 1 个快照回执')).toBeVisible()
  await expect(panel.getByText('snapshot-ready-1')).toBeVisible()
  await expect(panel.getByText('快照已完成').last()).toBeVisible()
  await expect(panel.getByText('摘要暂时不可用')).toBeVisible()
  await expect(panel.getByText('共 10 行')).toBeVisible()
  await expect(panel).not.toContainText('91 / 分钟')
  await expect(panel).not.toContainText('private-runtime.invalid')
  await expect(panel).not.toContainText('capability-secret')
  await expect(panel).not.toContainText('payload-secret')
  await expect(panel).not.toContainText('receipt-secret')
  await expect(panel).not.toContainText('token-secret')
  await expect(panel).not.toContainText('summary-secret-database-detail')
  await expect(panel.getByLabel(/SQL/i)).toHaveCount(0)
})

for (const scenario of [
  { state: 'disabled', label: '未启用' },
  { state: 'unavailable', label: '不可用' },
  { state: 'unhealthy', label: '运行异常' },
]) {
  test(`renders the ${scenario.state} capability state without export actions`, async ({ page }) => {
    await mockOperationsPage(page, { capabilityState: scenario.state })
    const panel = await openRunTrace(page)

    await expect(panel.getByText(scenario.label, { exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: '预览范围' })).toHaveCount(0)
    await expect(panel.getByRole('button', { name: '创建分析快照' })).toHaveCount(0)
  })
}

test('empty preview stays non-exportable', async ({ page }) => {
  await mockOperationsPage(page, {
    previewRowCounts: {
      workflowTraceEvents: 0,
      acquisitionExecutionMetrics: 0,
      total: 0,
    },
  })
  const panel = await openRunTrace(page)

  await panel.getByRole('button', { name: '预览范围' }).click()
  await expect(panel.getByText('所选范围内没有可导出的事件')).toBeVisible()
  await expect(panel.getByRole('button', { name: '创建分析快照' })).toBeDisabled()
})

test('mobile Run detail keeps UTC controls touch-sized without sheet overflow', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 760 })
  await mockOperationsPage(page)
  const panel = await openRunTrace(page)

  const startBox = await panel.getByLabel('开始时间（UTC）').boundingBox()
  const previewBox = await panel.getByRole('button', { name: '预览范围' }).boundingBox()
  expect(startBox?.height).toBeGreaterThanOrEqual(44)
  expect(previewBox?.height).toBeGreaterThanOrEqual(44)
  await expect(page.locator('[data-slot="sheet-content"]')).toBeVisible()
  expect(await page.locator('[data-slot="sheet-content"]').evaluate(
    (element) => element.scrollWidth <= element.clientWidth + 1,
  )).toBe(true)
})

test('restores failed and expired receipts as distinct public states', async ({ page }) => {
  const failed = receipt({
    snapshotId: 'snapshot-failed-1',
    status: 'failed',
    completedAt: null,
    failureCode: 'runtime_write_failed',
  })
  const expired = receipt({
    snapshotId: 'snapshot-expired-1',
    status: 'expired',
  })
  const harness = await mockOperationsPage(page, { receipts: [failed, expired] })
  const panel = await openRunTrace(page)

  await expect(panel.getByText('快照创建失败').last()).toBeVisible()
  await expect(panel.getByText('runtime_write_failed')).toBeVisible()
  await panel.getByRole('button', { name: '打开快照 snapshot-expired-1' }).click()
  await expect(panel.getByText('快照已过期').last()).toBeVisible()
  expect(harness.requests.summaryCalls).toBe(0)
})

test('polls a restored exporting receipt to completion and updates its history state', async ({ page }) => {
  const exporting = receipt({ status: 'exporting', completedAt: null })
  const completed = receipt()
  const harness = await mockOperationsPage(page, {
    receipts: [exporting],
    resolveReceipt: (_selected, call) => call === 1 ? exporting : completed,
  })
  const panel = await openRunTrace(page)
  const historyItem = panel.getByRole('button', { name: '打开快照 snapshot-ready-1' })

  await expect(historyItem).toContainText('快照正在导出')
  await expect(historyItem).toContainText('快照已完成', { timeout: 5_000 })
  await expect(panel.getByText('固定分析摘要')).toBeVisible()
  expect(harness.requests.receiptCalls).toBeGreaterThanOrEqual(2)
})

test('a non-completed run never requests analysis capability', async ({ page }) => {
  const partialRun = { ...runLog, status: 'partial_success' }
  const harness = await mockOperationsPage(page, { selectedRun: partialRun })
  const panel = await openRunTrace(page)

  await expect(panel.getByText('仅已完成运行')).toBeVisible()
  expect(harness.requests.capabilityCalls).toBe(0)
})
