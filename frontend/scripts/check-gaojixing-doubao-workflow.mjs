import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'dagre') return { url: 'dagre:test-stub', shortCircuit: true }
    const candidates = []
    if (specifier.startsWith('@/')) {
      candidates.push(path.join(frontendRoot, specifier.slice(2)))
    } else if (specifier.startsWith('.') && context.parentURL?.startsWith('file:')) {
      candidates.push(path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier))
    }
    for (const candidate of candidates) {
      for (const resolvedPath of [candidate, `${candidate}.ts`, `${candidate}.tsx`]) {
        if (existsSync(resolvedPath)) {
          return { url: pathToFileURL(resolvedPath).href, shortCircuit: true }
        }
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url === 'dagre:test-stub') {
      return {
        format: 'module',
        source: 'export default { graphlib: { Graph: class Graph {} }, layout() {} }',
        shortCircuit: true,
      }
    }
    if (url.endsWith('.ts') || url.endsWith('.tsx')) {
      const source = stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
        mode: 'strip',
        sourceUrl: url,
      })
      return { format: 'module', source, shortCircuit: true }
    }
    if (url.endsWith('.json')) {
      const json = readFileSync(fileURLToPath(url), 'utf8')
      return { format: 'module', source: `export default ${json}`, shortCircuit: true }
    }
    return nextLoad(url, context)
  },
})

const importTypeScript = (relativePath) => import(pathToFileURL(path.join(frontendRoot, relativePath)).href)

const catalogSource = await readFile(new URL('../lib/workflow/node-catalog.ts', import.meta.url), 'utf8')
const workflowSource = await readFile(new URL('../lib/workflow/gaojixing-doubao-workflow.ts', import.meta.url), 'utf8')
const templateSource = await readFile(new URL('../lib/workflow/studio-templates.ts', import.meta.url), 'utf8')
const runProxySource = await readFile(new URL('../app/api/workflow/run/route.ts', import.meta.url), 'utf8')
const runPanelSource = await readFile(new URL('../components/flow/run-trace-panel.tsx', import.meta.url), 'utf8')
const researchWorkbenchesSource = await readFile(new URL('../components/flow/run-trace-research-workbenches.tsx', import.meta.url), 'utf8')

test('Gaojixing canvas uses two real deep capabilities instead of a bloated primitive graph', () => {
  assert.match(catalogSource, /id: "package\.gaojixing\.doubao-batch"/)
  assert.match(catalogSource, /"tool\.gaojixing\.doubao-batch\.run"/)
  assert.match(catalogSource, /"gaojixing_doubao_batch"/)
  assert.match(catalogSource, /id: "package\.gaojixing\.batch-certification"/)
  assert.match(catalogSource, /"tool\.gaojixing\.batch-certify"/)
  assert.match(catalogSource, /"gaojixing_batch_certify"/)

  assert.match(workflowSource, /nodes: \[trigger, collection, certification, delivery\]/)
  assert.match(workflowSource, /source: collection\.id, target: certification\.id/)
  assert.doesNotMatch(workflowSource, /captcha[-_ ]wait|human[-_ ]approval|design_only/i)
})

