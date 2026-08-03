import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(frontendRoot, '..')
const windowsRepositoryPython = path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe')
const unixRepositoryPython = path.join(repositoryRoot, '.venv', 'bin', 'python')
const pythonExecutable = process.env.PYTHON
  ?? (existsSync(windowsRepositoryPython)
    ? windowsRepositoryPython
    : (existsSync(unixRepositoryPython) ? unixRepositoryPython : 'python'))

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

const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8')
const importTypeScript = (relativePath) => import(pathToFileURL(path.join(frontendRoot, relativePath)).href)

function compileWithBackend(project) {
  const result = spawnSync(pythonExecutable, ['-c', [
    'import json, sys',
    'from backend.schemas.workflow import WorkflowProject',
    'from backend.workflow.compiler import compile_workflow_project',
    'project = WorkflowProject.model_validate(json.load(sys.stdin))',
    'result = compile_workflow_project(project)',
    'print(result.model_dump_json())',
    'raise SystemExit(0 if result.valid else 1)',
  ].join('; ')], {
    cwd: repositoryRoot,
    input: Buffer.from(JSON.stringify(project), 'utf8'),
    encoding: 'utf8',
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    },
    maxBuffer: 1024 * 1024,
  })

  return { ...result, report: result.stdout ? JSON.parse(result.stdout) : null }
}

function sourceSection(source, start, end) {
  const startIndex = source.indexOf(start)
  assert.notEqual(startIndex, -1, `missing source section: ${start}`)
  const endIndex = source.indexOf(end, startIndex + start.length)
  assert.notEqual(endIndex, -1, `missing source section terminator: ${end}`)
  return source.slice(startIndex, endIndex)
}

test('generated workflow node ids avoid reserved path separators', async () => {
  const { workflowNodeId } = await importTypeScript('lib/flow/store-utils.ts')
  for (let index = 0; index < 1_000; index += 1) {
    assert.doesNotMatch(workflowNodeId(), /::|__/)
  }
})

