import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

registerHooks({
  resolve(specifier, context, nextResolve) {
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
    if (url.endsWith('.ts')) {
      const source = stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
        mode: 'strip',
        sourceUrl: url,
      })
      return { format: 'module', source, shortCircuit: true }
    }
    return nextLoad(url, context)
  },
})

const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8')
const importTypeScript = (relativePath) => import(pathToFileURL(path.join(frontendRoot, relativePath)).href)

const node = (id, x, y, label = id) => ({
  id,
  position: { x, y },
  data: { label, nodeType: 'default', category: 'transform', icon: 'Box' },
})

test('workflow outline preserves hierarchy while filtering and collapsing', async () => {
  const {
    buildWorkflowOutlineRows,
    filterWorkflowOutlineRows,
    visibleWorkflowOutlineRows,
    workflowOutlineRowHasChildren,
  } = await importTypeScript('lib/workflow/workflow-outline.ts')

  const nodes = [
    node('root', 0, 0, 'Start'),
    node('branch', 0, 100, 'Classify'),
    node('leaf', 0, 200, 'Publish'),
    node('other', 200, 0, 'Detached'),
  ]
  const edges = [
    { id: 'root-branch', source: 'root', target: 'branch' },
    { id: 'branch-leaf', source: 'branch', target: 'leaf', sourceHandle: 'approved' },
  ]
  const rows = buildWorkflowOutlineRows(nodes, edges)

  assert.deepEqual(
    rows.map(({ nodeId, depth, disconnected }) => ({
      nodeId,
      depth,
      disconnected,
    })),
    [
      { nodeId: 'root', depth: 0, disconnected: false },
      { nodeId: 'branch', depth: 1, disconnected: false },
      { nodeId: 'leaf', depth: 2, disconnected: false },
      { nodeId: 'other', depth: 0, disconnected: true },
    ],
  )
  assert.equal(workflowOutlineRowHasChildren(rows, 0), true)
  assert.equal(workflowOutlineRowHasChildren(rows, 1), true)
  assert.equal(workflowOutlineRowHasChildren(rows, 2), false)

  assert.deepEqual(
    visibleWorkflowOutlineRows(rows, new Set(['branch'])).map((row) => row.nodeId),
    ['root', 'branch', 'other'],
  )
  assert.deepEqual(
    filterWorkflowOutlineRows(rows, 'publish', (nodeId) => nodes.find((item) => item.id === nodeId)?.data.label ?? '')
      .map((row) => row.nodeId),
    ['root', 'branch', 'leaf'],
    'search should retain matching rows and their ancestors',
  )
})

test('workflow upstream discovery uses only real graph ancestors', async () => {
  const {
    workflowDirectUpstreamNodeIds,
    workflowInputReferenceForPort,
    workflowUpstreamNodeIds,
  } = await importTypeScript('lib/workflow/workflow-outline.ts')
  const edges = [
    { id: 'a-b', source: 'a', target: 'b' },
    { id: 'b-c', source: 'b', target: 'c' },
    { id: 'x-y', source: 'x', target: 'y' },
  ]

  assert.deepEqual([...workflowUpstreamNodeIds('c', edges)], ['b', 'a'])
  assert.deepEqual([...workflowUpstreamNodeIds('y', edges)], ['x'])
  assert.deepEqual([...workflowUpstreamNodeIds('a', edges)], [])
  assert.deepEqual([...workflowDirectUpstreamNodeIds('c', edges)], ['b'])
  assert.equal(workflowInputReferenceForPort('records'), '{{records}}')
  assert.equal(workflowInputReferenceForPort('result.items'), '{{result.items}}')
  assert.equal(workflowInputReferenceForPort('not valid'), undefined)
})

