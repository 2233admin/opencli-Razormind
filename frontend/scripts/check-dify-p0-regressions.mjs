import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

import { parse as parseYaml } from 'yaml'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(frontendRoot, '..')

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
    if (url.endsWith('.ts') || url.endsWith('.tsx')) {
      const source = stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
        mode: 'strip',
        sourceUrl: url,
      })
      return { format: 'module', source, shortCircuit: true }
    }
    return nextLoad(url, context)
  },
})

const readFrontendSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8')
const readRepositorySource = (relativePath) => readFile(path.join(repositoryRoot, relativePath), 'utf8')
const importTypeScript = (relativePath) => import(pathToFileURL(path.join(frontendRoot, relativePath)).href)

test('pure Dify DSL becomes one locked expandable compatibility package', async () => {
  const [{ translateDifyWorkflowToWorkflowProject }, source] = await Promise.all([
    importTypeScript('lib/workflow/dify-translator.ts'),
    readRepositorySource('tests/fixtures/dify/pure_logic.yml'),
  ])
  const translated = translateDifyWorkflowToWorkflowProject(parseYaml(source))

  assert.equal(translated.ok, true)
  assert.equal(translated.project.nodes.length, 1)
  const packageNode = translated.project.nodes[0]
  assert.equal(packageNode.params.packageFormat, 'dify')
  assert.equal(packageNode.internals.locked, true)
  assert.equal(packageNode.internals.nodes.length, 2)
  assert.equal(packageNode.ui.package.expandable, true)
  assert.equal(translated.report.executable, false, 'browser fallback must never claim execution readiness')
})

test('all 25 user-visible Dify node families resolve to stable OpenCLI capability IDs', async () => {
  const {
    DIFY_INTERNAL_NODE_TYPES,
    DIFY_MIGRATABLE_NODE_TYPES,
    DIFY_NODE_CAPABILITY_IDS,
    resolveDifyNodeCapability,
  } = await importTypeScript('lib/workflow/dify-capability-map.ts')
  const backendCatalog = await readRepositorySource('backend/plugins/capability_catalog.py')

  assert.equal(DIFY_MIGRATABLE_NODE_TYPES.length, 25)
  for (const nodeType of DIFY_MIGRATABLE_NODE_TYPES) {
    const config = nodeType === 'list-operator' ? { operation: 'filter' } : {}
    const mapping = resolveDifyNodeCapability(nodeType, config)
    assert.notEqual(mapping.resolution, 'unsupported', nodeType)
    assert.ok(mapping.capabilityId, `${nodeType} must retain a stable capability catalog ID`)
    assert.ok(
      backendCatalog.includes(`"${nodeType}": "${mapping.capabilityId}"`),
      `${nodeType} must use the backend-authoritative capability ID`,
    )
  }

  assert.equal(resolveDifyNodeCapability('template-transform').capabilityId, DIFY_NODE_CAPABILITY_IDS.templateTransform)
  assert.equal(resolveDifyNodeCapability('assigner').capabilityId, DIFY_NODE_CAPABILITY_IDS.variableAssign)
  assert.equal(resolveDifyNodeCapability('variable-assigner').capabilityId, DIFY_NODE_CAPABILITY_IDS.variableAggregate)
  assert.equal(resolveDifyNodeCapability('list-operator', { operation: 'sort' }).capabilityId, DIFY_NODE_CAPABILITY_IDS.listSort)
  assert.equal(resolveDifyNodeCapability('tool').capabilityId, DIFY_NODE_CAPABILITY_IDS.tool)

  assert.deepEqual(DIFY_INTERNAL_NODE_TYPES, ['loop-start', 'loop-end', 'iteration-start'])
  for (const nodeType of DIFY_INTERNAL_NODE_TYPES) {
    assert.equal(resolveDifyNodeCapability(nodeType).resolution, 'unsupported')
  }
})