test('right workflow dock derives its outline from graph structure and opens without a selection', async () => {
  const [{ buildWorkflowOutlineRows }, shortcuts, inspector, shell, effects] = await Promise.all([
    importTypeScript('lib/workflow/workflow-outline.ts'),
    readSource('components/flow/workflow-keyboard-shortcuts.ts'),
    readSource('components/flow/inspector.tsx'),
    readSource('components/flow/inspector-shell.tsx'),
    readSource('components/flow/workflow-editor-effects.ts'),
  ])
  const nodes = [
    { id: 'start', position: { x: 0, y: 0 }, data: {} },
    { id: 'branch', position: { x: 0, y: 100 }, data: {} },
    { id: 'leaf', position: { x: 0, y: 200 }, data: {} },
    { id: 'orphan', position: { x: 500, y: 50 }, data: {} },
  ]
  const rows = buildWorkflowOutlineRows(nodes, [
    { id: 'e1', source: 'start', target: 'branch', data: { label: 'yes' } },
    { id: 'e2', source: 'branch', target: 'leaf', sourceHandle: 'result' },
  ])

  assert.deepStrictEqual(rows, [
    { nodeId: 'start', depth: 0, branchLabel: undefined, disconnected: false },
    { nodeId: 'branch', depth: 1, branchLabel: 'yes', disconnected: false },
    { nodeId: 'leaf', depth: 2, branchLabel: 'result', disconnected: false },
    { nodeId: 'orphan', depth: 0, branchLabel: undefined, disconnected: true },
  ])
  assert.match(shortcuts, /showToast\(["']右侧工具架已显示["']\)/)
  assert.doesNotMatch(shortcuts, /请先选择一个节点或连线|selectedNodeCount|selectedEdgeCount/)
  assert.match(inspector, /WORKFLOW::OUTLINE/)
  assert.match(inspector, /buildWorkflowOutlineRows\(nodes, edges\)/)
  assert.match(shell, /aria-label=["']工作流右侧工具架["']/)
  assert.match(shell, />Workflow Dock</)
  assert.match(shell, /data-dock-mode=\{compact \? ["']overlay["'] : ["']shared["']\}/)
  assert.match(shell, /compact[\s\S]{0,120}z-50/)
  assert.match(shell, /aria-label=["']调整右侧工具架宽度["']/)
  assert.match(inspector, /pinnedNodeId/)
  assert.match(effects, /matchMedia\(["']\(max-width: 1024px\)["']\)/)
})

test('atomic source capabilities live in the workflow node catalog without a duplicate plugin source list', async () => {
  const { WORKFLOW_NODE_CATALOG } = await importTypeScript('lib/workflow/node-catalog.ts')
  const byId = new Map(WORKFLOW_NODE_CATALOG.map((item) => [item.id, item]))

  const atomicSourceIds = [
    'intelligence.source.rss',
    'intelligence.source.rsshub',
    'intelligence.source.rss-bridge',
    'intelligence.source.http',
  ]
  for (const nodeId of atomicSourceIds) {
    const node = byId.get(nodeId)
    assert.ok(node, `${nodeId} must have a workflow catalog node`)
    assert.equal(node.kind, 'source')
    assert.equal(node.capability, 'fetch')
    assert.ok(node.requiredAdapters?.length, `${nodeId} must have a runtime adapter`)
  }
  assert.equal(byId.get('intelligence.source.rsshub').params.generatorType, 'rsshub')
  assert.equal(byId.get('intelligence.source.rss-bridge').params.generatorType, 'rss_bridge')
  assert.equal(byId.get('intelligence.source.http').requiredAdapters[0].provider, 'http')
})

test('OpenCLI write commands become accepted action nodes with explicit mutation permission', async () => {
  const [{ workflowCatalogItemForOpenCLIAdapterNode }, { addCatalogNodeToWorkflowProject }, { parseWorkflowProject }] = await Promise.all([
    importTypeScript('lib/workflow/backend-opencli-adapter-nodes.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/schema.ts'),
  ])
  const catalogItem = workflowCatalogItemForOpenCLIAdapterNode({
    id: 'opencli.adapter.twitter.post',
    label: 'twitter · post',
    description: 'Publish a post',
    status: 'blocked',
    site: 'twitter',
    command: 'post',
    access: 'write',
    browser: true,
    catalogId: 'external.tool.capability',
    kind: 'action',
    capability: 'store',
    requiredArgs: ['text'],
    args: [{
      name: 'text',
      type: 'string',
      required: true,
      valueRequired: true,
      positional: true,
      choices: [],
    }],
    adapter: {
      id: 'opencli-twitter',
      type: 'utility',
      provider: 'opencli',
      mode: 'live',
      config: { channel: 'opencli' },
    },
    params: {
      site: 'twitter',
      command: 'post',
      format: 'json',
      args: {},
      positional_args: [],
    },
    manifest: {},
  }, { text: 'hello from workflow' })
  const project = parseWorkflowProject({
    id: 'opencli-write-project',
    name: 'OpenCLI write project',
    profile: 'intelligence',
    nodes: [{
      id: 'manual-trigger',
      kind: 'action',
      capability: 'trigger',
      params: {},
    }],
    edges: [],
  })
  const updated = addCatalogNodeToWorkflowProject(
    project,
    catalogItem,
    'action-opencli-twitter-post',
    { x: 320, y: 180 },
  )
  const writeNode = updated.nodes.find((node) => node.id === 'action-opencli-twitter-post')

  assert.equal(catalogItem.kind, 'action')
  assert.equal(catalogItem.capability, 'store')
  assert.equal(catalogItem.proposalState, 'accepted')
  assert.equal(updated.agentPermissions.canMutateExternalSites, true)
  assert.equal(writeNode?.proposalState, 'accepted')
  assert.equal(writeNode?.params.opencliAccess, 'write')
  assert.deepEqual(writeNode?.params.positional_args, ['hello from workflow'])
  assert.ok(updated.adapters.some((adapter) => adapter.id === 'opencli-twitter'))
})

test('runnable OpenCLI source presets retain the stable backend catalog binding', async () => {
  const [{ openCLIAdapterNodeMaterialization, workflowCatalogItemForOpenCLIAdapterNode }, { addCatalogNodeToWorkflowProject }, { parseWorkflowProject }, paletteSource, catalogHookSource] = await Promise.all([
    importTypeScript('lib/workflow/backend-opencli-adapter-nodes.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/schema.ts'),
    readSource('components/flow/command-palette.tsx'),
    readSource('lib/workflow/use-opencli-adapter-catalog.ts'),
  ])
  const catalogItem = workflowCatalogItemForOpenCLIAdapterNode({
    id: 'opencli.adapter.statsgov.nbs',
    label: 'statsgov · nbs',
    description: 'National Bureau of Statistics releases',
    status: 'runnable',
    site: 'statsgov',
    command: 'nbs',
    access: 'read',
    browser: false,
    catalogId: 'intelligence.source.opencli-slot',
    kind: 'source',
    capability: 'fetch',
    presetKind: 'source_slot',
    runtimeReadiness: 'source_slot_ready',
    requiredArgs: [],
    args: [],
    adapter: {
      id: 'opencli-statsgov-nbs',
      type: 'source',
      provider: 'opencli',
      mode: 'live',
      config: { channel: 'opencli' },
    },
    params: {
      site: 'statsgov',
      command: 'nbs',
      format: 'json',
      args: {},
      positional_args: [],
    },
    manifest: {},
  })
  const addOpenCLIAdapter = sourceSection(
    paletteSource,
    'const addOpenCLIAdapter',
    'const generate',
  )

  assert.match(
    addOpenCLIAdapter,
    /materialization === "source_slot_ready"[\s\S]*?workflowCatalogItemForOpenCLIAdapterNode\(item\)/,
  )
  assert.match(
    catalogHookSource,
    /node\.access === "read"[\s\S]*?workflowCatalogItemForOpenCLIAdapterNode\(node\)/,
  )
  assert.equal(catalogItem.id, 'intelligence.source.opencli-slot')
  assert.equal(
    openCLIAdapterNodeMaterialization({
      status: 'preview_only',
      runtimeReadiness: 'source_slot_ready',
      manifest: { canvas: { materialization: 'source_slot_ready' } },
    }),
    'unavailable',
  )

  const project = parseWorkflowProject({
    id: 'opencli-statsgov-project',
    name: 'OpenCLI statsgov project',
    profile: 'intelligence',
    nodes: [{
      id: 'manual-trigger',
      kind: 'action',
      capability: 'trigger',
      params: {},
    }],
    edges: [],
  })
  const updated = addCatalogNodeToWorkflowProject(
    project,
    catalogItem,
    'source-opencli-statsgov-nbs',
    { x: 320, y: 180 },
  )
  const sourceNode = updated.nodes.find((node) => node.id === 'source-opencli-statsgov-nbs')

  assert.equal(sourceNode?.ui?.catalogId, 'intelligence.source.opencli-slot')
  assert.equal(sourceNode?.params.opencliAdapterNodeId, 'opencli.adapter.statsgov.nbs')
  assert.ok(updated.adapters.some((adapter) => adapter.id === 'opencli-statsgov-nbs'))

  const persistedBadDraft = structuredClone(updated)
  persistedBadDraft.nodes.find((node) => node.id === sourceNode.id).ui.catalogId = 'opencli.adapter.statsgov.nbs'
  const rejected = compileWithBackend(persistedBadDraft)
  assert.notEqual(rejected.status, 0)
  assert.ok(rejected.report.errors.some((error) => error.code === 'unknown_node_library_binding'))

  const repaired = parseWorkflowProject(persistedBadDraft)
  const repairedSource = repaired.nodes.find((node) => node.id === sourceNode.id)
  assert.equal(repairedSource?.ui?.catalogId, 'intelligence.source.opencli-slot')

  const untrustedNearMiss = structuredClone(persistedBadDraft)
  untrustedNearMiss.adapters.find((adapter) => adapter.id === 'opencli-statsgov-nbs').provider = 'http'
  assert.equal(
    parseWorkflowProject(untrustedNearMiss).nodes.find((node) => node.id === sourceNode.id)?.ui?.catalogId,
    'opencli.adapter.statsgov.nbs',
  )

  const compiled = compileWithBackend(repaired)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
  assert.equal(
    compiled.report.plan.runtime.nodes.find((node) => node.id === sourceNode.id).runtime.binding.binding_id,
    'iii.collector-opencli.snapshot',
  )
})

test('OpenTabs tools become typed callable nodes and compile to the OpenTabs executor', async () => {
  const [{ workflowCatalogItemForOpenTabsToolNode }, { addCatalogNodeToWorkflowProject }, { parseWorkflowProject }] = await Promise.all([
    importTypeScript('lib/workflow/backend-opentabs-tool-nodes.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/schema.ts'),
  ])
  const catalogItem = workflowCatalogItemForOpenTabsToolNode({
    id: 'opentabs.tool.browser.browser-list-tabs',
    label: 'browser · List tabs',
    description: 'List open browser tabs',
    status: 'blocked',
    plugin: 'browser',
    tool: 'browser_list_tabs',
    access: 'read',
    requiredArgs: ['windowId'],
    args: [{
      name: 'windowId',
      type: 'integer',
      required: true,
      valueRequired: true,
      choices: [],
    }],
    inputSchema: {},
    params: {
      toolCapability: {
        id: 'tool.opentabs.call',
        executor: {
          mode: 'opentabs',
          params: {
            tool: 'browser_list_tabs',
            plugin: 'browser',
            readOnly: true,
          },
        },
      },
      toolParams: {},
      opentabsTool: {
        name: 'browser_list_tabs',
        plugin: 'browser',
        access: 'read',
      },
    },
    manifest: {},
  }, { windowId: '7' })
  const project = parseWorkflowProject({
    id: 'opentabs-browser-project',
    name: 'OpenTabs browser project',
    profile: 'intelligence',
    nodes: [{
      id: 'manual-trigger',
      kind: 'action',
      capability: 'trigger',
      params: {},
    }],
    edges: [],
  })
  const updated = addCatalogNodeToWorkflowProject(
    project,
    catalogItem,
    'action-opentabs-browser-list-tabs',
    { x: 320, y: 180 },
  )
  const toolNode = updated.nodes.find((node) => node.id === 'action-opentabs-browser-list-tabs')

  assert.equal(updated.agentPermissions.canFetchNetwork, true)
  assert.equal(toolNode?.params.toolParams.windowId, 7)
  assert.equal(toolNode?.params.toolCapability.executor.mode, 'opentabs')
  assert.equal(toolNode?.params.toolCapability.executor.params.tool, 'browser_list_tabs')

  const compiled = compileWithBackend(updated)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
  const runtimeNode = compiled.report.plan.runtime.nodes.find((node) => node.id === toolNode.id)
  assert.equal(runtimeNode.runtime.binding.binding_id, 'workflow.external-tool.capability')
  assert.equal(runtimeNode.runtime.binding.input.executorMode, 'opentabs')
  assert.equal(runtimeNode.runtime.binding.input.executorParams.tool, 'browser_list_tabs')
})

test('BBX methods become typed callable nodes and compile to the Browser Bridge executor', async () => {
  const [{ workflowCatalogItemForBbxToolNode }, { addCatalogNodeToWorkflowProject }, { parseWorkflowProject }] = await Promise.all([
    importTypeScript('lib/workflow/backend-bbx-tool-nodes.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/schema.ts'),
  ])
  const catalogItem = workflowCatalogItemForBbxToolNode({
    id: 'bbx.tool.page.page-get-text',
    label: 'BBX · page.get_text',
    description: '读取当前页面中有界的可见文本',
    status: 'blocked',
    group: 'page',
    tool: 'page.get_text',
    access: 'read',
    requiredArgs: [],
    args: [
      {
        name: 'tabId',
        type: 'integer',
        required: false,
        valueRequired: false,
        choices: [],
      },
      {
        name: 'params',
        type: 'object',
        required: false,
        valueRequired: false,
        choices: [],
      },
    ],
    params: {
      toolCapability: {
        id: 'tool.bbx.call',
        executor: {
          mode: 'bbx',
          params: {
            tool: 'page.get_text',
            group: 'page',
            readOnly: true,
          },
        },
      },
      toolParams: {},
      bbxTool: {
        name: 'page.get_text',
        group: 'page',
        access: 'read',
      },
    },
    manifest: {},
  }, {
    tabId: '27',
    params: '{"textBudget":600}',
  })
  const project = parseWorkflowProject({
    id: 'bbx-browser-project',
    name: 'BBX browser project',
    profile: 'intelligence',
    nodes: [{
      id: 'manual-trigger',
      kind: 'action',
      capability: 'trigger',
      params: {},
    }],
    edges: [],
  })
  const updated = addCatalogNodeToWorkflowProject(
    project,
    catalogItem,
    'action-bbx-page-get-text',
    { x: 320, y: 180 },
  )
  const toolNode = updated.nodes.find((node) => node.id === 'action-bbx-page-get-text')

  assert.equal(updated.agentPermissions.canFetchNetwork, true)
  assert.equal(toolNode?.params.toolParams.tabId, 27)
  assert.deepEqual(toolNode?.params.toolParams.params, { textBudget: 600 })
  assert.equal(toolNode?.params.toolCapability.executor.mode, 'bbx')
  assert.equal(toolNode?.params.toolCapability.executor.params.tool, 'page.get_text')

  const compiled = compileWithBackend(updated)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
  const runtimeNode = compiled.report.plan.runtime.nodes.find((node) => node.id === toolNode.id)
  assert.equal(runtimeNode.runtime.binding.binding_id, 'workflow.external-tool.capability')
  assert.equal(runtimeNode.runtime.binding.input.executorMode, 'bbx')
  assert.equal(runtimeNode.runtime.binding.input.executorParams.tool, 'page.get_text')
})

test('node workflow lives inside a project shell while the legacy canvas route redirects', async () => {
  const [navigation, canvasPage, workspaceWorkflowPage, projectOverviewPage, studioPage, rootPage] = await Promise.all([
    readSource('lib/navigation.ts'),
    readSource('app/(app)/canvas/page.tsx'),
    readSource('app/(app)/studio/workflow/page.tsx'),
    readSource('app/(app)/studio/projects/[projectId]/page.tsx'),
    readSource('app/(app)/studio/page.tsx'),
    readSource('app/page.tsx'),
  ])

  for (const label of ['概览', '项目', '自动化与 Agent', '任务与通知', '执行资源']) {
    assert.match(navigation, new RegExp(`label:\\s*['"]${label}['"]`))
  }
  assert.doesNotMatch(navigation, /href:\s*['"]\/canvas['"][\s\S]{0,80}label:\s*['"]节点工作流['"]/) 
  assert.match(canvasPage, /if \(workspaceId && projectId\) redirect\(`\/studio\/workflow\?/)
  assert.match(canvasPage, /redirect\(`\/studio\$\{suffix/)
  assert.match(workspaceWorkflowPage, /<WorkflowEditorSession\s*\/>/)
  assert.match(projectOverviewPage, /<ProjectNavigation/)
  assert.match(projectOverviewPage, /active="overview"/)
  assert.match(studioPage, /useMyWorkspaces[\s\S]*useWorkspaceProjects/)
  assert.match(studioPage, /router\.push\(`\/studio\/workflow\?workspace=/)
  assert.match(rootPage, /redirect\(['"]\/studio['"]\)/)
  assert.doesNotMatch(navigation, /BUILD_WORKFLOW_PATH|\/build\/workflow/)
})

test('studio creation is transactional and the editor anchors to the project primary workflow', async () => {
  const [types, endpoints, hooks, studio, templates, newProject, session, lifecycle] = await Promise.all([
    readSource('lib/api/types.ts'),
    readSource('lib/api/endpoints.ts'),
    readSource('lib/api/hooks.ts'),
    readSource('app/(app)/studio/page.tsx'),
    readSource('app/(app)/studio/templates/page.tsx'),
    readSource('app/(app)/studio/new/page.tsx'),
    readSource('components/flow/workflow-editor-session.tsx'),
    readSource('components/studio/workflow-lifecycle-strip.logic.ts'),
  ])

  assert.match(types, /primary_workflow_id: string \| null/)
  assert.match(endpoints, /projects\/bootstrap/)
  assert.doesNotMatch(endpoints, /createWorkspaceProject/)
  assert.match(hooks, /useBootstrapWorkspaceProject/)
  assert.doesNotMatch(hooks, /useCreateWorkspaceProject/)
  for (const source of [studio, templates, newProject]) {
    assert.match(source, /useBootstrapWorkspaceProject/)
    assert.doesNotMatch(source, /useCreateWorkspaceProject/)
  }
  assert.match(session, /project\?\.primary_workflow_id/)
  assert.doesNotMatch(session, /projectWorkflows\.data\?\.\[0\]\?\.id/)
  assert.match(session, /if \(!active\) return/)
  assert.match(session, /loadState === 'empty'/)
  assert.match(session, /createBlankWorkflow/)
  assert.match(session, /loadState === 'error'/)
  assert.match(session, /返回项目/)
  assert.match(templates, /CATEGORIES\.map/)
  assert.doesNotMatch(templates, /CATEGORIES\.slice\(0,\s*4\)/)
  assert.match(templates, /aria-pressed=\{active\}/)
  assert.match(newProject, /nextRequirementPrompt/)
  assert.match(newProject, /role="log"/)
  assert.match(newProject, /const studioHref = workspaceId \? `\/studio\?workspace=\$\{workspaceId\}` : '\/studio'/)
  assert.doesNotMatch(lifecycle, /activate|激活|待后端接入/)
  assert.doesNotMatch(newProject, /可激活|正式激活|检查并激活/)
})

test('Agent Builder exposes capability gaps and requires conflict confirmation before applying a conversational patch', async () => {
  const [newProject, builderCanvas] = await Promise.all([
    readSource('app/(app)/studio/new/page.tsx'),
    readSource('components/studio/agent-builder-canvas.tsx'),
  ])

  for (const contract of [
    /Capability Gap/,
    /发布与运行已阻止/,
    /Patch \/ Diff/,
    /检测到手工画布编辑冲突/,
    /确认应用 Patch/,
    /保留手工编辑/,
    /capabilityGaps/,
    /pendingPatch/,
    /manualEditVersion/,
  ]) {
    assert.match(newProject, contract)
  }
  assert.match(newProject, /workflowReadiness\?\.canRun[^\n]+<Play/)
  assert.match(newProject, /workflowReadiness\?\.canPublish[^\n]+<Rocket/)
  assert.match(newProject, /manual\?\.type === node\.type/)
  assert.match(newProject, /sameLogicalEndpoints/)
  assert.match(newProject, /mapping: manual\.mapping \?\? edge\.mapping/)
  assert.match(builderCanvas, /READY/)
  assert.match(builderCanvas, /NEEDS CONFIG/)
  assert.match(builderCanvas, /最近状态/)
  assert.match(builderCanvas, /字段映射/)
  assert.match(builderCanvas, /onManualEdit/)
})

test('P0 Builder and Canvas enforce a DAG without Loop, Retry-control, cross-Run Store, or recovery surfaces', async () => {
  const [settingsStore, interactionPanel, flowTypes, newProject] = await Promise.all([
    readSource('lib/flow/settings-store.ts'),
    readSource('components/flow/interaction-settings-panel.tsx'),
    readSource('lib/flow/types.ts'),
    readSource('app/(app)/studio/new/page.tsx'),
  ])
  const validationToggles = sourceSection(interactionPanel, 'const VALIDATION_BOOLS', 'export function InteractionSettingsPanel')
  const generatedNodeTypes = sourceSection(flowTypes, 'export type GeneratedWorkflowNodeType', 'export type GeneratedWorkflowRunStatus')

  assert.match(settingsStore, /preventCycles:\s*true/)
  assert.doesNotMatch(validationToggles, /preventCycles/)
  assert.match(interactionPanel, /DAG only/)
  assert.match(interactionPanel, /始终启用/)
  assert.doesNotMatch(generatedNodeTypes, /"loop"|"retry"|"store"|"inbox"/)
  assert.doesNotMatch(newProject, /Recovery Case|recoveryCase|Inbox/)
})

test('agent-created project specs convert into the canonical persisted workflow model', async () => {
  const [{ generateWorkflowLocally }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const spec = generateWorkflowLocally('每天抓取多个网站，摘要后发送通知')
  const project = generatedSpecToWorkflowProject(spec, 'Agent 创建的项目')

  assert.equal(project.name, 'Agent 创建的项目')
  assert.equal(project.nodes.length, spec.nodes.length)
  assert.equal(project.edges.length, spec.edges.length)
  assert.equal(project.nodes[0].capability, 'trigger')
  assert.ok(project.edges.every((edge) => project.nodes.some((node) => node.id === edge.source) && project.nodes.some((node) => node.id === edge.target)))
})

test('agent-created monitoring projects persist the P0 email delivery target', async () => {
  const [{ generateWorkflowLocally }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const spec = generateWorkflowLocally('每天抓取多个网站并生成摘要')
  const project = generatedSpecToWorkflowProject(spec, '邮件监测项目', { deliveryEmail: 'brief@example.com' })
  const emailNode = project.nodes.find((node) => node.kind === 'notify')

  assert.ok(emailNode)
  assert.equal(emailNode.params.channel, 'email')
  assert.equal(emailNode.params.to, 'brief@example.com')
  assert.ok(project.edges.some((edge) => edge.target === emailNode.id))
  assert.ok(emailNode.adapter)
  assert.ok(project.adapters.some((adapter) => adapter.id === emailNode.adapter && adapter.type === 'notification'))
})

test('agent-created monitoring projects preserve parsed source and cadence and compile in the backend', async () => {
  const [{ extractWorkflowSchedule, extractWorkflowSource, generateWorkflowLocally }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  assert.equal(extractWorkflowSource('抓取多个网站的新内容'), null)
  assert.equal(extractWorkflowSchedule('定时检查一次'), null)
  assert.equal(extractWorkflowSchedule('每天检查'), null)
  assert.equal(extractWorkflowSchedule('工作日检查'), null)
  assert.equal(extractWorkflowSchedule('每周五检查'), null)
  assert.equal(extractWorkflowSchedule('每 90 分钟检查'), null)
  assert.equal(extractWorkflowSchedule('每天 25 点检查'), null)
  assert.equal(extractWorkflowSchedule('工作日晚上 7 点检查')?.config, 'cron: 0 19 * * 1-5')
  assert.equal(extractWorkflowSchedule('每周五上午 9 点检查')?.config, 'cron: 0 9 * * 5')
  assert.equal(extractWorkflowSource('先看知乎，再改成 https://example.com/feed.xml'), 'https://example.com/feed.xml')
  assert.equal(extractWorkflowSchedule('原来每天，后来改为每小时')?.config, 'cron: 0 * * * *')
  const spec = generateWorkflowLocally('每小时抓取 https://example.com/feed.xml，摘要后发送邮件')
  const sourceSpec = spec.nodes.find((node) => node.type === 'api-agent')
  const scheduleSpec = spec.nodes.find((node) => node.type === 'schedule-trigger')
  const project = generatedSpecToWorkflowProject(spec, '每小时监测', { deliveryEmail: 'brief@example.com' })
  const sourceNode = project.nodes.find((node) => node.kind === 'source')

  assert.equal(sourceSpec?.config, 'https://example.com/feed.xml')
  assert.equal(scheduleSpec?.config, 'cron: 0 * * * *')
  assert.ok(sourceNode?.adapter)
  assert.ok(project.adapters.some((adapter) => adapter.id === sourceNode.adapter && adapter.provider === 'http'))

  const compiled = compileWithBackend(project)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
})

test('Agent Builder defaults one-time API work to Manual Trigger and Records without inventing Email', async () => {
  const [{ generateWorkflowLocally, analyzeGeneratedWorkflowReadiness }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const spec = generateWorkflowLocally('一次性查询 https://api.example.com/news，保存到 Records')
  const readiness = analyzeGeneratedWorkflowReadiness(spec)
  const project = generatedSpecToWorkflowProject(spec, '一次性 API 查询')

  assert.equal(spec.version, 1)
  assert.deepEqual(spec.intent, { mode: 'one_time', execution: 'batch', acyclic: true })
  assert.equal(spec.envelope.rawPath, 'data.raw')
  assert.deepEqual(spec.envelope.fields, ['data', 'schema', 'metadata', 'provenance', 'trace'])
  assert.equal(spec.nodes.filter((node) => node.type === 'manual-trigger').length, 1)
  assert.equal(spec.nodes.filter((node) => node.type === 'schedule-trigger').length, 0)
  assert.ok(spec.nodes.some((node) => node.type === 'api-agent'))
  assert.ok(spec.nodes.some((node) => node.type === 'records-output'))
  assert.equal(spec.nodes.some((node) => node.type === 'email-output'), false)
  assert.equal(spec.nodes.some((node) => ['loop', 'retry', 'store', 'inbox'].includes(node.type)), false)
  assert.equal(readiness.canSave, true)
  assert.equal(readiness.canPublish, true)
  assert.equal(readiness.canRun, true)
  assert.ok(spec.nodes.find((node) => node.type === 'manual-trigger')?.params?.inputSchema)
  assert.ok(Array.isArray(spec.nodes.find((node) => node.type === 'manual-trigger')?.params?.presets))
  assert.ok(spec.nodes.filter((node) => node.type.endsWith('-agent')).every((node) => node.retryPolicy && typeof node.retryPolicy.maxAttempts === 'number'))
  assert.ok(spec.nodes.filter((node) => node.type.endsWith('-agent')).every((node) => node.definitionRef?.version))

  const compiled = compileWithBackend(project)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
})

test('Agent Builder keeps scheduled Records and Email journeys distinct', async () => {
  const { generateWorkflowLocally, analyzeGeneratedWorkflowReadiness } = await importTypeScript('lib/flow/local-generate.ts')
  const recordsSpec = generateWorkflowLocally('每天 9 点查询 https://api.example.com/news，保存到 Records')
  const emailSpec = generateWorkflowLocally('每天 9 点查询 https://api.example.com/news，并发送邮件到 ops@example.com')

  assert.deepEqual(recordsSpec.intent, { mode: 'scheduled', execution: 'batch', acyclic: true })
  assert.equal(recordsSpec.nodes.filter((node) => node.type === 'schedule-trigger').length, 1)
  assert.equal(recordsSpec.nodes.find((node) => node.type === 'schedule-trigger')?.params?.overlap, 'coalesce-one-pending')
  assert.equal(recordsSpec.nodes.find((node) => node.type === 'schedule-trigger')?.params?.missedRuns, 'skip')
  assert.equal(recordsSpec.nodes.find((node) => node.type === 'schedule-trigger')?.params?.timezone, 'Asia/Shanghai')
  assert.ok(recordsSpec.nodes.some((node) => node.type === 'records-output'))
  assert.equal(recordsSpec.nodes.some((node) => node.type === 'email-output'), false)
  assert.equal(analyzeGeneratedWorkflowReadiness(recordsSpec).canRun, true)

  assert.equal(emailSpec.nodes.filter((node) => node.type === 'schedule-trigger').length, 1)
  assert.ok(emailSpec.nodes.some((node) => node.type === 'email-output'))
  assert.equal(emailSpec.nodes.find((node) => node.type === 'email-output')?.params.to, 'ops@example.com')
  assert.equal(analyzeGeneratedWorkflowReadiness(emailSpec).canPublish, true)
})

test('Agent Builder persists partial_success on an independently failed output without adding recovery work', async () => {
  const [{ generateWorkflowLocally }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const spec = generateWorkflowLocally('每天 9 点查询 https://api.example.com/news，并发送邮件到 ops@example.com')
  const emailOutput = spec.nodes.find((node) => node.type === 'email-output')
  assert.ok(emailOutput)
  emailOutput.recentStatus = 'partial_success'
  emailOutput.outputStatus = 'partial_success'

  const project = generatedSpecToWorkflowProject(spec, '部分成功状态')
  const canonicalEmail = project.nodes.find((node) => node.id === emailOutput.id)
  assert.equal(canonicalEmail?.ui?.recentStatus, 'partial_success')
  assert.equal(canonicalEmail?.ui?.outputStatus, 'partial_success')
  assert.equal(project.nodes.some((node) => node.kind === 'inbox'), false)
})

test('Agent Builder supports hybrid entrances, chained Agents, edge mapping, and parallel outputs', async () => {
  const [{ generateWorkflowLocally, analyzeGeneratedWorkflowReadiness }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const hybrid = generateWorkflowLocally('支持一次性查询和每天 9 点定时监控 https://api.example.com/news，保存到 Records')
  assert.deepEqual(hybrid.intent, { mode: 'hybrid', execution: 'batch', acyclic: true })
  assert.equal(hybrid.nodes.filter((node) => node.type === 'manual-trigger').length, 1)
  assert.equal(hybrid.nodes.filter((node) => node.type === 'schedule-trigger').length, 1)
  const hybridMerge = hybrid.nodes.find((node) => node.type === 'merge')
  assert.ok(hybridMerge)
  assert.equal(hybridMerge.params?.strategy, 'available')
  const incomingCounts = new Map()
  for (const edge of hybrid.edges) incomingCounts.set(edge.target, (incomingCounts.get(edge.target) ?? 0) + 1)
  assert.ok([...incomingCounts].filter(([, count]) => count > 1).every(([nodeId]) => hybrid.nodes.find((node) => node.id === nodeId)?.type === 'merge'))

  const spec = generateWorkflowLocally('先用 API Agent 查询 https://api.example.com/news，再用 OpenCLI Agent 搜索小红书，然后交给 LLM Transform Agent 总结，同时输出到 Records、Email ops@example.com 和 Webhook https://hooks.example.com/notify')
  const project = generatedSpecToWorkflowProject(spec, '多 Agent 多输出')
  const nodeTypes = spec.nodes.map((node) => node.type)
  const outputTypes = nodeTypes.filter((type) => type.endsWith('-output'))

  assert.ok(nodeTypes.indexOf('api-agent') < nodeTypes.indexOf('opencli-agent'))
  assert.ok(nodeTypes.indexOf('opencli-agent') < nodeTypes.indexOf('llm-transform-agent'))
  assert.deepEqual(new Set(outputTypes), new Set(['records-output', 'email-output', 'webhook-output']))
  assert.ok(spec.edges.every((edge) => edge.mapping))
  assert.ok(spec.edges.every((edge) => edge.mapping.preserveRaw === true))
  assert.ok(spec.edges.every((edge) => edge.mapping.compatible === true))
  assert.ok(spec.edges.some((edge) => edge.mapping.mode === 'auto'))
  assert.equal(analyzeGeneratedWorkflowReadiness(spec).canRun, true)
  assert.equal(project.nodes.some((node) => node.kind === 'inbox'), false)

  const terminalTargets = new Set(spec.edges.filter((edge) => outputTypes.includes(spec.nodes.find((node) => node.id === edge.target)?.type)).map((edge) => edge.target))
  assert.equal(terminalTargets.size, 3)
  const compiled = compileWithBackend(project)
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`)
  assert.equal(compiled.report.valid, true)
})

test('Agent Builder saves incomplete drafts but blocks publish and run for capability or mapping gaps', async () => {
  const { generateWorkflowLocally, analyzeGeneratedWorkflowReadiness } = await importTypeScript('lib/flow/local-generate.ts')
  const incomplete = generateWorkflowLocally('每天 9 点查询数据并发送邮件')
  const incompleteReadiness = analyzeGeneratedWorkflowReadiness(incomplete)

  assert.ok(incomplete.capabilityGaps.length >= 2)
  assert.equal(incompleteReadiness.canSave, true)
  assert.equal(incompleteReadiness.canPublish, false)
  assert.equal(incompleteReadiness.canRun, false)
  assert.ok(incompleteReadiness.blockingGapIds.length >= 2)

  const vagueMonitoring = generateWorkflowLocally('监控竞品官网和社交平台，发现重要变化后生成简报')
  assert.ok(vagueMonitoring.nodes.some((node) => node.type.endsWith('-agent')))
  assert.ok(vagueMonitoring.capabilityGaps.some((gap) => /数据来源/.test(`${gap.title} ${gap.detail}`)))
  assert.equal(analyzeGeneratedWorkflowReadiness(vagueMonitoring).canRun, false)

  const incompatible = generateWorkflowLocally('一次性查询 https://api.example.com/news，保存到 Records，字段类型冲突')
  const incompatibleEdge = incompatible.edges.find((edge) => edge.mapping.compatible === false)
  const incompatibleReadiness = analyzeGeneratedWorkflowReadiness(incompatible)
  assert.ok(incompatibleEdge)
  assert.ok(incompatibleEdge.mapping.conflicts.length > 0)
  assert.equal(incompatibleReadiness.canSave, true)
  assert.equal(incompatibleReadiness.canPublish, false)
  assert.equal(incompatibleReadiness.canRun, false)
})

test('Capability Gap survives draft persistence, blocks backend compile, and clears when Email is configured', async () => {
  const [{ generateWorkflowLocally, analyzeGeneratedWorkflowReadiness }, { generatedSpecToWorkflowProject }] = await Promise.all([
    importTypeScript('lib/flow/local-generate.ts'),
    importTypeScript('lib/workflow/generated-project.ts'),
  ])
  const spec = generateWorkflowLocally('每天 9 点查询 https://api.example.com/news 并发送邮件')
  const readiness = analyzeGeneratedWorkflowReadiness(spec)
  assert.equal(readiness.canSave, true)
  assert.equal(readiness.canPublish, false)
  assert.equal(readiness.canRun, false)
  assert.ok(spec.capabilityGaps.some((gap) => gap.capability === 'configuration'))

  const incompleteProject = generatedSpecToWorkflowProject(spec, '待配置邮件 Draft')
  const incompleteCompile = compileWithBackend(incompleteProject)
  assert.equal(incompleteCompile.status, 1)
  assert.ok(incompleteCompile.report.errors.some((error) => error.code === 'capability_gap'))

  const resolvedProject = generatedSpecToWorkflowProject(spec, '已配置邮件 Draft', { deliveryEmail: 'ops@example.com' })
  assert.ok(resolvedProject.nodes.every((node) => (node.ui?.builder?.capabilityGaps?.length ?? 0) === 0))
  const resolvedCompile = compileWithBackend(resolvedProject)
  assert.equal(resolvedCompile.status, 0, `${resolvedCompile.stdout}\n${resolvedCompile.stderr}`)
  assert.equal(resolvedCompile.report.valid, true)
})

test('studio blank and packaged template drafts retain canonical adapters and compile', async () => {
  const { STUDIO_TEMPLATES, studioGraphForTemplate } = await importTypeScript('lib/workflow/studio-templates.ts')
  const blank = studioGraphForTemplate('blank', '空白项目')
  const packaged = STUDIO_TEMPLATES.map((template) => studioGraphForTemplate(template.id, template.title))
  const collectionResearchTemplate = STUDIO_TEMPLATES.find((template) => template.id === 'native-intelligence-lifecycle')

  assert.equal(blank.nodes.length, 1)
  assert.equal(blank.nodes[0].ui?.catalogId, 'intelligence.input.collection-need')
  assert.equal(blank.nodes[0].internals?.nodes?.length ?? 0, 0)
  assert.equal(collectionResearchTemplate?.title, '多平台采集研究项目')
  assert.doesNotMatch(STUDIO_TEMPLATES.map((template) => `${template.title} ${template.description}`).join(' '), /原生智能|Native Intelligence Lifecycle/i)
  assert.equal(studioGraphForTemplate('native-intelligence-lifecycle', collectionResearchTemplate.title).nodes[0].ui?.label, '采集研究与报告')

  for (const project of [blank, ...packaged]) {
    const visit = (node) => {
      if (node.adapter) assert.ok(project.adapters.some((adapter) => adapter.id === node.adapter), `${project.name}: ${node.adapter}`)
      for (const child of node.internals?.nodes ?? []) visit(child)
    }
    project.nodes.forEach(visit)
    const compiled = compileWithBackend(project)
    assert.equal(compiled.status, 0, `${project.name}\n${compiled.stdout}\n${compiled.stderr}`)
    assert.equal(compiled.report.valid, true)
  }
})

test('legacy intelligence project names are translated only for display', async () => {
  const { businessProjectName } = await importTypeScript('lib/workflow/business-node-experience.ts')
  assert.equal(businessProjectName('原生智能完整生命周期'), '多平台采集研究项目')
  assert.equal(businessProjectName('原生智能完整生命周期 主工作流'), '多平台采集研究项目 主工作流')
  assert.equal(businessProjectName('网站变化监控'), '网站变化监控')
})

test('studio templates persist template-specific source, cadence, and delivery intent', async () => {
  const [{ studioGraphForTemplate }, { inferWorkflowRunTrigger }] = await Promise.all([
    importTypeScript('lib/workflow/studio-templates.ts'),
    importTypeScript('lib/workflow/backend-runs.ts'),
  ])
  const websiteWatch = studioGraphForTemplate('website-watch', '网站变化监控')
  const newsBrief = studioGraphForTemplate('news-brief', '每日资讯简报')
  const opencliLive = studioGraphForTemplate('opencli-live-pipeline', 'OpenCLI 实时管线')
  const financialRss = studioGraphForTemplate('financial-rss-intelligence', '财经多源 RSS 情报')

  assert.notDeepEqual(websiteWatch.nodes, newsBrief.nodes)
  assert.equal(websiteWatch.nodes[0].params.templateId, 'website-watch')
  assert.equal(newsBrief.nodes[0].params.templateId, 'news-brief')
  assert.equal(websiteWatch.nodes[0].params.cadence, 'hourly')
  assert.equal(newsBrief.nodes[0].params.cadence, 'daily')
  assert.equal(opencliLive.nodes.find((node) => node.id === 'source-opencli-bbc-news')?.params.opencliAdapterNodeId, 'opencli.adapter.bbc.news')
  assert.deepEqual(
    opencliLive.nodes.map((node) => node.id),
    ['schedule', 'source-opencli-bbc-news', 'record-hygiene', 'records', 'notify-webhook'],
  )
  const hygiene = opencliLive.nodes.find((node) => node.id === 'record-hygiene')
  assert.equal(hygiene?.ui?.catalogId, 'package.processing.record-hygiene')
  assert.equal(hygiene?.internals?.locked, true)
  assert.deepEqual(hygiene?.internals?.nodes.map((node) => node.id), ['normalize', 'dedupe', 'record-acceptance'])
  assert.deepEqual(
    opencliLive.edges.map((edge) => [edge.id, edge.source, edge.sourcePort, edge.target, edge.targetPort]),
    [
      ['schedule-source', 'schedule', undefined, 'source-opencli-bbc-news', undefined],
      ['source-record-hygiene', 'source-opencli-bbc-news', 'out', 'record-hygiene', 'in'],
      ['record-hygiene-records', 'record-hygiene', 'out', 'records', 'records'],
      ['record-hygiene-notify', 'record-hygiene', 'out', 'notify-webhook', 'in'],
    ],
  )
  assert.equal(opencliLive.agentPermissions.canSendNotifications, true)
  const financialSourcePool = financialRss.nodes.find((node) => node.ui?.catalogId === 'intelligence.source.pool')
  assert.ok(financialSourcePool)
  assert.equal(financialRss.nodes.filter((node) => node.kind === 'source').length, 0)
  assert.deepEqual(financialRss.nodes.map((node) => node.id), [
    'schedule-finance-rss',
    'source-pool-finance-rss',
    'normalize-finance-rss',
    'accept-finance-rss',
    'records-finance-rss',
  ])
  assert.deepEqual(
    financialSourcePool.internals?.nodes.map((node) => node.params.sourceGroup),
    ['macro-policy', 'market-regulation', 'central-bank-research'],
  )
  assert.ok(financialSourcePool.internals?.nodes.every((node) => node.ui?.catalogId === 'intelligence.source.rss'))
  assert.deepEqual(
    financialSourcePool.internals?.nodes.map((node) => node.params.sourceKey),
    ['rss-federal-reserve', 'rss-sec-regulation', 'rss-ecb-research'],
  )
  assert.deepEqual(financialSourcePool.ui?.runtimeContract?.outputShape?.ports, [{ name: 'items', type: 'items[]' }])
  assert.deepEqual(
    financialSourcePool.internals?.nodes.map((node) =>
      Object.fromEntries(
        node.parameterInterface?.fields
          .filter((field) => ['feedUrl', 'sourceGroup'].includes(field.binding.fieldId))
          .map((field) => [field.binding.fieldId, field.value]) ?? [],
      ),
    ),
    [
      { feedUrl: 'https://www.federalreserve.gov/feeds/press_all.xml', sourceGroup: 'macro-policy' },
      { feedUrl: 'https://www.sec.gov/news/pressreleases.rss', sourceGroup: 'market-regulation' },
      { feedUrl: 'https://www.ecb.europa.eu/rss/press.html', sourceGroup: 'central-bank-research' },
    ],
  )
  assert.deepEqual(
    financialRss.edges.map((edge) => [edge.source, edge.target]),
    [
      ['schedule-finance-rss', 'source-pool-finance-rss'],
      ['source-pool-finance-rss', 'normalize-finance-rss'],
      ['normalize-finance-rss', 'accept-finance-rss'],
      ['accept-finance-rss', 'records-finance-rss'],
    ],
  )
  assert.deepEqual(financialRss.agentPermissions.allowedDomains, ['federalreserve.gov', 'sec.gov', 'ecb.europa.eu'])
  assert.equal(financialRss.nodes.at(-1)?.ui?.catalogId, 'intelligence.sink.records')
  assert.deepEqual(inferWorkflowRunTrigger(financialRss), {
    kind: 'schedule',
    triggerNodeId: 'schedule-finance-rss',
  })
  assert.deepEqual(inferWorkflowRunTrigger({
    ...financialRss,
    nodes: financialRss.nodes.filter((node) => node.kind !== 'schedule'),
  }), { kind: 'manual' })
})

test('project application types use persisted values and preserve Dify modes', async () => {
  const [{ PROJECT_APP_TYPE_LABELS, projectAppCategoryLabel, projectAppTypeForDifyMode, projectAppTypeLabel, projectMatchesAppType }, { translateWorkflowDsl }, { studioAppTypeForTemplate }] = await Promise.all([
    importTypeScript('lib/studio/app-types.ts'),
    importTypeScript('lib/workflow/codec.ts'),
    importTypeScript('lib/workflow/studio-templates.ts'),
  ])

  assert.deepEqual(
    ['chat', 'agent-chat', 'advanced-chat', 'workflow', 'completion'].map(projectAppTypeForDifyMode),
    ['chatbot', 'agent', 'chatflow', 'workflow', 'text-generator'],
  )
  assert.equal(projectAppTypeForDifyMode('unknown'), 'workflow')
  assert.equal(projectMatchesAppType({ app_type: 'chatbot' }, 'conversation'), true)
  assert.equal(projectMatchesAppType({ app_type: 'agent' }, 'conversation'), true)
  assert.equal(projectMatchesAppType({ app_type: 'workflow' }, 'conversation'), false)
  assert.equal(projectMatchesAppType({ app_type: 'workflow' }, 'orchestration'), true)
  assert.equal(projectAppCategoryLabel('chatflow'), '对话应用')
  assert.equal(PROJECT_APP_TYPE_LABELS['text-generator'], '文本生成')
  assert.equal(projectAppTypeLabel(undefined), '未分类')
  assert.equal(studioAppTypeForTemplate('research-agent'), 'agent')
  assert.equal(studioAppTypeForTemplate('content-summary'), 'text-generator')
  assert.equal(studioAppTypeForTemplate('blank'), 'workflow')

  const imported = translateWorkflowDsl(JSON.stringify({
    kind: 'app',
    app: { name: 'Support flow', mode: 'advanced-chat' },
    version: '0.3.0',
    workflow: { graph: { nodes: [{ id: 'start', data: { type: 'start', title: 'Start' } }], edges: [] } },
  }))
  assert.equal(imported.ok, true)
  assert.equal(imported.report?.source, 'dify')
  assert.equal(imported.report?.appMode, 'advanced-chat')
  assert.equal(projectAppTypeForDifyMode(imported.report?.appMode), 'chatflow')
})

test('the production studio adopts the selected project-workspace concept with a real project overview', async () => {
  const [studio, projectOverview, projectNavigation, workflowPage, projectHeader] = await Promise.all([
    readSource('app/(app)/studio/page.tsx'),
    readSource('app/(app)/studio/projects/[projectId]/page.tsx'),
    readSource('components/studio/project-navigation.tsx'),
    readSource('app/(app)/studio/workflow/page.tsx'),
    readSource('components/studio/workflow-project-header.tsx'),
  ])

  assert.match(studio, /useMyWorkspaces\(\)/)
  assert.match(studio, /useWorkspaceProjects\(workspaceId\)/)
  assert.match(studio, /title="项目"/)
  assert.match(studio, /get\('create'\) === 'workflow'/)
  assert.match(studio, /setCreateTemplate\('opencli-live-pipeline'\)/)
  assert.match(studio, /aria-label="项目浏览工具栏"/)
  assert.match(studio, /aria-label="项目分类筛选"/)
  assert.match(studio, /PROJECT_APP_CATEGORY_LABELS\.conversation/)
  assert.match(studio, /PROJECT_APP_CATEGORY_LABELS\.generation/)
  assert.match(studio, /onContextMenu=/)
  assert.match(studio, /useDeleteWorkspaceProject\(\)/)
  assert.doesNotMatch(studio, /全部创建者/)
  assert.match(studio, /const selectedWorkspace = workspaces\.data\?\.find/)
  assert.match(studio, /workspaces\.data\?\.length[\s\S]*> 1/)
  assert.match(studio, /<SelectValue>\{selectedWorkspace\?\.name \?\? '选择工作区'\}<\/SelectValue>/)
  assert.match(studio, /aria-label="当前工作区"/)
  assert.doesNotMatch(studio, /<SelectValue placeholder="选择工作区"\s*\/>/)
  assert.match(studio, /projectMatchesAppType\(project, type\)/)
  assert.match(studio, /projectAppTypeLabel\(project\.app_type\)/)
  assert.doesNotMatch(studio, /inferProjectType/)
  assert.match(studio, /\{visibleProjects\.length\} 个项目/)
  assert.match(studio, /project\.updated_at/)
  assert.match(studio, /\/studio\/projects\/\$\{project\.id\}\?workspace=\$\{workspaceId\}/)
  assert.doesNotMatch(studio, /ProductShellPrototype|PrototypeNotice|workspaceProjects|forceStandalone/)
  assert.match(projectOverview, /useWorkspaceProjects\(workspaceId\)/)
  assert.match(projectOverview, /useProjectWorkflows\(workspaceId, projectId\)/)
  assert.match(projectOverview, /project\?\.primary_workflow_id/)
  assert.match(projectOverview, /aria-label="项目下一步"/)
  assert.ok(projectOverview.indexOf('if (projectWorkflows.isError)') < projectOverview.indexOf('const needsWorkflow'))
  assert.match(projectOverview, /projectWorkflows\.refetch\(\)/)
  assert.match(projectOverview, /title="创建草稿"[\s\S]*done=\{Boolean\(primaryWorkflow\)\}/)
  assert.doesNotMatch(projectOverview, /title="检查草稿"[\s\S]*done=\{Boolean\(primaryWorkflow\)\}/)
  assert.match(projectNavigation, /概览/)
  assert.match(projectNavigation, /编排/)
  assert.match(projectNavigation, /运行记录/)
  assert.match(projectNavigation, /数据/)
  assert.match(projectNavigation, /逻辑与证据/)
  assert.match(projectNavigation, /设置/)
  assert.match(projectNavigation, /\{ id: 'operations', label: '运行记录'/)
  assert.match(projectNavigation, /: null/)
  assert.match(projectNavigation, /aria-disabled="true"/)
  assert.match(workflowPage, /<WorkflowProjectHeader\s*\/>/)
  assert.match(workflowPage, /<WorkflowEditorSession\s*\/>/)
  assert.match(projectHeader, /useWorkspaceProjects\(workspaceId\)/)
  assert.match(projectHeader, /useProjectWorkflows\(workspaceId, projectId\)/)
  assert.match(projectHeader, /`\/studio\/projects\/\$\{projectId\}\?workspace=\$\{workspaceId\}`/)
  assert.match(projectHeader, /<ProjectNavigation/)
  assert.match(projectHeader, /aria-label="选择工作流"/)
  assert.match(projectHeader, /selectedWorkflow\?\.name/)
  assert.match(projectHeader, /正式节点系统/)
  assert.doesNotMatch(projectHeader, /PrototypeNotice|forceStandalone|comparisonProfiles/)
})

test('studio and workflow primary controls keep touch targets and explicit selected state', async () => {
  const [studio, templates, newProject, projectNavigation, projectHeader, commandStrip, workflowEditor, runTracePanel] = await Promise.all([
    readSource('app/(app)/studio/page.tsx'),
    readSource('app/(app)/studio/templates/page.tsx'),
    readSource('app/(app)/studio/new/page.tsx'),
    readSource('components/studio/project-navigation.tsx'),
    readSource('components/studio/workflow-project-header.tsx'),
    readSource('components/flow/command-strip.tsx'),
    readSource('components/flow/workflow-editor.tsx'),
    readSource('components/flow/run-trace-panel.tsx'),
  ])

  assert.match(studio, /aria-pressed=\{type === value\}/)
  assert.doesNotMatch(studio, /\b(?:h-8|sm:h-8|sm:h-9)\b/)
  for (const source of [templates, newProject, projectNavigation, projectHeader]) {
    assert.doesNotMatch(source, /(?:sm|md):min-h-[89]\b/)
  }
  assert.match(commandStrip, /"size-11 text-muted-foreground hover:text-foreground"/)
  assert.match(commandStrip, /data-testid="workflow-run"[\s\S]*className="min-h-11 gap-1\.5 rounded-lg"[\s\S]*onClick=\{onRunWorkflow\}[\s\S]*<Play[\s\S]*运行/)
  assert.match(commandStrip, /<DropdownMenuItem onClick=\{onToggleRunTrace\}>[\s\S]*运行面板/)
  assert.match(workflowEditor, /const runWorkflow = useCallback\(\(\) => \{[\s\S]*setRunTraceOpen\(true\)[\s\S]*setRunRequestId/)
  assert.match(workflowEditor, /onRunWorkflow=\{runWorkflow\}/)
  assert.match(runTracePanel, /if \(runRequestId > 0\) runButtonRef\.current\?\.click\(\)/)
  assert.match(runTracePanel, /startWorkflowRun\(workflowProject/)
  assert.match(commandStrip, /persistenceNeedsAttention && "text-\[#ff7a17\]"/)
  assert.doesNotMatch(commandStrip, /className="size-[78] rounded-lg"/)
})

test('the product-shell prototype reuses the canonical editor without project draft mutations', async () => {
  const [prototype, session] = await Promise.all([
    readSource('components/prototype/product-shell-prototype.tsx'),
    readSource('components/flow/workflow-editor-session.tsx'),
  ])

  assert.ok(prototype.includes('<WorkflowEditorSession forceStandalone />'))
  assert.ok(session.includes('forceStandalone?: boolean'))
  assert.ok(session.includes("const workspaceId = forceStandalone ? null : params.get('workspace')"))
  assert.ok(session.includes("const projectId = forceStandalone ? null : params.get('project')"))
  for (const duplicatePrototypeModel of ['bindingOptions', 'bindingSelections', 'DataDebugDock', 'NodeInspector']) {
    assert.ok(!prototype.includes(duplicatePrototypeModel), `${duplicatePrototypeModel} must not return to Direction C`)
  }
})

test('workflow validation waits for the runtime capability catalog', async () => {
  const [capabilitiesHook, session] = await Promise.all([
    readSource('lib/workflow/use-workflow-capabilities.ts'),
    readSource('components/flow/workflow-editor-session.tsx'),
  ])

  assert.match(capabilitiesHook, /capabilities: projectedCapabilities/)
  assert.match(capabilitiesHook, /loading: loading \|\| nodeCatalog\.loading/)
  assert.match(session, /const \{ error: capabilityError, loading: capabilityLoading \} = useWorkflowCapabilities\(true\)/)
  assert.match(session, /if \(capabilityLoading\) \{[\s\S]*运行能力目录仍在加载/)
  assert.match(session, /disabled=\{capabilityLoading \|\| Boolean\(capabilityError\)/)
  assert.match(session, /capabilityLoading \? '正在加载运行能力目录'/)
})

test('workflow separates lightweight canvas actions from the guided node picker', async () => {
  const [editor, surface, palette, contextMenu, commandStrip, effects, runTrace] = await Promise.all([
    readSource('components/flow/workflow-editor.tsx'),
    readSource('components/flow/workflow-canvas-surface.tsx'),
    readSource('components/flow/command-palette.tsx'),
    readSource('components/flow/node-context-menu.tsx'),
    readSource('components/flow/command-strip.tsx'),
    readSource('components/flow/workflow-editor-effects.ts'),
    readSource('components/flow/run-trace-panel.tsx'),
  ])

  const paneContextHandler = sourceSection(editor, 'const onPaneContextMenu', 'const openNodePicker')
  assert.match(paneContextHandler, /setNodeMenu\(\{ x: event\.clientX, y: event\.clientY \}\)/)
  assert.doesNotMatch(paneContextHandler, /setPaletteOpen\(true\)/)
  for (const action of ['添加节点', '添加注释', '测试运行', '导入应用']) {
    assert.match(contextMenu, new RegExp(action))
  }
  assert.match(contextMenu, /w-56/)
  assert.match(contextMenu, /className="size-3\.5/)
  assert.doesNotMatch(contextMenu, /当前节点|进入内部网络|选择当前流程分支|参数与通道|节点信息/)
  assert.doesNotMatch(contextMenu, /DOP Operators|Add Internal Primitive/)
  assert.match(contextMenu, /querySelector<HTMLButtonElement>\('\[role="menuitem"\]'\)\?\.focus\(\)/)
  assert.match(contextMenu, /event\.key === ["']ArrowDown["']/)
  assert.match(contextMenu, /event\.key === ["']ArrowUp["']/)
  assert.match(editor, /NODE_PALETTE\.find\(\(item\) => item\.nodeType === ["']note["']\)/)
  assert.match(editor, /addNodeFromPalette\(note, screenToFlowPosition/)
  assert.match(editor, /importInputRef\.current\?\.click\(\)/)
  assert.match(editor, /setRunRequestId\(\(current\) => current \+ 1\)/)
  assert.match(runTrace, /runButtonRef\.current\?\.click\(\)/)
  assert.match(runTrace, /startWorkflowRun\(workflowProject/)
  assert.match(commandStrip, /importInputRef\?: RefObject<HTMLInputElement \| null>/)
  assert.match(surface, /onPaneContextMenu=\{props\.onPaneContextMenu\}/)
  assert.match(surface, /onAddNode=\{props\.onAddNodeFromMenu\}/)
  assert.match(surface, /onAddNote=\{props\.onAddNoteFromMenu\}/)
  assert.match(surface, /onImportApp=\{props\.onImportApp\}/)
  assert.match(surface, /onTestRun=\{props\.onTestRun\}/)
  for (const tab of ['节点', '工具', '开始']) {
    assert.match(palette, new RegExp(`label: ["']${tab}["']`))
  }
  assert.match(palette, /role="tablist"/)
  assert.match(palette, /createPortal/)
  assert.match(palette, /document\.body/)
  assert.match(palette, /OpenCLI 能力预设/)
  assert.match(palette, /插件与后端工具/)
  assert.match(palette, /href="\/plugins"/)
  assert.match(palette, /workflowCatalogItemIsOpenCLIAdapterPreset/)
  assert.match(palette, /catalogItemUnavailable/)
  assert.match(palette, /inNodeNetwork \? getWorkflowPrimitives\(\) : getDifyCommonWorkflowPrimitives\(\)/)
  assert.match(palette, /item\.category === ["']annotation["'] \|\| item\.category === ["']shape["']/)
  assert.match(palette, /groupPrimitivesForNodeMenu/)
  assert.match(effects, /event\.key === ["']Escape["']/)
  assert.doesNotMatch(effects, /addEventListener\(["']keydown["'], close\)/)
})

test('workflow node ports show contract names without anonymous duplicates', async () => {
  const [node, capabilities, canvasCss] = await Promise.all([
    readSource('components/flow/nodes/workflow-node.tsx'),
    readSource('lib/workflow/capabilities.ts'),
    readSource('app/flow-canvas.css'),
  ])

  for (const label of ['触发信号', '条目', '候选记录', '记录', '投递结果', '已存储条目']) {
    assert.match(node, new RegExp(label))
  }
  assert.match(node, /primitiveOutputs\.length > 0 \? primitiveOutputs : semanticPorts\.outputs/)
  assert.match(node, /primitiveInputs\.length > 0 \? primitiveInputs : semanticPorts\.inputs/)
  assert.match(node, /if \(id === undefined && declared\.length > 0\) continue/)
  assert.match(node, /direction: ["']IN["'] as const/)
  assert.match(node, /direction: ["']OUT["'] as const/)
  assert.match(node, /\{direction\} · \{port\.id \?\? ["']default["']\}/)
  assert.match(node, /\[\{port\.type\}\]/)
  assert.match(node, /"data-port-direction": handleType === ["']source["'] \? ["']output["'] : ["']input["']/)
  assert.match(node, /"data-port-id": port\.id \?\? ["']default["']/)
  assert.match(node, /"data-port-type": port\.type \?\? ["']unknown["']/)
  assert.match(node, /position=\{Position\.Top\}/)
  assert.match(node, /position=\{Position\.Bottom\}/)
  assert.match(node, /aria-label.*\$\{port\.id \?\? ["']default["']\}.*\$\{port\.type \?\? ["']unknown["']\}/)
  assert.doesNotMatch(node, /outputs: \[\{ id: undefined, label: ["']out["'] \}, \{ id: ["']out["'], label: ["']out["'] \}\]/)
  assert.match(canvasCss, /\.workflow-port-name \{[\s\S]*opacity: 0/)
  assert.match(canvasCss, /\.workflow-port-anchor:hover \.workflow-port-name,[\s\S]*opacity: 1/)
  assert.match(capabilities, /missingLabels: Array\.from\(new Set\(missing\.map\(displayMissingLabel\)\)\)/)
})

test('the default canvas is an operator network with recursive four-layer lookup', async () => {
  const [pipeline, store, nodePath, commandStrip, editor, settings, hierarchy] = await Promise.all([
    readSource('lib/workflow/collection-pipeline.ts'),
    readSource('lib/flow/store.ts'),
    readSource('lib/workflow/node-path.ts'),
    readSource('components/flow/command-strip.tsx'),
    readSource('components/flow/workflow-editor.tsx'),
    readSource('lib/flow/settings-store.ts'),
    readSource('lib/workflow/node-hierarchy.ts'),
  ])
  const packaged = sourceSection(pipeline, 'export function buildPackagedWorkflowProject(', 'export const PACKAGED_WORKFLOW_PROJECT')

  for (const packageId of ['package.opencli.multi-source-hda', 'package.intelligence.pipeline', 'package.review.human-review', 'package.dispatch.fanout']) {
    assert.match(packaged, new RegExp(packageId.replaceAll('.', '\\.')))
  }
  assert.match(packaged, /createOperatorNodeFromCatalog/)
  for (const operatorId of ['source-operator', 'intelligence-operator', 'review-operator', 'dispatch-operator']) {
    assert.match(packaged, new RegExp(operatorId))
  }
  assert.match(store, /const initialWorkflowProject = PACKAGED_WORKFLOW_PROJECT/)
  assert.match(store, /findWorkflowProjectNodeByCanvasId as findProjectNodeByCanvasId/)
  assert.match(nodePath, /function findWorkflowProjectNodeByCanvasId[\s\S]*CANVAS_NODE_PATH_SEPARATOR[\s\S]*child\.id/)
  assert.match(store, /materializeProjectInternals\(projectNode, node, nodeId, ["']network["']\)/)
  assert.match(store, /networkStack\.length >= MAX_WORKFLOW_NODE_DEPTH - 1/)
  assert.match(commandStrip, /载入完整采集示例/)
  for (const hierarchyLabel of ['业务节点', '实现节点', '组件节点', '原子节点']) {
    assert.match(hierarchy, new RegExp(hierarchyLabel))
  }
  for (const hierarchyLabel of ['workflowNodeDepthFromNetworkStack', 'workflowNodeLayerAtDepth']) {
    assert.match(commandStrip, new RegExp(hierarchyLabel))
  }
  assert.match(editor, /useState\(false\)[\s\S]*setInspectorOpen\(true\)/)
  assert.match(settings, /showMiniMap:\s*false/)
})

test('the actual packaged default project satisfies backend node and typed-port contracts', async () => {
  const { PACKAGED_WORKFLOW_PROJECT } = await importTypeScript('lib/workflow/collection-pipeline.ts')
  const sourceOperator = PACKAGED_WORKFLOW_PROJECT.nodes.find((node) => node.id === 'source-operator')
  const sourcePackage = sourceOperator?.internals?.nodes.find((node) => node.id === 'source-package')

  assert.ok(sourcePackage, 'the OpenCLI implementation node must remain under the source operator')
  assert.doesNotMatch(JSON.stringify(sourcePackage.params), /maxConcurrency/)
  assert.deepStrictEqual(
    sourcePackage.internals.edges.map(({ source, target, sourcePort, targetPort }) => ({
      source,
      target,
      sourcePort,
      targetPort,
    })),
    [
      { source: 'source-pool', target: 'source-douyin', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-pool', target: 'source-bilibili', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-pool', target: 'source-xiaohongshu', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-pool', target: 'source-twitter', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-douyin', target: 'internal-normalize', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-bilibili', target: 'internal-normalize', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-xiaohongshu', target: 'internal-normalize', sourcePort: 'out', targetPort: 'in' },
      { source: 'source-twitter', target: 'internal-normalize', sourcePort: 'out', targetPort: 'in' },
      { source: 'internal-normalize', target: 'collection-output', sourcePort: 'out', targetPort: 'in' },
    ],
  )

  const backendCheck = compileWithBackend(PACKAGED_WORKFLOW_PROJECT)

  assert.equal(backendCheck.status, 0, `${backendCheck.stdout}\n${backendCheck.stderr}`)
  assert.equal(JSON.parse(backendCheck.stdout).valid, true)
})

test('editor selector remains shallow-stable for an unchanged store snapshot', async () => {
  const [{ selectEditorCanvasState }, editorSource] = await Promise.all([
    importTypeScript('components/flow/workflow-editor-selectors.ts'),
    readSource('components/flow/workflow-editor.tsx'),
  ])
  const values = new Map()
  const state = new Proxy({}, {
    get(_target, property) {
      if (!values.has(property)) values.set(property, typeof property === 'string' ? Symbol(property) : property)
      return values.get(property)
    },
  })

  const first = selectEditorCanvasState(state)
  const second = selectEditorCanvasState(state)
  assert.notStrictEqual(first, second, 'selector intentionally returns a projection object')
  assert.deepStrictEqual(first, second, 'projection values must retain stable identities for shallow comparison')
  assert.match(editorSource, /useFlowStore\(useShallow\(selectEditorCanvasState\)\)/)
})

test('inspector uses the selected node for configuration and the graph for its outline', async () => {
  const inspector = await readSource('components/flow/inspector.tsx')
  const selectOutlineNode = sourceSection(inspector, 'const selectOutlineNode =', '/* ---- edge parameter interface ---- */')

  assert.match(inspector, /const canvasSelected = nodes\.filter\(\(n\) => n\.selected\)/)
  assert.match(inspector, /const selected = pinnedNode \? \[pinnedNode\] : canvasSelected/)
  assert.match(inspector, /selected\.length !== 1 \|\| nodeTab === ["']outline["']/)
  assert.doesNotMatch(inspector, /setNodeTab\(selectedNodeId \? ["']config["'] : ["']outline["']\)/)
  assert.doesNotMatch(selectOutlineNode, /setNodeTab/)
  assert.match(inspector, /onDoubleClick=\{\(\) => onOpenNode\(node\.id\)\}/)
  assert.match(inspector, /event\.key !== ["']Enter["']/)
  assert.match(inspector, /<WorkflowOutlinePanel/)
  assert.match(inspector, /const node = selected\[0\]/)
  assert.match(inspector, /findWorkflowProjectNodeByCanvasId\(workflowProject, node\.id\)/)
  assert.doesNotMatch(inspector, /fallback[\s\S]{0,80}schedule|schedule[\s\S]{0,80}fallback/i)
})

test('the right workflow dock keeps selection, locates nodes, and allows empty P toggles', async () => {
  const [shortcuts, inspector, shell, surface] = await Promise.all([
    readSource('components/flow/workflow-keyboard-shortcuts.ts'),
    readSource('components/flow/inspector.tsx'),
    readSource('components/flow/inspector-shell.tsx'),
    readSource('components/flow/workflow-canvas-surface.tsx'),
  ])

  assert.match(shortcuts, /setInspectorOpen\(true\)[\s\S]*右侧工具架已显示/)
  assert.doesNotMatch(shortcuts, /selectedNodeCount|selectedEdgeCount|请先选择一个节点或连线/)
  assert.doesNotMatch(shortcuts, /setInspectorOpen\(\(open\) =>/)
  assert.match(inspector, /getInternalNode\(node\.id\)[\s\S]*setCenter\(/)
  assert.match(inspector, /getInternalNode\(nodeId\)[\s\S]*setCenter\(/)
  assert.match(inspector, /onClose=\{onClose\}/)
  assert.doesNotMatch(inspector, /onClose=\{deselectAll\}/)
  assert.match(shell, /aria-label="定位到当前节点"/)
  assert.match(shell, /onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/)
  assert.match(surface, /<Inspector compact=\{props\.compactViewport\} onClose=\{props\.onCloseInspector\} \/>/)
})

test('the inspector host constrains long node configuration so the dock owns vertical scrolling', async () => {
  const [page, surface] = await Promise.all([
    readSource('app/(app)/studio/workflow/page.tsx'),
    readSource('components/flow/workflow-canvas-surface.tsx'),
  ])

  assert.match(page, /className="flex h-\[calc\(100dvh-3\.5rem\)\] min-h-0 min-w-0 flex-col overflow-hidden"/)
  assert.match(surface, /className="flex min-h-0 min-w-0 flex-1 overflow-hidden"/)
  assert.match(
    surface,
    /className=\{cn\("h-full min-h-0 overflow-hidden", !inspectorVisible && ["']hidden["']\)\}/,
  )
})

test('Houdini-style wiring uses native lifecycle hooks without validation toast side effects', async () => {
  const [interactions, surface, editor, palette, commandStrip, workflowNode] = await Promise.all([
    readSource('components/flow/workflow-canvas-interactions.ts'),
    readSource('components/flow/workflow-canvas-surface.tsx'),
    readSource('components/flow/workflow-editor.tsx'),
    readSource('components/flow/command-palette.tsx'),
    readSource('components/flow/command-strip.tsx'),
    readSource('components/flow/nodes/workflow-node.tsx'),
  ])
  const guards = sourceSection(interactions, 'export function useConnectionGuards', 'export function useCanvasViewportCompaction')

  assert.doesNotMatch(guards, /showToast|setToast/)
  assert.match(surface, /onConnectStart=\{props\.onConnectStart\}/)
  assert.match(surface, /onConnectEnd=\{props\.onConnectEnd\}/)
  assert.match(surface, /onReconnect=\{props\.onReconnect\}/)
  assert.match(surface, /connectionRadius=\{32\}/)
  assert.match(surface, /reconnectRadius=\{24\}/)
  assert.match(surface, /onClickConnectStart=\{props\.onClickConnectStart\}/)
  assert.match(surface, /onClickConnectEnd=\{props\.onClickConnectEnd\}/)
  assert.match(editor, /setPaletteAnchor\(connectionState\.pointer\)/)
  assert.match(editor, /connectNodes\(connection, \{ suppressSnapshot: true \}\)/)
  assert.match(editor, /onClickConnectStart=\{onConnectStart\}/)
  assert.match(editor, /onClickConnectEnd=\{onConnectEnd\}/)
  assert.match(editor, /compatiblePort=\{wiringState === ["']picker["'] \? compatiblePort : undefined\}/)
  assert.match(palette, /getNodeContractByCatalogId\(item\.id\)/)
  assert.match(palette, /originType === ["']unknown["'] \|\| port\.type\.trim\(\)\.toLowerCase\(\) !== ["']unknown["']/)
  assert.match(palette, /const auxiliaryOperators = \(compatiblePort \? \[\] : NODE_PALETTE\)/)
  assert.match(commandStrip, /autoLayout\(["']TB["'], ["']elk["'], true\)/)
  assert.match(workflowNode, /tabIndex: 0/)
  assert.match(workflowNode, /onContextMenu: \(event: MouseEvent\) =>/)
  assert.match(workflowNode, /event\.key === ["']ContextMenu["'] \|\| \(event\.shiftKey && event\.key === ["']F10["']\)/)
  assert.match(workflowNode, /if \(!event\.altKey\) return/)
  assert.match(workflowNode, /new CustomEvent\(["']opencli:workflow-port-menu["']/)
})

test('the right inspector uses graph contracts instead of manual keys and field paths', async () => {
  const [inspector, parameterInterface] = await Promise.all([
    readSource('components/flow/inspector.tsx'),
    readSource('lib/workflow/parameter-interface.ts'),
  ])

  assert.match(inspector, /fieldMappingGap/)
  assert.match(inspector, /onReconnect\(currentEdge, connection\)/)
  assert.match(inspector, /connectNodes\(connection\)/)
  assert.match(inspector, /removeEdgesByIds\(currentEdges\.map\(\(edge\) => edge\.id\)\)/)
  assert.match(inspector, /<SelectValue placeholder=\{copy\.inputUnbound\}/)
  assert.doesNotMatch(inspector, /placeholder=["']data\.source["']/)
  assert.doesNotMatch(inspector, /placeholder=["']data\.target["']/)
  assert.doesNotMatch(parameterInterface, /Config \(JSON\)/)
  assert.match(parameterInterface, /id: `operator\.config\.\$\{key\}`/)
  assert.match(parameterInterface, /fieldId: `config\.\$\{key\}`/)
})

test('dropping a selected node on an edge rewrites root and nested canonical graphs in one undo', async () => {
  const [{ useFlowStore }, { readCanonicalNetworkScope }] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/flow/store-canonical-actions.ts'),
  ])
  const assertInserted = (edges, source, middle, target) => {
    assert.deepStrictEqual(
      edges.map((edge) => `${edge.source}->${edge.target}`).sort(),
      [`${middle}->${target}`, `${source}->${middle}`].sort(),
    )
  }

  const rootProject = workflowProjectFixture(
    [
      canonicalTestNode('left'),
      canonicalTestNode('middle', { ui: { position: { x: 260, y: 0 } } }),
      canonicalTestNode('right', { ui: { position: { x: 520, y: 0 } } }),
    ],
    [{ id: 'root-edge', source: 'left', target: 'right' }],
  )
  useFlowStore.getState().importWorkflowProject(rootProject)
  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: node.id === 'middle' })),
  }))
  useFlowStore.getState().takeSnapshot()
  let snapshotCount = useFlowStore.getState().past.length
  useFlowStore.getState().insertNodeOnEdge('root-edge')
  let current = useFlowStore.getState()
  assertInserted(current.workflowProject.edges, 'left', 'middle', 'right')
  assert.equal(current.past.length, snapshotCount, 'edge replacement stays inside the drag snapshot')
  current.undo()
  assert.deepStrictEqual(useFlowStore.getState().workflowProject.edges, rootProject.edges)

  const nestedProject = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: {
        locked: false,
        nodes: [
          canonicalTestNode('left'),
          canonicalTestNode('middle', { ui: { position: { x: 260, y: 0 } } }),
          canonicalTestNode('right', { ui: { position: { x: 520, y: 0 } } }),
        ],
        edges: [{ id: 'nested-edge', source: 'left', target: 'right' }],
      },
    }),
  ])
  useFlowStore.getState().importWorkflowProject(nestedProject)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 3)
  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: node.id === 'l1__middle' })),
    edges: [{
      id: 'e-l1__nested-edge',
      source: 'l1__left',
      target: 'l1__right',
      type: 'workflow',
      data: { internalOf: 'l1', internalEdgeId: 'nested-edge' },
    }],
  }))
  useFlowStore.getState().takeSnapshot()
  snapshotCount = useFlowStore.getState().past.length
  useFlowStore.getState().insertNodeOnEdge(useFlowStore.getState().edges[0].id)
  current = useFlowStore.getState()
  assertInserted(readCanonicalNetworkScope(current.workflowProject, 'l1').edges, 'left', 'middle', 'right')
  assert.equal(current.past.length, snapshotCount, 'nested insertion also uses one drag snapshot')
  current.undo()
  assert.deepStrictEqual(readCanonicalNetworkScope(useFlowStore.getState().workflowProject, 'l1').edges, [
    { id: 'nested-edge', source: 'left', target: 'right' },
  ])
})

test('typed connection preview reads actual handle contracts and shake disconnect has a bounded gesture', async () => {
  const [{ validateConnection }, { advanceShakeState }] = await Promise.all([
    importTypeScript('lib/flow/graph.ts'),
    importTypeScript('components/flow/workflow-canvas-interactions.ts'),
  ])
  const nodes = [
    {
      id: 'source',
      data: { primitivePorts: [{ id: 'out', direction: 'output', type: 'EvidenceBatch' }] },
    },
    {
      id: 'compatible',
      data: { primitivePorts: [{ id: 'in', direction: 'input', type: 'evidencebatch' }] },
    },
    {
      id: 'blocked',
      data: { primitivePorts: [{ id: 'in', direction: 'input', type: 'URL' }] },
    },
  ]
  const options = { preventCycles: true, typedHandles: true, nodes }

  assert.deepStrictEqual(
    validateConnection([], { source: 'source', target: 'compatible', sourceHandle: 'out', targetHandle: 'in' }, options),
    { ok: true },
  )
  assert.match(
    validateConnection([], { source: 'source', target: 'blocked', sourceHandle: 'out', targetHandle: 'in' }, options).reason,
    /EvidenceBatch.*URL/,
  )

  let state = { lastX: 0, lastDirection: 0, turns: 0, disconnected: false, startedAt: 0 }
  state = advanceShakeState(state, 20, 100)
  state = advanceShakeState(state, -20, 200)
  state = advanceShakeState(state, 20, 300)
  state = advanceShakeState(state, -20, 400)
  assert.equal(state.turns, 3)
  assert.deepStrictEqual(
    advanceShakeState(state, 20, 900),
    { lastX: 20, lastDirection: 0, turns: 0, disconnected: false, startedAt: 900 },
  )
})

test('event and projection actions retain the node reducer contract', async () => {
  const store = await readSource('lib/flow/store.ts')
  const eventAction = sourceSection(store, 'applyWorkflowNodeRunEvent: (event) => {', 'applyWorkflowRunProjection: (projection) => {')
  const projectionAction = sourceSection(store, 'applyWorkflowRunProjection: (projection) => {', 'applyWorkflowEvidenceBatchProjection: (projection, batches) => {')
  const evidenceAction = sourceSection(store, 'applyWorkflowEvidenceBatchProjection: (projection, batches) => {', 'updateWorkflowProfile: (profile) => {')

  assert.match(eventAction, /runtimeNodeIdCandidates\(event\.nodeId, event\.packageNodeId, event\.internalNodeId, event\.nodePath\)/)
  assert.match(eventAction, /patchProjectNodeRunEvent\(node, event, runtimeRunState\)/)
  assert.match(eventAction, /nodes:\s*state\.nodes\.map/)
  assert.match(eventAction, /runtimeLatestEvent:\s*event/)

  assert.match(projectionAction, /runtimeStateByCanvasNodeId\(projection\)/)
  assert.match(projectionAction, /patchProjectNodeRunProjection\(node, stateByCanvasNodeId\)/)
  assert.match(projectionAction, /nodes:\s*state\.nodes\.map/)
  assert.match(projectionAction, /runId:\s*projection\.runId/)

  assert.match(evidenceAction, /applyEvidenceBatchRuntimePatches\(state\.nodes, state\.edges, projection, batches\)/)
})

test('Preview reports dispatch readiness without mutating real execution state', async () => {
  const { applyRuntimeNodePatches, buildRuntimeNodePatches } = await importTypeScript('lib/workflow/runtime-bridge.ts')
  const runArtifact = {
    runId: 'real-run',
    artifactPath: 'runtime://real-trace',
    apiPath: '/api/v1/workflows/runs/real-run',
  }
  const nodes = ['compile-error', 'bound-node', 'missing-node', 'source-a'].map((id) => ({
    id,
    data: { label: id, status: 'success', runArtifact },
  }))
  const patches = buildRuntimeNodePatches({
    compile: {
      valid: false,
      errors: [{ node_id: 'compile-error', code: 'invalid', message: 'Invalid configuration' }],
      plan: {
        runtime: {
          nodes: [
            {
              id: 'bound-node',
              runtime: { binding: { worker: 'worker-a', function_id: 'opencli.dispatch' } },
            },
            {
              id: 'missing-node',
              runtime: {
                missing_runtime: {
                  code: 'missing_runtime_parameter',
                  message: 'Worker is missing',
                },
              },
            },
          ],
        },
      },
    },
    trace: {
      valid: true,
      errors: [],
      workflowId: 'workflow-a',
      runId: 'preview-run',
      traceId: 'preview-trace',
      dispatch: {
        runtime: 'opencli',
        worker: 'worker-a',
        functionId: 'opencli.dispatch',
        mode: 'preview',
      },
      dispatches: [{
        taskId: 'task-a',
        nodeId: 'source-a',
        sourceGroup: 'news',
        site: 'example',
        command: 'search',
        args: {},
        iii: { function_id: 'opencli.dispatch', payload: {} },
      }],
    },
  })

  assert.equal(patches.length, 4)
  for (const patch of patches) {
    assert.equal(Object.hasOwn(patch.data, 'status'), false)
    assert.equal(Object.hasOwn(patch.data, 'runArtifact'), false)
  }
  const result = applyRuntimeNodePatches(nodes, patches)
  for (const node of result) {
    assert.equal(node.data.status, 'success')
    assert.strictEqual(node.data.runArtifact, runArtifact)
  }
  assert.deepStrictEqual(
    result.map((node) => node.data.runtimePreview.status),
    ['blocked', 'bound', 'blocked', 'dispatch-ready'],
  )
})

test('EvidenceBatch projection produces stable node and edge view-models', async () => {
  const { applyEvidenceBatchRuntimePatches } = await importTypeScript('lib/workflow/runtime-bridge.ts')
  const sourceNode = { id: 'package-a', data: { label: 'Source', runtimePreview: {} } }
  const untouchedNode = { id: 'other', data: { label: 'Other' } }
  const sourceEdge = { id: 'edge-a', source: 'package-a', target: 'other', data: {} }
  const untouchedEdge = { id: 'edge-b', source: 'other', target: 'package-a', data: {} }
  const projection = {
    runId: 'run-1',
    traceId: 'trace-1',
    status: 'partial',
    nodes: [],
    clusters: [],
    missingSources: [{
      nodeId: 'package-a::source',
      sourceGroup: 'news',
      status: 'partial',
      reasons: [{ code: 'source_partial', message: 'one source pending', details: {} }],
    }],
    summaries: [],
    conflicts: [],
    artifacts: [],
  }
  const batches = [{
    runId: 'run-1',
    traceId: 'trace-1',
    nodeId: 'package-a::source',
    packageNodeId: 'package-a',
    internalNodeId: 'package-a::source',
    status: 'partial',
    batchId: 'batch-1',
    itemCount: 7,
    recordCount: 5,
    sourceGroup: 'news',
  }]

  const result = applyEvidenceBatchRuntimePatches(
    [sourceNode, untouchedNode],
    [sourceEdge, untouchedEdge],
    projection,
    batches,
  )

  assert.equal(result.nodes[0].data.status, 'running')
  assert.equal(result.nodes[0].data.runtimeEvidenceBatches[0].batchId, 'batch-1')
  assert.equal(result.nodes[0].data.runtimePreview.status, 'evidence-partial')
  assert.equal(result.nodes[0].data.runtimePreview.diagnostic, 'one source pending')
  assert.strictEqual(result.nodes[1], untouchedNode, 'unrelated nodes retain reference identity')

  assert.equal(result.edges[0].animated, true)
  assert.deepStrictEqual(result.edges[0].data.runtimeEvidenceBatch, {
    runId: 'run-1',
    status: 'partial',
    batchIds: ['batch-1'],
    itemCount: 7,
    recordCount: 5,
  })
  assert.strictEqual(result.edges[1], untouchedEdge, 'unrelated edges retain reference identity')
})

test('EvidenceBatch workbench consumes projection, list, selection, and detail state', async () => {
  const [panel, proxy] = await Promise.all([
    readSource('components/flow/run-trace-panel.tsx'),
    readSource('app/api/workflow/evidence-batch-proxy.ts'),
  ])
  const workbench = sourceSection(panel, 'function EvidenceBatchWorkbench(', 'function EvidenceBatchDetailCard(')

  assert.match(workbench, /aria-label="EvidenceBatch results"/)
  assert.match(workbench, /state\.projection/)
  assert.match(workbench, /const batches = state\.batches/)
  assert.match(workbench, /batches\.map/)
  assert.match(workbench, /onSelectBatch\(batch\.batchId\)/)
  assert.match(workbench, /state\.detail\s*\?\s*<EvidenceBatchDetailCard/)
  assert.match(proxy, /export async function proxyWorkflowEvidenceProjectionRequest/)
  assert.match(proxy, /`\$\{BACKEND_URL\}\/api\/v1\/workflows\/runs\/\$\{encodeURIComponent\(runId\)\}\/projection`/)
  assert.match(proxy, /`\$\{BACKEND_URL\}\/api\/v1\/workflows\/runs\/\$\{encodeURIComponent\(runId\)\}\/evidence-batches`/)
})

test('backend Preview traces OpenCLI HDA only when the workflow contains that package', async () => {
  const [panel, helper] = await Promise.all([
    readSource('components/flow/run-trace-panel.tsx'),
    readSource('lib/workflow/backend-opencli-hda-trace.ts'),
  ])
  const preview = sourceSection(panel, 'const runBackendPreview = async () => {', 'const resetRun = () => {')

  assert.match(preview, /const openCLIPackageNodeId = findOpenCLIHDAWorkflowPackageNodeId\(workflowProject\)/)
  assert.match(preview, /compile\.valid && openCLIPackageNodeId/)
  assert.match(preview, /traceOpenCLIHDAWorkflow\(workflowProject, \{[\s\S]*packageNodeId: openCLIPackageNodeId/)
  assert.match(helper, /params\?\.template === "opencli-multi-source"/)
})

test('L2-L4 network edits persist in the canonical workflow graph across scope re-entry', async () => {
  const [canonical, store, slices] = await Promise.all([
    importTypeScript('lib/flow/store-canonical-actions.ts'),
    readSource('lib/flow/store.ts'),
    readSource('lib/flow/store-slices.ts'),
  ])
  const node = (id, internals) => ({
    id,
    kind: 'agent',
    capability: 'normalize',
    params: {},
    ...(internals ? { internals } : {}),
  })
  const project = {
    id: 'nested-edit-regression',
    name: 'Nested edit regression',
    profile: 'intelligence',
    version: 1,
    nodes: [node('l1', {
      locked: false,
      nodes: [node('l2-parent', {
        locked: false,
        nodes: [node('l3-parent', {
          locked: false,
          nodes: [node('l4-existing')],
          edges: [],
        })],
        edges: [],
      })],
      edges: [],
    })],
    edges: [],
    settings: { timezone: 'Asia/Shanghai', deterministicSimulation: true, maxItemsPerRun: 20 },
    adapters: [],
    agentPermissions: {
      canFetchNetwork: false,
      canSendNotifications: false,
      canWriteInbox: true,
      allowedDomains: [],
    },
  }

  const scopes = [
    { parent: 'l1', existing: 'l2-parent', added: 'l2-added', canvas: 'l1__l2-added', expected: { x: 40, y: 60 } },
    { parent: 'l1__l2-parent', existing: 'l3-parent', added: 'l3-added', canvas: 'l1__l2-parent__l3-added', expected: { x: 80, y: 100 } },
    { parent: 'l1__l2-parent__l3-parent', existing: 'l4-existing', added: 'l4-added', canvas: 'l1__l2-parent__l3-parent__l4-added', expected: { x: 120, y: 140 } },
  ]

  let edited = project
  for (const [index, scope] of scopes.entries()) {
    edited = canonical.appendCanonicalNetworkNode(edited, scope.parent, node(scope.added))
    edited = canonical.appendCanonicalNetworkEdge(edited, scope.parent, {
      id: `edge-${index + 2}`,
      source: scope.existing,
      target: scope.added,
    })
    edited = canonical.updateCanonicalProjectNodeByCanvasId(edited, scope.canvas, (current) => ({
      ...current,
      params: { editedAtLayer: index + 2 },
    }))
    edited = canonical.syncCanonicalNetworkNodePositions(edited, scope.parent, [{
      id: scope.canvas,
      position: {
        x: canonical.NETWORK_CANVAS_ORIGIN.x + scope.expected.x,
        y: canonical.NETWORK_CANVAS_ORIGIN.y + scope.expected.y,
      },
    }])
  }

  for (const [index, scope] of scopes.entries()) {
    const firstEntry = canonical.readCanonicalNetworkScope(edited, scope.parent)
    const reEntry = canonical.readCanonicalNetworkScope(edited, scope.parent)
    const added = reEntry.nodes.find((candidate) => candidate.id === scope.added)
    assert.deepStrictEqual(reEntry, firstEntry, `L${index + 2} canonical scope survives exit/re-entry`)
    assert.equal(added.params.editedAtLayer, index + 2)
    assert.deepStrictEqual(added.ui.position, scope.expected)
    assert.ok(reEntry.edges.some((edge) => edge.source === scope.existing && edge.target === scope.added))
  }

  edited = canonical.appendCanonicalNetworkNode(edited, scopes[2].parent, node('l4-delete-me'))
  edited = canonical.appendCanonicalNetworkEdge(edited, scopes[2].parent, {
    id: 'edge-delete-me',
    source: 'l4-existing',
    target: 'l4-delete-me',
  })
  edited = canonical.removeCanonicalNetworkItems(
    edited,
    scopes[2].parent,
    new Set(['l4-delete-me']),
    new Set(),
  )
  const finalL4 = canonical.readCanonicalNetworkScope(edited, scopes[2].parent)
  assert.ok(!finalL4.nodes.some((candidate) => candidate.id === 'l4-delete-me'))
  assert.ok(!finalL4.edges.some((edge) => edge.id === 'edge-delete-me'))

  for (const canonicalWrite of [
    'appendCanonicalNetworkNode',
    'updateCanonicalProjectNodeByCanvasId',
    'appendCanonicalNetworkEdge',
    'removeCanonicalNetworkItems',
    'syncCanonicalNetworkNodePositions',
  ]) {
    assert.ok(store.includes(canonicalWrite) || slices.includes(canonicalWrite), `${canonicalWrite} must be wired into the store`)
  }
  assert.match(store, /exitNodeNetwork:[\s\S]*previous\.snapshot\.nodes/)
  assert.match(store, /enterNodeNetwork:[\s\S]*materializeProjectInternals\(projectNode/)
})

function workflowProjectFixture(nodes, edges = []) {
  return {
    id: 'store-regression',
    name: 'Store regression',
    profile: 'intelligence',
    version: 1,
    nodes,
    edges,
    settings: { timezone: 'Asia/Shanghai', deterministicSimulation: true, maxItemsPerRun: 20 },
    adapters: [],
    agentPermissions: {
      canFetchNetwork: false,
      canSendNotifications: false,
      canWriteInbox: true,
      allowedDomains: [],
    },
  }
}

function canonicalTestNode(id, options = {}) {
  return {
    id,
    kind: 'agent',
    capability: 'normalize',
    params: {},
    ui: { label: id, position: { x: 0, y: 0 }, ...(options.ui ?? {}) },
    ...(options.internals ? { internals: options.internals } : {}),
    ...(options.parameterInterface ? { parameterInterface: options.parameterInterface } : {}),
  }
}

function canonicalNodeAtPath(project, pathIds) {
  let nodes = project.nodes
  let current
  for (const id of pathIds) {
    current = nodes.find((node) => node.id === id)
    assert.ok(current, `missing canonical node path segment: ${id}`)
    nodes = current.internals?.nodes ?? []
  }
  return current
}

function publicParameterInterface(childId) {
  return {
    groups: [{ id: 'general', label: 'General' }],
    fields: [{
      id: 'public-value',
      label: 'Public value',
      groupId: 'general',
      type: 'text',
      binding: { nodeId: childId, source: 'params', fieldId: 'value' },
      value: 'before',
    }],
  }
}

test('actual store actions keep root and nested canonical graphs undoable', async () => {
  const [{ useFlowStore }, { readCanonicalNetworkScope }] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/flow/store-canonical-actions.ts'),
  ])
  const rootProject = workflowProjectFixture([
    canonicalTestNode('root-a'),
    canonicalTestNode('root-b', { ui: { position: { x: 260, y: 0 } } }),
    canonicalTestNode('root-c', { ui: { position: { x: 520, y: 0 } } }),
  ])
  useFlowStore.getState().importWorkflowProject(rootProject)

  useFlowStore.getState().onConnect({ source: 'root-a', target: 'root-b', sourceHandle: null, targetHandle: null })
  let current = useFlowStore.getState()
  assert.equal(current.workflowProject.edges.length, 1, 'root connect persists to the canonical graph')
  const connectedEdgeId = current.edges[0].id
  current.onReconnect(current.edges[0], {
    source: 'root-a',
    target: 'root-c',
    sourceHandle: 'out',
    targetHandle: 'in',
  })
  current = useFlowStore.getState()
  assert.equal(current.edges.length, 1, 'reconnect preserves edge cardinality')
  assert.equal(current.edges[0].id, connectedEdgeId, 'reconnect preserves the existing edge identity')
  assert.equal(current.edges[0].target, 'root-c')
  assert.equal(current.edges[0].data.sourcePort, 'out')
  assert.equal(current.workflowProject.edges[0].target, 'root-c')
  assert.equal(current.workflowProject.edges[0].targetPort, 'in')
  current.undo()
  current = useFlowStore.getState()
  assert.equal(current.edges[0].target, 'root-b', 'one undo restores the previous reconnect target')
  assert.equal(current.workflowProject.edges[0].target, 'root-b')

  current.onNodesChange([{ type: 'position', id: 'root-a', position: { x: 80, y: 120 }, dragging: false }])
  current = useFlowStore.getState()
  assert.deepStrictEqual(current.workflowProject.nodes.find((node) => node.id === 'root-a').ui.position, { x: 80, y: 120 })

  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: node.id === 'root-b' })),
  }))
  useFlowStore.getState().deleteSelected()
  current = useFlowStore.getState()
  assert.ok(!current.workflowProject.nodes.some((node) => node.id === 'root-b'))
  assert.equal(current.workflowProject.edges.length, 0, 'deleting a root node removes canonical incident edges')

  const nestedProject = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: {
        locked: false,
        nodes: [canonicalTestNode('l2')],
        edges: [],
      },
    }),
  ])
  current.importWorkflowProject(nestedProject)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 1)
  useFlowStore.getState().takeSnapshot()
  useFlowStore.getState().onNodesChange([{
    type: 'position',
    id: 'l1__l2',
    position: { x: 600, y: 200 },
    dragging: false,
  }])
  current = useFlowStore.getState()
  assert.deepStrictEqual(readCanonicalNetworkScope(current.workflowProject, 'l1').nodes[0].ui.position, { x: 80, y: 120 })
  current.undo()
  current = useFlowStore.getState()
  assert.deepStrictEqual(readCanonicalNetworkScope(current.workflowProject, 'l1').nodes[0].ui.position, { x: 0, y: 0 })
  assert.deepStrictEqual(current.networkStack.map((entry) => entry.nodeId), ['l1'])
})

test('L2 and L3 primitive edits persist compiler-recognized primitive origins', async () => {
  const [{ useFlowStore }, { getWorkflowPrimitives }, { readCanonicalNetworkScope }, registrySource] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/workflow/node-primitives.ts'),
    importTypeScript('lib/flow/store-canonical-actions.ts'),
    readFile(path.join(frontendRoot, '..', 'backend', 'workflow', 'node_registry.py'), 'utf8'),
  ])
  const project = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: {
        locked: false,
        nodes: [canonicalTestNode('l2-parent', {
          internals: {
            locked: false,
            nodes: [canonicalTestNode('l3-existing')],
            edges: [],
          },
        })],
        edges: [],
      },
    }),
  ])
  const primitive = getWorkflowPrimitives()[0]
  useFlowStore.getState().importWorkflowProject(project)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 1)
  useFlowStore.getState().addPrimitiveNode(primitive, { x: 760, y: 220 })
  let current = useFlowStore.getState()
  const l2Added = readCanonicalNetworkScope(current.workflowProject, 'l1').nodes.find(
    (node) => node.ui?.primitiveId === primitive.id,
  )
  assert.ok(l2Added, 'L2 primitive is persisted canonically')
  assert.equal(l2Added.ui.catalogId, primitive.id, 'catalogId remains as a frontend compatibility hint')

  assert.equal(current.enterNodeNetwork('l1__l2-parent'), 1)
  useFlowStore.getState().addPrimitiveNode(primitive, { x: 720, y: 180 })
  current = useFlowStore.getState()
  const l3Added = readCanonicalNetworkScope(current.workflowProject, 'l1__l2-parent').nodes.find(
    (node) => node.ui?.primitiveId === primitive.id,
  )
  assert.ok(l3Added, 'L3 primitive is persisted canonically')
  const stackBeforeLeafDive = current.networkStack.map((entry) => entry.nodeId)
  assert.equal(current.enterNodeNetwork(`l1__l2-parent__${l3Added.id}`), 0)
  assert.deepStrictEqual(
    useFlowStore.getState().networkStack.map((entry) => entry.nodeId),
    stackBeforeLeafDive,
    'a leaf without internals does not enter an empty scope',
  )
  assert.match(registrySource, new RegExp(`['"]${primitive.id.replaceAll('.', '\\.') }['"]`))
  assert.match(registrySource, /if primitive_id in WORKFLOW_PRIMITIVE_IDS:/)
})

test('duplicate, cut, and paste clone the active canonical scope while visual Add Child is inert', async () => {
  const [{ useFlowStore }, { readCanonicalNetworkScope }] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/flow/store-canonical-actions.ts'),
  ])
  const nestedProject = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: {
        locked: false,
        nodes: [canonicalTestNode('left'), canonicalTestNode('right', { ui: { position: { x: 260, y: 0 } } })],
        edges: [{ id: 'nested-edge', source: 'left', target: 'right' }],
      },
    }),
  ])
  useFlowStore.getState().importWorkflowProject(nestedProject)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 2)
  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: true })),
    edges: [{
      id: 'e-l1__nested-edge',
      source: 'l1__left',
      target: 'l1__right',
      type: 'workflow',
      data: { internalOf: 'l1', internalEdgeId: 'nested-edge' },
    }],
  }))
  const beforeInsert = JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
  })
  useFlowStore.getState().insertNodeOnEdge('e-l1__nested-edge')
  assert.equal(JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
  }), beforeInsert, 'canvas-only edge insertion is disabled')
  useFlowStore.getState().duplicateSelected()
  let current = useFlowStore.getState()
  let nestedScope = readCanonicalNetworkScope(current.workflowProject, 'l1')
  assert.equal(nestedScope.nodes.length, 4, 'nested duplicate writes canonical child nodes')
  assert.equal(nestedScope.edges.length, 2, 'nested duplicate writes the canonical internal edge')
  assert.equal(current.nodes.length, 4)
  assert.equal(current.edges.length, 2)

  const rootProject = workflowProjectFixture([
    canonicalTestNode('only-root'),
    canonicalTestNode('root-anchor', { ui: { position: { x: 320, y: 0 } } }),
  ])
  current.importWorkflowProject(rootProject)
  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: node.id === 'only-root' })),
  }))
  useFlowStore.getState().cut()
  assert.ok(
    !useFlowStore.getState().workflowProject.nodes.some((node) => node.id === 'only-root'),
    'cut removes the canonical source',
  )
  useFlowStore.getState().paste()
  current = useFlowStore.getState()
  assert.equal(current.workflowProject.nodes.length, 2, 'paste restores a new canonical root node')
  assert.equal(current.nodes.length, 2)
  assert.ok(current.workflowProject.nodes.some((node) => node.id !== 'root-anchor' && node.id !== 'only-root'))

  const beforeAddChild = JSON.stringify({
    project: current.workflowProject,
    nodes: current.nodes,
    edges: current.edges,
    historyLength: current.past.length,
  })
  current.addChildNode(current.nodes[0].id)
  const afterAddChild = useFlowStore.getState()
  assert.equal(JSON.stringify({
    project: afterAddChild.workflowProject,
    nodes: afterAddChild.nodes,
    edges: afterAddChild.edges,
    historyLength: afterAddChild.past.length,
  }), beforeAddChild, 'visual Add Child cannot create a second non-canonical hierarchy')
})

test('canonical paste rejects a subtree that would exceed the four-level boundary', async () => {
  const { useFlowStore } = await importTypeScript('lib/flow/store.ts')
  const sourceProject = workflowProjectFixture([
    canonicalTestNode('copied-root', {
      internals: { locked: false, nodes: [canonicalTestNode('copied-child')], edges: [] },
    }),
  ])
  useFlowStore.getState().importWorkflowProject(sourceProject)
  useFlowStore.setState((state) => ({
    nodes: state.nodes.map((node) => ({ ...node, selected: node.id === 'copied-root' })),
  }))
  useFlowStore.getState().copy()

  const destinationProject = workflowProjectFixture([
    canonicalTestNode('l1', { internals: { locked: false, nodes: [
      canonicalTestNode('l2', { internals: { locked: false, nodes: [
        canonicalTestNode('l3', { internals: { locked: false, nodes: [canonicalTestNode('l4')], edges: [] } }),
      ], edges: [] } }),
    ], edges: [] } }),
  ])
  useFlowStore.getState().importWorkflowProject(destinationProject)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 1)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1__l2'), 1)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1__l2__l3'), 1)
  const beforePaste = JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
    historyLength: useFlowStore.getState().past.length,
  })
  useFlowStore.getState().paste()
  assert.equal(JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
    historyLength: useFlowStore.getState().past.length,
  }), beforePaste)
})

test('fallback internals migrate only on an explicit edit and survive scope re-entry', async () => {
  const [{ useFlowStore }, { getWorkflowPrimitives }, { readCanonicalNetworkScope }] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/workflow/node-primitives.ts'),
    importTypeScript('lib/flow/store-canonical-actions.ts'),
  ])
  const project = workflowProjectFixture([
    canonicalTestNode('normalize', { ui: { catalogId: 'intelligence.processing.normalize' } }),
  ])
  useFlowStore.getState().importWorkflowProject(project)
  const beforeUnlock = JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
  })
  assert.equal(useFlowStore.getState().unlockNodeInternals('normalize'), 0)
  assert.equal(JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    nodes: useFlowStore.getState().nodes,
    edges: useFlowStore.getState().edges,
  }), beforeUnlock, 'fallback internals cannot be unlocked into a canvas-only draft')
  const beforeDive = JSON.stringify(useFlowStore.getState().workflowProject)
  const fallbackCount = useFlowStore.getState().enterNodeNetwork('normalize')
  assert.ok(fallbackCount > 0)
  assert.equal(JSON.stringify(useFlowStore.getState().workflowProject), beforeDive, 'navigation is persistence-free')

  const primitive = getWorkflowPrimitives()[0]
  useFlowStore.getState().addPrimitiveNode(primitive, { x: 760, y: 220 })
  let current = useFlowStore.getState()
  let canonicalScope = readCanonicalNetworkScope(current.workflowProject, 'normalize')
  assert.equal(canonicalScope.nodes.length, fallbackCount + 1)
  assert.ok(canonicalScope.nodes.some((node) => node.ui?.primitiveId === primitive.id))

  assert.equal(current.exitNodeNetwork(), true)
  assert.equal(useFlowStore.getState().enterNodeNetwork('normalize'), fallbackCount + 1)
  current = useFlowStore.getState()
  canonicalScope = readCanonicalNetworkScope(current.workflowProject, 'normalize')
  assert.equal(canonicalScope.nodes.length, fallbackCount + 1, 'migrated fallback and explicit node survive re-entry')

  useFlowStore.getState().importWorkflowProject(project)
  const historyBeforeAtomicAdd = useFlowStore.getState().past.length
  assert.ok(useFlowStore.getState().addPrimitiveToNodeNetwork('normalize', primitive, { x: 780, y: 96 }) > 0)
  assert.equal(useFlowStore.getState().past.length, historyBeforeAtomicAdd + 1)
  useFlowStore.getState().undo()
  assert.deepStrictEqual(
    useFlowStore.getState().workflowProject,
    project,
    'one undo restores both the primitive add and fallback-to-canonical migration',
  )
})

test('local public bindings persist at L2/L3, unlocked networks remain editable, and L4 is the depth boundary', async () => {
  const [{ useFlowStore }, { NODE_NETWORK_DEPTH_LIMIT_REACHED }] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/workflow/node-hierarchy.ts'),
  ])
  const project = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: {
        locked: false,
        nodes: [canonicalTestNode('l2-package', {
          parameterInterface: publicParameterInterface('l3-package'),
          internals: {
            locked: false,
            nodes: [canonicalTestNode('l3-package', {
              parameterInterface: publicParameterInterface('l4-atom'),
              internals: {
                locked: false,
                nodes: [canonicalTestNode('l4-atom')],
                edges: [],
              },
            })],
            edges: [],
          },
        })],
        edges: [],
      },
    }),
  ])
  useFlowStore.getState().importWorkflowProject(project)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 1)
  let current = useFlowStore.getState()
  assert.equal(current.nodes[0].draggable, true)
  assert.equal(current.nodes[0].connectable, true)
  current.updateParameterInterfaceField('l1__l2-package', 'public-value', 'from-l2')
  current = useFlowStore.getState()
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-package']).parameterInterface.fields[0].value, 'from-l2')
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-package', 'l3-package']).params.value, 'from-l2')

  assert.equal(current.enterNodeNetwork('l1__l2-package'), 1)
  current = useFlowStore.getState()
  current.updateParameterInterfaceField('l1__l2-package__l3-package', 'public-value', 'from-l3')
  current = useFlowStore.getState()
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-package', 'l3-package']).parameterInterface.fields[0].value, 'from-l3')
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-package', 'l3-package', 'l4-atom']).params.value, 'from-l3')

  assert.equal(current.enterNodeNetwork('l1__l2-package__l3-package'), 1)
  current = useFlowStore.getState()
  const stackAtL4 = current.networkStack.map((entry) => entry.nodeId)
  assert.equal(current.enterNodeNetwork('l1__l2-package__l3-package__l4-atom'), NODE_NETWORK_DEPTH_LIMIT_REACHED)
  assert.deepStrictEqual(useFlowStore.getState().networkStack.map((entry) => entry.nodeId), stackAtL4)
})

test('four-level runtime paths update exact nodes without a completed leaf clearing a blocked package', async () => {
  const { useFlowStore } = await importTypeScript('lib/flow/store.ts')
  const project = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: { locked: false, nodes: [canonicalTestNode('l2', {
        internals: { locked: false, nodes: [canonicalTestNode('l3', {
          internals: { locked: false, nodes: [canonicalTestNode('l4')], edges: [] },
        })], edges: [] },
      })], edges: [] },
    }),
  ])
  useFlowStore.getState().importWorkflowProject(project)
  useFlowStore.getState().applyWorkflowNodeRunEvent({
    id: 'event-l4-blocked',
    sequence: 1,
    workflowId: project.id,
    workflowRunId: 'run-1',
    traceId: 'trace-1',
    nodeId: 'l4',
    nodePath: ['l1', 'l2', 'l3', 'l4'],
    eventType: 'blocked',
    createdAt: '2026-07-14T00:00:00Z',
    details: {},
  })
  let current = useFlowStore.getState()
  for (const pathIds of [['l1'], ['l1', 'l2'], ['l1', 'l2', 'l3'], ['l1', 'l2', 'l3', 'l4']]) {
    assert.equal(canonicalNodeAtPath(current.workflowProject, pathIds).ui.runtimeRunState.status, 'blocked')
  }

  const state = (nodeId, nodePath, status, eventCount) => ({
    nodeId,
    nodePath,
    status,
    sourceGroups: [],
    eventCount,
    blockReasons: status === 'blocked' ? [{ code: 'blocked', message: 'blocked', details: {} }] : [],
    batches: [],
  })
  current.applyWorkflowRunProjection({
    workflowId: project.id,
    runId: 'run-1',
    traceId: 'trace-1',
    valid: true,
    status: 'blocked',
    startedAt: '2026-07-14T00:00:00Z',
    updatedAt: '2026-07-14T00:01:00Z',
    eventCount: 3,
    nodeStates: [
      state('l1', ['l1'], 'blocked', 1),
      state('l4', ['l1', 'l2', 'l3', 'l4'], 'blocked', 1),
      state('l4', ['l1', 'l2', 'l3', 'l4'], 'completed', 2),
    ],
    errors: [],
  })
  current = useFlowStore.getState()
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1']).ui.runtimeRunState.status, 'blocked')
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1', 'l2', 'l3', 'l4']).ui.runtimeRunState.status, 'completed')
})

test('locked package internals keep semantic identity, typed ports, runtime capability, and handle-bound edges', async () => {
  const [{ useFlowStore }, catalog, nodePath, storeSource, nodeSource, inspectorSource] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/node-path.ts'),
    readSource('lib/flow/store.ts'),
    readSource('components/flow/nodes/workflow-node.tsx'),
    readSource('components/flow/inspector.tsx'),
  ])
  const catalogItem = catalog.WORKFLOW_NODE_CATALOG.find(
    (item) => item.id === catalog.RECORD_HYGIENE_PACKAGE_CATALOG_ID,
  )
  assert.ok(catalogItem)
  const packageNode = catalog.createWorkflowNodeFromCatalog(catalogItem, 'hygiene', { x: 0, y: 0 })
  const project = workflowProjectFixture([packageNode])
  useFlowStore.getState().importWorkflowProject(project)
  assert.equal(useFlowStore.getState().enterNodeNetwork('hygiene'), 3)

  const contract = (bindingId, inputPorts, outputPorts) => ({
    schemaVersion: 1,
    bindingId,
    status: 'executable',
    inputShape: { ports: inputPorts.map(([name, type]) => ({ name, type })), params: [] },
    outputShape: { ports: outputPorts.map(([name, type]) => ({ name, type })), artifacts: [] },
    permissionGate: { required: [] },
    configGate: { required: [] },
    eventShape: { events: [] },
    fixtureCoverage: { cases: [] },
    certification: { realNodeIoContract: true, realWebhookDelivery: false },
    canvas: { exposeResourceInternals: false },
  })
  const runtime = (id, kind, capability, ioContract) => ({
    id,
    label: id,
    surface: 'catalog',
    status: 'runnable',
    backendAvailable: true,
    kind,
    capability,
    reason: null,
    missing: [],
    tags: ['test'],
    source: 'test.runtime',
    manifest: { contract: ioContract },
  })
  useFlowStore.getState().applyWorkflowCapabilities({
    version: 'test',
    catalog: [
      runtime('intelligence.processing.normalize', 'agent', 'normalize', contract('normalize', [['in', 'items[]']], [['out', 'recordCandidate[]']])),
      runtime('intelligence.processing.dedupe', 'agent', 'dedupe', contract('dedupe', [['in', 'recordCandidate[]']], [['out', 'recordCandidate[]']])),
      runtime('intelligence.control.record-acceptance', 'control', 'accept', contract('accept', [['candidates', 'recordCandidate[]']], [['records', 'record[]']])),
    ],
    primitives: [],
    channels: [],
    notifiers: [],
    triggers: [],
    resources: [],
  })

  const current = useFlowStore.getState()
  const normalize = current.nodes.find((node) => node.id === 'hygiene__normalize')
  const dedupe = current.nodes.find((node) => node.id === 'hygiene__dedupe')
  const acceptance = current.nodes.find((node) => node.id === 'hygiene__record-acceptance')
  assert.equal(normalize.data.runtimeCapability.status, 'runnable')
  assert.equal(dedupe.data.runtimeCapability.status, 'runnable')
  assert.equal(acceptance.data.runtimeCapability.status, 'runnable')
  assert.deepStrictEqual(dedupe.data.runtimeContract.inputShape.ports.map((port) => port.name), ['in'])
  assert.deepStrictEqual(acceptance.data.runtimeContract.outputShape.ports.map((port) => port.name), ['records'])
  assert.deepStrictEqual(current.nodes.map((node) => node.data.status), ['idle', 'idle', 'idle'])
  assert.equal(
    nodePath.findWorkflowProjectNodeByCanvasId(current.workflowProject, 'hygiene__dedupe').capability,
    'dedupe',
  )

  assert.match(storeSource, /sourceHandle: edge\.sourcePort/)
  assert.match(storeSource, /targetHandle: edge\.targetPort/)
  assert.doesNotMatch(storeSource, /status: mode === ["']network["'] \? ["']success["']/)
  assert.match(nodeSource, /findWorkflowProjectNodeByCanvasId\(workflowProject, id\)/)
  assert.match(nodeSource, /typeCaption\(data\.category, canonical\?\.capability \?\? data\.nodeType\)/)
  assert.doesNotMatch(nodeSource, /LOCKED PACKAGE/)
  assert.match(inspectorSource, /findWorkflowProjectNodeByCanvasId\(workflowProject, node\.id\)/)
})

test('internal primitive menu edits the clicked node child scope at L2/L3 and rejects L4', async () => {
  const [{ useFlowStore }, { getWorkflowPrimitives }, { NODE_NETWORK_DEPTH_LIMIT_REACHED }, menuSource] = await Promise.all([
    importTypeScript('lib/flow/store.ts'),
    importTypeScript('lib/workflow/node-primitives.ts'),
    importTypeScript('lib/workflow/node-hierarchy.ts'),
    readSource('components/flow/workflow-node-menu-actions.ts'),
  ])
  const menuAction = sourceSection(menuSource, 'const addPrimitiveFromMenu', 'const lockInternals')
  assert.match(menuAction, /addPrimitiveToNodeNetwork\([\s\S]*nodeMenu\.nodeId/)
  assert.doesNotMatch(menuAction, /isInsideNetwork/)
  assert.match(menuAction, /if \(count <= 0\)[\s\S]*return/)

  const project = workflowProjectFixture([
    canonicalTestNode('l1', {
      internals: { locked: false, nodes: [canonicalTestNode('l2-target')], edges: [] },
    }),
  ])
  const [l3Primitive, l4Primitive] = getWorkflowPrimitives()
  useFlowStore.getState().importWorkflowProject(project)
  assert.equal(useFlowStore.getState().enterNodeNetwork('l1'), 1)
  assert.ok(useFlowStore.getState().addPrimitiveToNodeNetwork('l1__l2-target', l3Primitive, { x: 780, y: 96 }) > 0)

  let current = useFlowStore.getState()
  const l2Target = canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-target'])
  assert.equal(canonicalNodeAtPath(current.workflowProject, ['l1']).internals.nodes.length, 1, 'L3 is not added as an L2 sibling')
  const l3Node = l2Target.internals.nodes.find((node) => node.ui?.primitiveId === l3Primitive.id)
  assert.ok(l3Node)

  const l3CanvasId = `l1__l2-target__${l3Node.id}`
  assert.ok(current.addPrimitiveToNodeNetwork(l3CanvasId, l4Primitive, { x: 780, y: 96 }) > 0)
  current = useFlowStore.getState()
  const l4Node = canonicalNodeAtPath(current.workflowProject, ['l1', 'l2-target', l3Node.id]).internals.nodes.find(
    (node) => node.ui?.primitiveId === l4Primitive.id,
  )
  assert.ok(l4Node)

  const beforeL4Edit = JSON.stringify({
    project: current.workflowProject,
    stack: current.networkStack,
  })
  assert.equal(
    current.addPrimitiveToNodeNetwork(`${l3CanvasId}__${l4Node.id}`, l4Primitive, { x: 780, y: 96 }),
    NODE_NETWORK_DEPTH_LIMIT_REACHED,
  )
  assert.equal(JSON.stringify({
    project: useFlowStore.getState().workflowProject,
    stack: useFlowStore.getState().networkStack,
  }), beforeL4Edit)
})