test('Gaojixing HDA internals match the backend single-tool materialization', async () => {
  const [{ WORKFLOW_NODE_CATALOG }, { buildGaojixingDoubaoWorkflow }] = await Promise.all([
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/gaojixing-doubao-workflow.ts'),
  ])
  const project = buildGaojixingDoubaoWorkflow('Gaojixing regression')
  const expectedTools = new Map([
    ['package.gaojixing.doubao-batch', 'tool.gaojixing.doubao-batch.run'],
    ['package.gaojixing.batch-certification', 'tool.gaojixing.batch-certify'],
  ])

  for (const [catalogId, toolId] of expectedTools) {
    const catalogItem = WORKFLOW_NODE_CATALOG.find((candidate) => candidate.id === catalogId)
    assert.ok(catalogItem, `missing catalog item ${catalogId}`)
    assert.equal(catalogItem.topicCollapse?.nodeCount, 1)
    assert.equal(catalogItem.internals?.locked, true)
    assert.deepEqual(catalogItem.internals?.nodes.map((node) => node.id), ['tool'])
    assert.equal(catalogItem.internals?.nodes[0]?.ui.catalogId, 'external.tool.capability')
    assert.equal(catalogItem.internals?.nodes[0]?.params.toolCapability?.id, toolId)
    assert.deepEqual(catalogItem.internals?.edges, [])
    if (catalogId === 'package.gaojixing.doubao-batch') {
      assert.equal(catalogItem.params.feishuWebhookEnv, 'GAOJIXING_FEISHU_WEBHOOK_URL')
      assert.equal(
        catalogItem.internals?.nodes[0]?.params.toolParams?.feishuWebhookEnv,
        'GAOJIXING_FEISHU_WEBHOOK_URL',
      )
    }

    const workflowNode = project.nodes.find((node) => node.ui.catalogId === catalogId)
    assert.ok(workflowNode, `missing instantiated node ${catalogId}`)
    assert.equal(workflowNode.topicCollapse?.nodeCount, 1)
    assert.deepEqual(workflowNode.internals?.nodes.map((node) => node.id), ['tool'])
    assert.deepEqual(workflowNode.internals?.edges, [])
  }

  const unrelatedPackage = WORKFLOW_NODE_CATALOG.find(
    (candidate) => candidate.id === 'package.intelligence.situation-awareness',
  )
  assert.ok(unrelatedPackage)
  assert.equal(unrelatedPackage.topicCollapse?.nodeCount, 2)
  assert.deepEqual(unrelatedPackage.internals?.nodes.map((node) => node.id), ['tool', 'output'])
  assert.equal(unrelatedPackage.internals?.edges.length, 1)
})

test('Gaojixing workflow has exactly four nodes and a backend-recognizable manual trigger', async () => {
  const { buildGaojixingDoubaoWorkflow } = await importTypeScript(
    'lib/workflow/gaojixing-doubao-workflow.ts',
  )
  const project = buildGaojixingDoubaoWorkflow('Gaojixing manual run')

  assert.deepEqual(
    project.nodes.map((node) => ({ id: node.id, catalogId: node.ui.catalogId })),
    [
      { id: 'trigger', catalogId: 'intelligence.schedule.cron' },
      { id: 'gaojixing-doubao-batch', catalogId: 'package.gaojixing.doubao-batch' },
      { id: 'gaojixing-batch-certification', catalogId: 'package.gaojixing.batch-certification' },
      { id: 'delivery', catalogId: 'intelligence.output.inbox' },
    ],
  )
  const trigger = project.nodes[0]
  assert.equal(trigger.kind, 'schedule')
  assert.equal(trigger.params.interval, 'manual')
  assert.equal(trigger.params.timezone, 'Asia/Shanghai')
  assert.equal(trigger.params.mode, 'manual')
  assert.equal(
    project.nodes[1].params.feishuWebhookEnv,
    'GAOJIXING_FEISHU_WEBHOOK_URL',
  )
})

test('Gaojixing certification copy promises structural evidence checks, not visual or OCR review', async () => {
  const { WORKFLOW_NODE_CATALOG } = await importTypeScript('lib/workflow/node-catalog.ts')
  const collection = WORKFLOW_NODE_CATALOG.find(
    (candidate) => candidate.id === 'package.gaojixing.doubao-batch',
  )
  const certification = WORKFLOW_NODE_CATALOG.find(
    (candidate) => candidate.id === 'package.gaojixing.batch-certification',
  )

  assert.ok(collection)
  assert.match(collection.description, /live_preflight.*独立.*只读就绪检查/)
  assert.match(collection.description, /不产生批次结果.*不进入.*终审/)
  assert.match(collection.description, /通知权限.*feishuWebhookEnv.*同时满足/)
  assert.ok(certification)
  assert.equal(certification.label, '批次证据结构终审与交付')
  assert.match(certification.description, /证据结构终审/)
  assert.match(certification.description, /文件存在/)
  assert.match(certification.description, /引用一致性/)
  assert.match(certification.description, /不执行截图视觉或 OCR 内容判定/)
})