test('root canvas exposes the curated Dify common-node set without hiding design-only primitives', async () => {
  const [{ DIFY_NODE_CAPABILITY_IDS }, primitives, palette] = await Promise.all([
    importTypeScript('lib/workflow/dify-capability-map.ts'),
    importTypeScript('lib/workflow/node-primitives.ts'),
    readFrontendSource('components/flow/command-palette.tsx'),
  ])
  const expectedIds = [
    DIFY_NODE_CAPABILITY_IDS.start,
    DIFY_NODE_CAPABILITY_IDS.end,
    DIFY_NODE_CAPABILITY_IDS.answer,
    DIFY_NODE_CAPABILITY_IDS.llm,
    DIFY_NODE_CAPABILITY_IDS.agent,
    DIFY_NODE_CAPABILITY_IDS.knowledgeRetrieval,
    DIFY_NODE_CAPABILITY_IDS.questionClassifier,
    DIFY_NODE_CAPABILITY_IDS.ifElse,
    DIFY_NODE_CAPABILITY_IDS.switch,
    DIFY_NODE_CAPABILITY_IDS.humanInput,
    DIFY_NODE_CAPABILITY_IDS.iteration,
    DIFY_NODE_CAPABILITY_IDS.loop,
    DIFY_NODE_CAPABILITY_IDS.code,
    DIFY_NODE_CAPABILITY_IDS.templateTransform,
    DIFY_NODE_CAPABILITY_IDS.variableAssign,
    DIFY_NODE_CAPABILITY_IDS.variableAggregate,
    DIFY_NODE_CAPABILITY_IDS.parameterExtract,
    DIFY_NODE_CAPABILITY_IDS.documentExtract,
    DIFY_NODE_CAPABILITY_IDS.httpRequest,
  ]

  assert.deepEqual(primitives.DIFY_COMMON_NODE_CAPABILITY_IDS, expectedIds)
  assert.equal(new Set(expectedIds).size, expectedIds.length)
  for (const id of expectedIds) {
    assert.equal(
      primitives.WORKFLOW_PRIMITIVES.filter((item) => item.id === id).length,
      1,
      `${id} must reuse one exact primitive instead of adding a duplicate`,
    )
  }
  assert.deepEqual(
    primitives.getDifyCommonWorkflowPrimitives().map((item) => item.id),
    expectedIds,
  )
  assert.ok(primitives.getWorkflowPrimitives().length > expectedIds.length, 'nested networks retain the full primitive library')
  assert.match(palette, /inNodeNetwork\s*\?\s*getWorkflowPrimitives\(\)\s*:\s*getDifyCommonWorkflowPrimitives\(\)/)
  assert.match(palette, /catalogOperatorIds\.has\(item\.id\)/)
  assert.doesNotMatch(palette, /getDifyCommonWorkflowPrimitives\(\)[\s\S]{0,200}catalogItemUnavailable/)
})