test('inspector keeps navigation gestures and exposes contract-backed controls', async () => {
  const [inspector, shell] = await Promise.all([
    readSource('components/flow/inspector.tsx'),
    readSource('components/flow/inspector-shell.tsx'),
  ])

  assert.match(inspector, /data-testid="workflow-outline-search"/)
  assert.match(inspector, /toggleOutlineNode/)
  assert.match(inspector, /onClick=\{\(\) => onSelectNode\(node\.id\)\}/)
  assert.match(inspector, /onDoubleClick=\{\(\) => onOpenNode\(node\.id\)\}/)
  assert.match(inspector, /event\.key !== "Enter"/)
  assert.match(inspector, /workflowStatusDotClass/)
  assert.match(shell, /export const workflowStatusDotClass/)

  assert.match(inspector, /upstreamVariableOptions/)
  assert.match(inspector, /candidateContract\.ports/)
  assert.match(inspector, /port\.direction === "output"/)
  assert.match(inspector, /data-testid="parameter-variable-selector"/)
  assert.match(inspector, /workflowInputReferenceForPort/)
  assert.doesNotMatch(inspector, /function parameterReferenceValue\(nodeId/)
  assert.match(inspector, /onValueChange=\{\(value\) => value && updateParameterField\(field, value\)\}/)
  assert.match(inspector, /href="\/providers"/)
  assert.doesNotMatch(inspector, /href="\/sources"/)
  assert.match(inspector, /data-testid="source-pool-search"/)
  assert.match(inspector, /groupedSources\.map/)
  assert.match(inspector, /data-testid="source-pool-preflight"/)
  assert.match(inspector, /data-testid="source-pool-editor"/)
  assert.match(inspector, /data-testid="source-pool-group"/)
  assert.match(inspector, /data-testid="source-pool-source-card"/)
  assert.match(inspector, /SOURCE_POOL_TOUR_STORAGE_KEY/)
  assert.match(inspector, /import \{ driver, type Driver \} from "driver\.js"/)
  assert.match(inspector, /element: '\[data-testid="source-pool-group"\]'/)
  assert.match(inspector, /smoothScroll: !reduceMotion/)
  assert.doesNotMatch(inspector, /import\("driver\.js"\)/)
  assert.match(inspector, /<Ripple/)
  assert.match(inspector, /matchWorkflowFleetCapability/)
  assert.match(inspector, /binding\.status !== "active"/)
  assert.match(inspector, /!source\.sourceBindingRevisionId \|\| !source\.sourceBindingRevisionNumber/)
  assert.match(inspector, /<SourceBindingRevisionControls/)
  assert.match(inspector, /useProjectSourceBindingRevisions/)
  assert.match(inspector, /sourceBindingRevisionId/)
})

test('generic JSON fields render only inside the existing Advanced disclosure', async () => {
  const inspector = await readSource('components/flow/inspector.tsx')

  assert.match(inspector, /regularParameterFields/)
  assert.match(inspector, /advancedParameterFields/)
  assert.match(inspector, /field\.type !== "json"/)
  assert.match(inspector, /data-testid="advanced-parameter-fields"/)
  assert.match(inspector, /advancedParameterFields\.map\(\(field\) => renderParameterField\(field\)\)/)
})

test('inspector owns image actions and map or runtime detail outside the canvas node', async () => {
  const [inspector, node] = await Promise.all([
    readSource('components/flow/inspector.tsx'),
    readSource('components/flow/nodes/workflow-node.tsx'),
  ])

  assert.match(inspector, /IMAGE_GENERATION_CATALOG_ID/)
  assert.match(inspector, /IMAGE_ASSET_CATALOG_ID/)
  assert.match(inspector, /imageStudioHref/)
  assert.match(inspector, /Create Image Canvas/)
  assert.match(inspector, /Select Workspace Assets/)
  assert.match(inspector, /RUN & RELATION DETAILS/)
  assert.match(inspector, /data\.runtimeEvidenceBatches/)
  assert.doesNotMatch(node, /imageStudioHref|imageAssetHref|miniNetwork/)
})