test('Gaojixing four-node template exposes only source modes that produce certifiable batches', async () => {
  const { buildGaojixingDoubaoWorkflow } = await importTypeScript(
    'lib/workflow/gaojixing-doubao-workflow.ts',
  )

  assert.match(templateSource, /id: 'gaojixing-doubao-evidence'/)
  assert.match(templateSource, /return buildGaojixingDoubaoWorkflow\(name\)/)
  assert.match(workflowSource, /sourceMode \?\? 'project_archive'/)
  assert.match(workflowSource, /sourceMode\?: 'offline_fixture' \| 'project_archive'/)
  assert.doesNotMatch(workflowSource, /sourceMode\?:[^\n]*live_preflight/)
  assert.match(workflowSource, /sourceMode === 'offline_fixture'/)
  assert.throws(
    () => buildGaojixingDoubaoWorkflow('Invalid preflight batch', { sourceMode: 'live_preflight' }),
    /live_preflight.*does not produce a certifiable batch/,
  )
  assert.match(workflowSource, /canFetchNetwork: false/)
  assert.match(workflowSource, /canSendNotifications: true/)
  assert.match(workflowSource, /questionBank/)
  assert.doesNotMatch(workflowSource, /questionBankPath|projectRoot/)
  assert.match(workflowSource, /feishuWebhookEnv/)
})