test('Dify preview preserves sanitized source config and reports ambiguous or missing mappings', async () => {
  const { translateDifyWorkflowToWorkflowProject } = await importTypeScript('lib/workflow/dify-translator.ts')
  const translated = translateDifyWorkflowToWorkflowProject({
    kind: 'app',
    version: '0.3.0',
    app: { name: 'Mapping fidelity', mode: 'workflow' },
    workflow: {
      graph: {
        nodes: [
          {
            id: 'tool-node',
            position: { x: 12, y: 34 },
            data: {
              type: 'tool',
              title: 'Search tool',
              provider_id: 'acme/search',
              provider_name: 'Acme Search',
              tool_name: 'lookup',
              tool_configurations: Object.fromEntries([
                ['region', 'cn'],
                ['api_key', ['do', 'not', 'store'].join('-')],
              ]),
            },
          },
          {
            id: 'list-node',
            data: { type: 'list-operator', title: 'Unknown list operation', custom_flag: true },
          },
          {
            id: 'future-node',
            data: { type: 'future-node-family', title: 'Future node', nested: { preserved: 42 } },
          },
        ],
        edges: [],
      },
    },
  })

  assert.equal(translated.ok, true)
  const internalNodes = translated.project.nodes[0].internals.nodes
  const tool = internalNodes.find((node) => node.params.difyType === 'tool')
  const list = internalNodes.find((node) => node.params.difyType === 'list-operator')
  const future = internalNodes.find((node) => node.params.difyType === 'future-node-family')

  assert.equal(tool.params.capabilityRef.id, 'external.tool.capability')
  assert.equal(tool.params.capabilityRef.resolution, 'backend')
  assert.equal(tool.params.config.provider_id, 'acme/search')
  assert.equal(tool.params.config.tool_name, 'lookup')
  assert.equal(tool.params.config.tool_configurations.region, 'cn')
  assert.equal(tool.params.config.tool_configurations.api_key, '[REDACTED]')
  assert.deepEqual(tool.params.sourceProvenance, {
    format: 'dify-app-dsl',
    version: '0.3.0',
    nodeId: 'tool-node',
    nodeType: 'tool',
    position: { x: 12, y: 34 },
  })

  assert.equal(list.params.capabilityRef.id, null)
  assert.deepEqual(list.params.capabilityRef.candidates, [
    'primitive.core.list-filter',
    'primitive.core.list-sort',
  ])
  assert.equal(future.params.config.nested.preserved, 42)
  assert.equal(future.params.capabilityRef.resolution, 'unsupported')
  assert.ok(translated.report.blockers.some((blocker) => blocker.code === 'import.mapping_ambiguous' && blocker.nodeId === 'tool-node'))
  assert.ok(translated.report.blockers.some((blocker) => blocker.code === 'import.mapping_ambiguous' && blocker.nodeId === 'list-node'))
  assert.ok(translated.report.blockers.some((blocker) => blocker.code === 'import.mapping_missing' && blocker.nodeId === 'future-node'))
  assert.equal(translated.report.executable, false)
  assert.equal(translated.project.adapters.length, 0)
  assert.doesNotMatch(JSON.stringify(translated.project), /do-not-store/)
  assert.doesNotMatch(JSON.stringify(translated.project), /"mode":"mock"|"mode":"fixture"/)
})

test('Studio uses the managed backend import boundary and explains exact blockers', async () => {
  const [codec, backendImport, studio, commandStrip] = await Promise.all([
    readFrontendSource('lib/workflow/codec.ts'),
    readFrontendSource('lib/workflow/backend-dify-import.ts'),
    readFrontendSource('app/(app)/studio/page.tsx'),
    readFrontendSource('components/flow/command-strip.tsx'),
  ])

  assert.match(codec, /translateWorkflowDslManaged/)
  assert.match(codec, /await importDifyWorkflow\(source\)/)
  assert.match(backendImport, /\/api\/workflow\/import\/dify/)
  assert.match(studio, /translateWorkflowDslManaged\(await file\.text\(\)\)/)
  assert.match(studio, /Graphon/)
  assert.match(commandStrip, /difyReport\.blockers\.length/)
})