test('Gaojixing template requires a fresh question batch per run and never persists batch counts', async () => {
  const { buildGaojixingDoubaoWorkflow } = await importTypeScript(
    'lib/workflow/gaojixing-doubao-workflow.ts',
  )
  const project = buildGaojixingDoubaoWorkflow('Fresh questions every run')
  const trigger = project.nodes.find((node) => node.id === 'trigger')
  const collection = project.nodes.find((node) => node.id === 'gaojixing-doubao-batch')
  const certification = project.nodes.find((node) => node.id === 'gaojixing-batch-certification')

  assert.ok(trigger)
  assert.deepEqual(trigger.params.inputSchema, {
    type: 'object',
    additionalProperties: false,
    required: ['questionBank'],
    properties: {
      questionBank: {
        type: 'string',
        format: 'binary',
        title: '本次题库',
        accept: '.json,.xls,.xlsx,application/json,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      },
    },
  })
  assert.ok(collection)
  assert.ok(certification)
  assert.equal(collection.params.sourceMode, 'project_archive')
  assert.equal(certification.params.sourceMode, 'project_archive')
  for (const node of [collection, certification]) {
    assert.equal('questionBankPath' in node.params, false)
    assert.equal('projectRoot' in node.params, false)
    assert.equal('phase1Expected' in node.params, false)
    assert.equal('phase2Expected' in node.params, false)
  }

  assert.doesNotMatch(workflowSource, /phase1Expected|phase2Expected/)
  assert.doesNotMatch(workflowSource, /questionBankPath\?:|projectRoot\?:/)
  assert.match(catalogSource, /每次运行.*新题包/)
  const templateCopy = templateSource.match(/id: 'gaojixing-doubao-evidence'[^\n]+/)?.[0] ?? ''
  assert.match(templateCopy, /每次运行.*新题包/)
  assert.doesNotMatch(templateCopy, /446|32|默认离线夹具/)
})

test('Workflow Run uploads one fresh question bank file and recognizes the Gaojixing trigger as manual', async () => {
  const [{ buildGaojixingDoubaoWorkflow }, { startWorkflowRun }] = await Promise.all([
    importTypeScript('lib/workflow/gaojixing-doubao-workflow.ts'),
    importTypeScript('lib/workflow/backend-runs.ts'),
  ])
  const project = buildGaojixingDoubaoWorkflow('Fresh run input')
  const questionBankFile = new File(
    ['legacy-excel-bytes'],
    '本次题库.xls',
    { type: 'application/vnd.ms-excel' },
  )
  const originalFetch = globalThis.fetch
  let requestUrl
  let requestBody
  let requestHeaders
  let fetchCalls = 0
  globalThis.fetch = async (_url, init) => {
    fetchCalls += 1
    requestUrl = String(_url)
    requestBody = init?.body
    requestHeaders = new Headers(init?.headers)
    return Response.json({ success: true, data: { runId: 'run-fresh-batch' } })
  }
  try {
    await startWorkflowRun(project, { questionBankFile })
    await assert.rejects(
      startWorkflowRun(project, {
        questionBankFile,
        sourceOutputs: { source: [{ title: 'must not be dropped' }] },
      }),
      /question bank file Runs do not accept sourceOutputs/i,
    )
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(requestUrl, '/api/workflow/run/question-bank')
  assert.ok(requestBody instanceof FormData)
  const uploadedFile = requestBody.get('questionBank')
  assert.ok(uploadedFile instanceof File)
  assert.equal(uploadedFile.name, '本次题库.xls')
  assert.deepEqual(JSON.parse(String(requestBody.get('request'))).trigger, {
    kind: 'manual',
    triggerNodeId: 'trigger',
  })
  assert.equal(requestHeaders.has('content-type'), false)
  assert.equal(fetchCalls, 1)
  assert.match(runProxySource, /body\?\.input[\s\S]*\{ input: body\.input \}/)
})

test('Studio Run presents the fresh question bank as a file contract instead of public paths', async () => {
  const [{ buildGaojixingDoubaoWorkflow }, runClient] = await Promise.all([
    importTypeScript('lib/workflow/gaojixing-doubao-workflow.ts'),
    importTypeScript('lib/workflow/backend-runs.ts'),
  ])
  const project = buildGaojixingDoubaoWorkflow('Studio fresh batch')

  assert.deepEqual(runClient.buildWorkflowRunInputTemplate(project), {})
  assert.deepEqual(runClient.getWorkflowRunFileInput(project), {
    name: 'questionBank',
    title: '本次题库',
    accept: '.json,.xls,.xlsx,application/json,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  assert.deepEqual(runClient.parseWorkflowRunInput(project, '{}'), {
    payload: {},
    source: 'operator',
  })
  const fixtureProject = buildGaojixingDoubaoWorkflow('Offline fixture', {
    sourceMode: 'offline_fixture',
  })
  assert.deepEqual(runClient.buildWorkflowRunInputTemplate(fixtureProject), {})
  assert.deepEqual(runClient.parseWorkflowRunInput(fixtureProject, '{}'), {
    payload: {},
    source: 'operator',
  })
  assert.match(runPanelSource, /type="file"/)
  assert.match(runPanelSource, /accept=\{runFileInput\.accept\}/)
  assert.match(runPanelSource, /请选择本次题库/)
  assert.match(runPanelSource, /题库文件（JSON \/ Excel）/)
  assert.match(runPanelSource, /questionBankFile/)
  assert.match(runPanelSource, /setQuestionBankFile\(null\)/)
  assert.match(
    runPanelSource,
    /ref=\{questionBankInputRef\}[\s\S]*?type="file"[\s\S]*?disabled=\{isRunning\}/,
  )
  assert.match(
    runPanelSource,
    /onClick=\{\(\) => questionBankInputRef\.current\?\.click\(\)\}[\s\S]*?disabled=\{isRunning\}/,
  )
  const runFlow = runPanelSource.slice(
    runPanelSource.indexOf('const runBackendWorkflow'),
    runPanelSource.indexOf('const runImportedOutput'),
  )
  assert.match(
    runFlow,
    /const started = scope && !runFileInput\s*\? await startWorkspaceWorkflowRun[\s\S]*?: await startWorkflowRun[\s\S]*?setQuestionBankFile\(\(current\) => current === submittedQuestionBankFile \? null : current\)[\s\S]*?monitorActiveRun\(started, authorization\)/,
  )
  assert.match(runFlow, /const submittedQuestionBankFile = questionBankFile/)
  assert.match(
    runFlow,
    /setQuestionBankFile\(\(current\) => current === submittedQuestionBankFile \? null : current\)/,
  )
  assert.match(
    runFlow,
    /questionBankInputRef\.current\?\.files\?\.\[0\] === submittedQuestionBankFile/,
  )
  assert.doesNotMatch(runPanelSource, /aria-label="本次运行输入 JSON"/)
  assert.match(runPanelSource, /\{!runFileInput \? \([\s\S]*?导入节点输出/)
  assert.match(runPanelSource, /allowSourceOutputs=\{!runFileInput\}/)
  assert.match(researchWorkbenchesSource, /const canContinue = allowSourceOutputs\s*&&/)
})

test('Question bank Run proxy streams the original bounded multipart request', async () => {
  const proxyUrl = new URL('../app/api/workflow/run/question-bank/route.ts', import.meta.url)
  const proxySource = await readFile(proxyUrl, 'utf8')
  const { POST } = await import(proxyUrl.href)
  const incoming = new FormData()
  incoming.set('questionBank', new File(['questions'], 'questions.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  }))
  incoming.set('request', JSON.stringify({ project: { id: 'project' }, trigger: { kind: 'manual' } }))
  const originalFetch = globalThis.fetch
  let forwardedUrl
  let forwardedBody
  let forwardedHeaders
  let fetchCalls = 0
  globalThis.fetch = async (url, init) => {
    fetchCalls += 1
    forwardedUrl = String(url)
    forwardedBody = init?.body
    forwardedHeaders = new Headers(init?.headers)
    return Response.json({ success: true, data: { runId: 'run-upload' } }, { status: 202 })
  }
  const incomingRequest = new Request('http://localhost/api/workflow/run/question-bank', {
    method: 'POST',
    headers: { Authorization: 'Bearer test-token' },
    body: incoming,
  })
  const incomingBody = incomingRequest.body
  const incomingContentType = incomingRequest.headers.get('content-type')
  let response
  let oversizedResponse
  try {
    response = await POST(incomingRequest)
    oversizedResponse = await POST(new Request('http://localhost/api/workflow/run/question-bank', {
      method: 'POST',
      headers: {
        'Content-Type': 'multipart/form-data; boundary=oversized',
        'Content-Length': String(6 * 1024 * 1024 + 1),
      },
      body: 'oversized',
    }))
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(response.status, 202)
  assert.match(forwardedUrl, /\/api\/v1\/workflows\/runs\/question-bank$/)
  assert.equal(forwardedBody, incomingBody)
  assert.equal(forwardedHeaders.get('authorization'), 'Bearer test-token')
  assert.equal(forwardedHeaders.get('content-type'), incomingContentType)
  assert.equal(oversizedResponse.status, 413)
  assert.equal(fetchCalls, 1)
  assert.doesNotMatch(proxySource, /\.formData\(/)
  assert.match(proxySource, /body:\s*req\.body/)
  assert.doesNotMatch(proxySource, /questionBankPath|projectRoot/)
})

const liveProjection = (runId, status, eventCount = 0) => ({
  workflowId: 'workflow-live',
  runId,
  traceId: `trace-${runId}`,
  valid: status !== 'failed',
  status,
  startedAt: '2026-08-12T00:00:00Z',
  updatedAt: '2026-08-12T00:00:01Z',
  eventCount,
  nodeStates: [],
  errors: [],
})

const tracePayload = (projection, events, nextAfterSequence) => ({
  success: true,
  data: {
    projection,
    checkpoint: {},
    events,
    filters: {},
    nextAfterSequence,
  },
})

test('Live Gaojixing Run polls a waiting upload through the completed projection', async () => {
  const [{ buildGaojixingDoubaoWorkflow }, { startWorkflowRun }, { monitorWorkflowRun }] = await Promise.all([
    importTypeScript('lib/workflow/gaojixing-doubao-workflow.ts'),
    importTypeScript('lib/workflow/backend-runs.ts'),
    importTypeScript('lib/workflow/live-run-monitor.ts'),
  ])
  const project = buildGaojixingDoubaoWorkflow('Live waiting Run')
  const scope = { workspaceId: 'workspace-live', projectId: 'project-live', workflowId: 'workflow-live' }
  const calls = []
  const snapshots = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    const requestUrl = String(url)
    calls.push({ url: requestUrl, method: init?.method ?? 'GET' })
    if ((init?.method ?? 'GET') === 'POST') {
      return Response.json({ success: true, data: liveProjection('run-live', 'waiting') }, { status: 202 })
    }
    const pollIndex = calls.filter((call) => call.method === 'GET').length
    if (pollIndex === 1) {
      return Response.json(tracePayload(liveProjection('run-live', 'waiting', 1), [{
        id: 'event-waiting',
        sequence: 1,
        workflowId: 'workflow-live',
        workflowRunId: 'run-live',
        traceId: 'trace-run-live',
        nodeId: 'gaojixing-doubao-batch::tool',
        eventType: 'waiting',
        createdAt: '2026-08-12T00:00:01Z',
        details: {},
      }], 1))
    }
    return Response.json(tracePayload(liveProjection('run-live', 'completed', 2), [{
      id: 'event-completed',
      sequence: 2,
      workflowId: 'workflow-live',
      workflowRunId: 'run-live',
      traceId: 'trace-run-live',
      nodeId: 'delivery',
      eventType: 'completed',
      createdAt: '2026-08-12T00:00:02Z',
      details: {},
    }], 2))
  }
  try {
    const started = await startWorkflowRun(project, {
      questionBankFile: new File(['{}'], 'questions.json', { type: 'application/json' }),
      scope,
    })
    const settled = await monitorWorkflowRun(started.runId, {
      scope,
      intervalMs: 0,
      onSnapshot: (snapshot) => snapshots.push(snapshot),
    })
    assert.equal(started.status, 'waiting')
    assert.equal(settled.projection.status, 'completed')
    assert.deepEqual(snapshots.map((snapshot) => snapshot.projection.status), ['waiting', 'completed'])
    assert.deepEqual(settled.events.map((event) => event.id), ['event-waiting', 'event-completed'])
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.deepEqual(calls.map((call) => call.method), ['POST', 'GET', 'GET'])
  assert.match(calls[0].url, /\/api\/workflow\/run\/question-bank\?workspace=workspace-live&project=project-live&workflow=workflow-live/)
  assert.match(calls[1].url, /\/api\/workflow\/runs\/run-live\/trace\?workspace=workspace-live/)
  assert.match(calls[1].url, /afterSequence=0/)
  assert.match(calls[2].url, /afterSequence=1/)
})

test('Waiting verification exposes only an opaque recovery artifact, resumes, then keeps polling', async () => {
  const [{ extractGaojixingRecoveryCase, monitorWorkflowRun }, { resumeGaojixingWorkflowRun }] = await Promise.all([
    importTypeScript('lib/workflow/live-run-monitor.ts'),
    importTypeScript('lib/workflow/backend-runs.ts'),
  ])
  const verificationEvent = {
    id: 'event-verification',
    sequence: 4,
    workflowId: 'workflow-live',
    workflowRunId: 'run-verification',
    traceId: 'trace-run-verification',
    nodeId: 'gaojixing-doubao-batch::tool',
    eventType: 'waiting',
    createdAt: '2026-08-12T00:00:04Z',
    details: {
      sampleOutputs: [{
        schema: 'gaojixing.collection-run.v1',
        status: 'waiting_verification',
        recoveryCase: {
          action: 'complete_verification',
          kind: 'human_verification_required',
          evidence: [{ artifactRef: 'run-artifact:screenshots/G0001-captcha.png' }],
        },
      }],
    },
  }
  assert.deepEqual(extractGaojixingRecoveryCase([verificationEvent]), {
    status: 'waiting_verification',
    action: 'complete_verification',
    kind: 'human_verification_required',
    artifactRef: 'run-artifact:screenshots/G0001-captcha.png',
  })
  assert.equal(extractGaojixingRecoveryCase([{
    ...verificationEvent,
    details: { sampleOutputs: [{
      schema: 'gaojixing.collection-run.v1',
      status: 'waiting_verification',
      artifactRef: 'C:\\private\\captcha.png',
    }] },
  }]), null)

  assert.deepEqual(extractGaojixingRecoveryCase([{
    ...verificationEvent,
    details: { sampleOutputs: [{
      schema: 'gaojixing.collection-run.v1',
      status: 'waiting_reconciliation',
    }] },
  }]), {status: 'waiting_reconciliation', artifactRef: null})

  const scope = { workspaceId: 'workspace-1', projectId: 'project-1', workflowId: 'workflow-1' }
  const calls = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    const call = { url: String(url), method: init?.method ?? 'GET', body: init?.body }
    calls.push(call)
    if (call.method === 'POST') {
      return Response.json({ success: true, data: liveProjection('run-verification', 'waiting', 4) }, { status: 202 })
    }
    return Response.json(tracePayload(liveProjection('run-verification', 'completed', 5), [], 5))
  }
  try {
    const resumed = await resumeGaojixingWorkflowRun('run-verification', { scope })
    assert.equal(resumed.status, 'waiting')
    const settled = await monitorWorkflowRun(resumed.runId, { scope, intervalMs: 0 })
    assert.equal(settled.projection.status, 'completed')
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.deepEqual(calls.map((call) => call.method), ['POST', 'GET'])
  assert.match(calls[0].url, /\/api\/workflow\/runs\/run-verification\/gaojixing\/resume\?/)
  assert.match(calls[0].url, /workspace=workspace-1/)
  assert.match(calls[0].url, /project=project-1/)
  assert.match(calls[0].url, /workflow=workflow-1/)
  assert.equal(calls[0].body, undefined)
})

test('Live Run polling stops after failed terminal state and after request failure', async () => {
  const { monitorWorkflowRun } = await importTypeScript('lib/workflow/live-run-monitor.ts')
  const originalFetch = globalThis.fetch
  let calls = 0
  try {
    globalThis.fetch = async () => {
      calls += 1
      return Response.json(tracePayload(liveProjection('run-failed', 'failed', 1), [], 1))
    }
    const failed = await monitorWorkflowRun('run-failed', { intervalMs: 0 })
    assert.equal(failed.projection.status, 'failed')
    assert.equal(calls, 1)

    calls = 0
    globalThis.fetch = async () => {
      calls += 1
      return Response.json({ success: false, message: 'trace unavailable' }, { status: 503 })
    }
    await assert.rejects(monitorWorkflowRun('run-network-error', { intervalMs: 0 }), /trace unavailable/)
    assert.equal(calls, 1)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Switching Runs aborts the old poll before it can publish stale state', async () => {
  const { monitorWorkflowRun } = await importTypeScript('lib/workflow/live-run-monitor.ts')
  const originalFetch = globalThis.fetch
  const published = []
  let resolveOld
  globalThis.fetch = async (url) => {
    if (String(url).includes('run-old')) {
      return await new Promise((resolve) => { resolveOld = resolve })
    }
    return Response.json(tracePayload(liveProjection('run-new', 'completed', 1), [], 1))
  }
  const oldController = new AbortController()
  try {
    const oldPoll = monitorWorkflowRun('run-old', {
      signal: oldController.signal,
      intervalMs: 0,
      onSnapshot: () => published.push('old'),
    })
    await new Promise((resolve) => setImmediate(resolve))
    oldController.abort()
    const newPoll = await monitorWorkflowRun('run-new', {
      intervalMs: 0,
      onSnapshot: () => published.push('new'),
    })
    resolveOld(Response.json(tracePayload(liveProjection('run-old', 'completed', 1), [], 1)))
    await assert.rejects(oldPoll, (error) => error?.name === 'AbortError')
    assert.equal(newPoll.projection.runId, 'run-new')
    assert.deepEqual(published, ['new'])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('Scoped live Run proxies preserve ownership for upload, trace, and resume', async () => {
  const [questionBankRoute, traceRoute, resumeRoute] = await Promise.all([
    import(new URL('../app/api/workflow/run/question-bank/route.ts', import.meta.url).href),
    import(new URL('../app/api/workflow/runs/[runId]/trace/route.ts', import.meta.url).href),
    import(new URL('../app/api/workflow/runs/[runId]/gaojixing/resume/route.ts', import.meta.url).href),
  ])
  const scopeSearch = 'workspace=workspace-owned&project=project-owned&workflow=workflow-owned'
  const calls = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init?.method ?? 'GET', headers: new Headers(init?.headers) })
    return Response.json({ success: true, data: liveProjection('run-owned', 'waiting') }, { status: 202 })
  }
  try {
    const form = new FormData()
    form.set('questionBank', new File(['{}'], 'questions.json', { type: 'application/json' }))
    form.set('request', '{}')
    await questionBankRoute.POST(new Request(`http://localhost/api/workflow/run/question-bank?${scopeSearch}`, {
      method: 'POST',
      headers: { Authorization: 'Bearer owned-token' },
      body: form,
    }))
    await traceRoute.GET(
      new Request(`http://localhost/api/workflow/runs/run-owned/trace?${scopeSearch}&afterSequence=7`),
      { params: Promise.resolve({ runId: 'run-owned' }) },
    )
    await resumeRoute.POST(
      new Request(`http://localhost/api/workflow/runs/run-owned/gaojixing/resume?${scopeSearch}`, {
        method: 'POST',
        headers: { Authorization: 'Bearer owned-token' },
      }),
      { params: Promise.resolve({ runId: 'run-owned' }) },
    )
  } finally {
    globalThis.fetch = originalFetch
  }
  assert.match(calls[0].url, /\/api\/v1\/workspaces\/workspace-owned\/projects\/project-owned\/workflows\/workflow-owned\/runs\/question-bank$/)
  assert.match(calls[1].url, /\/api\/v1\/workspaces\/workspace-owned\/projects\/project-owned\/workflows\/workflow-owned\/runs\/run-owned\/trace\?afterSequence=7$/)
  assert.match(calls[2].url, /\/api\/v1\/workspaces\/workspace-owned\/projects\/project-owned\/workflows\/workflow-owned\/runs\/run-owned\/gaojixing\/resume$/)
  assert.equal(calls[2].method, 'POST')
  assert.equal(calls[2].headers.get('authorization'), 'Bearer owned-token')
})

test('RunTracePanel monitors active Runs and resumes opaque verification recovery without sourceOutputs', () => {
  assert.match(runPanelSource, /useSearchParams\(\)/)
  assert.match(runPanelSource, /monitorWorkflowRun\(/)
  assert.match(runPanelSource, /new AbortController\(\)/)
  assert.match(runPanelSource, /runMonitorAbortRef\.current\?\.abort\(\)/)
  assert.match(runPanelSource, /extractGaojixingRecoveryCase\(runState\.events\)/)
  assert.match(runPanelSource, /resumeGaojixingWorkflowRun\(/)
  assert.match(runPanelSource, /已完成验证，继续/)
  assert.match(runPanelSource, /gaojixingRecoveryCase\.artifactRef/)
  const runFlow = runPanelSource.slice(
    runPanelSource.indexOf('const runBackendWorkflow'),
    runPanelSource.indexOf('const runImportedOutput'),
  )
  assert.doesNotMatch(runFlow, /replayWorkflowRunEventStream/)
  const resumeFlow = runPanelSource.slice(
    runPanelSource.indexOf('const resumeGaojixingRun'),
    runPanelSource.indexOf('useEffect(() => {\n    if (runRequestId > 0)'),
  )
  assert.doesNotMatch(resumeFlow, /sourceOutputs/)
})