test('plugin center is workspace-scoped, registry-backed, and never executes plugin-owned frontend code', async () => {
  const [catalog, page, dialog, templateRoute] = await Promise.all([
    readFrontendSource('lib/plugins/backend-plugin-catalog.ts'),
    readFrontendSource('app/(app)/plugins/page.tsx'),
    readFrontendSource('components/plugins/dify-package-import-dialog.tsx'),
    readFrontendSource('app/(app)/studio/templates/page.tsx'),
  ])

  assert.match(catalog, /pluginPath\(workspaceId\)/)
  assert.match(catalog, /\/api\/v1\/workspaces\/\$\{encodeURIComponent\(workspaceId\)\}\/plugins/)
  assert.match(catalog, /getApiAuthHeaders\(\)/)
  assert.match(catalog, /updatePluginInstallation/)
  assert.match(page, /PluginSubtypeTabs/)
  assert.match(page, /TemplateCatalog workspaceId=\{workspaceId\}/)
  assert.match(page, /不会把它们标记成“已安装”/)
  assert.match(dialog, /workspaceId/)
  assert.match(dialog, /只登记元数据，不执行包内代码/)
  assert.match(templateRoute, /type: 'template'/)
  for (const source of [catalog, page, dialog]) {
    assert.doesNotMatch(source, /eval\s*\(/)
    assert.doesNotMatch(source, /new\s+Function\s*\(/)
    assert.doesNotMatch(source, /import\s*\(\s*provider/)
  }
})

test('projected plugin nodes show provenance and stay locked in the palette', async () => {
  const [nodeCatalog, catalog, palette] = await Promise.all([
    importTypeScript('lib/workflow/node-catalog.ts'),
    readFrontendSource('lib/workflow/node-catalog.ts'),
    readFrontendSource('components/flow/command-palette.tsx'),
  ])

  const item = nodeCatalog.getWorkflowNodeCatalog('intelligence', {
    catalog: [{
      id: 'plugin.fixture.tool.lookup',
      label: 'Fixture Lookup',
      surface: 'catalog',
      status: 'blocked',
      backendAvailable: false,
      kind: 'action',
      capability: 'store',
      provider: 'example/research_tools',
      runtimeBinding: null,
      reason: 'Adapter required',
      missing: ['dify_plugin_runtime_adapter'],
      tags: ['plugin', 'dify', 'tool'],
      source: 'backend.services.plugin_registry_service',
      manifest: {
        plugin: {
          installationId: 'fixture-installation',
          providerKey: 'example/research_tools',
          version: '1.2.3',
          capabilityId: 'lookup',
          family: 'tool',
        },
        canvas: { node: true, locked: true, lockReason: 'Adapter required' },
      },
    }],
  }).find((candidate) => candidate.id === 'plugin.fixture.tool.lookup')

  assert.ok(item)
  assert.equal(nodeCatalog.workflowCatalogItemLocked(item), true)
  assert.deepEqual(nodeCatalog.workflowCatalogPluginProvenance(item), {
    providerKey: 'example/research_tools',
    version: '1.2.3',
  })
  assert.equal(item.category, 'output')

  assert.match(catalog, /backend\.services\.plugin_registry_service/)
  assert.match(catalog, /workflowCatalogItemLocked/)
  assert.match(catalog, /workflowCatalogPluginProvenance/)
  assert.match(palette, /function catalogItemUnavailable/)
  assert.match(palette, /return workflowCatalogItemLocked\(item\)/)
  assert.match(palette, /disabled=\{catalogItemUnavailable\(item\)\}/)
  assert.ok(
    /workflowCatalogPluginProvenance\(item\) !== null/.test(palette) ||
      !/filter\(\(item\) => item\.category === "package"\)/.test(palette),
  )
  assert.match(palette, /provenance\.providerKey/)
  assert.match(palette, /该插件能力尚未绑定运行适配器/)
})

test('backend keeps Dify execution behind the pinned Graphon binding', async () => {
  const [projection, compile, sidecar] = await Promise.all([
    readRepositorySource('backend/workflow/capability_projection.py'),
    readRepositorySource('backend/workflow/dify_compile.py'),
    readRepositorySource('compat/dify_graphon_runtime/engine.py'),
  ])

  assert.match(projection, /package\.compat\.dify-workflow/)
  assert.match(projection, /DIFY_GRAPHON_BINDING_ID/)
  assert.match(compile, /DIFY_GRAPHON_COMMIT/)
  assert.match(compile, /"allowTools": False/)
  assert.match(sidecar, /GRAPHON_COMMIT = "b187ce7927fea1a7c137b642be3f78e3abb9f7de"/)
})
