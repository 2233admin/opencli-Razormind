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
    if (specifier.startsWith('@/')) candidates.push(path.join(frontendRoot, specifier.slice(2)))
    else if (specifier.startsWith('.') && context.parentURL?.startsWith('file:')) {
      candidates.push(path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier))
    }
    for (const candidate of candidates) {
      for (const resolvedPath of [candidate, `${candidate}.ts`, `${candidate}.tsx`]) {
        if (existsSync(resolvedPath)) return { url: pathToFileURL(resolvedPath).href, shortCircuit: true }
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.ts') || url.endsWith('.tsx')) {
      return {
        format: 'module',
        source: stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
          mode: 'strip',
          sourceUrl: url,
        }),
        shortCircuit: true,
      }
    }
    return nextLoad(url, context)
  },
})

const readFrontendSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8')
const importTypeScript = (relativePath) => import(pathToFileURL(path.join(frontendRoot, relativePath)).href)

const backendCatalog = {
  version: 'opencli.node-capabilities.v1',
  authority: 'backend',
  nodes: [{
    id: 'workflow.native.template',
    label: 'Template Transform',
    description: 'Render a template with upstream data.',
    category: 'transform',
    origin: 'native',
    provider: 'opencli-core',
    source: 'backend.workflow.node_capability_catalog',
    readiness: 'runnable',
    runtimeBinding: 'workflow.native.template.v1',
    kind: 'agent',
    capability: 'normalize',
    icon: 'Braces',
    inputPorts: [{ name: 'input', type: 'object', required: true }],
    outputPorts: [{ name: 'output', type: 'string', required: true }],
    parameters: [{ name: 'template', label: 'Template', type: 'string', required: true, default: '{{ input }}', options: [] }],
    difyNodeTypes: ['template-transform'],
    missing: [],
  }],
  categories: [{ id: 'transform', label: '转换', count: 1 }],
  summary: { total: 1, byReadiness: { runnable: 1 }, byOrigin: { native: 1 } },
}

const composedNode = {
  ...backendCatalog.nodes[0],
  id: 'primitive.ai.question-classifier',
  label: 'Question Classifier',
  origin: 'composite',
  readiness: 'composed',
  runtimeBinding: null,
  missing: ['llm_classifier_composition'],
}

test('core catalog nodes and common parameters expose Chinese and English copy', async () => {
  const [i18n, businessNames] = await Promise.all([
    importTypeScript('lib/workflow/node-i18n.ts'),
    importTypeScript('lib/workflow/business-node-experience.ts'),
  ])
  const bilingualCatalogIds = [
    'primitive.core.start',
    'primitive.core.end',
    'primitive.core.answer',
    'primitive.ai.llm',
    'primitive.knowledge.retrieve',
    'primitive.knowledge.index',
    'primitive.document.extract',
    'primitive.ai.question-classifier',
    'primitive.core.template-transform',
    'primitive.core.variable-assign',
    'primitive.core.variable-aggregate',
    'primitive.core.list-filter',
    'primitive.core.list-sort',
    'primitive.core.iteration',
    'primitive.core.loop',
    'primitive.ai.parameter-extract',
    'primitive.integration.http-request',
    'primitive.ai.agent',
    'primitive.human.approval',
    'primitive.plugin.trigger',
    'primitive.plugin.datasource',
    'package.compat.dify-workflow',
    'intelligence.data.generate',
    'intelligence.data.filter',
    'intelligence.data.evaluate',
    'intelligence.data.refine',
    'intelligence.flow.merge',
    'intelligence.sink.records',
    'external.tool.capability',
    'package.collection.pipeline',
    'package.intelligence.situation-awareness',
    'package.simulation.swarm-forecast',
    'package.intelligence.native-lifecycle',
    'package.dispatch.fanout',
  ]

  for (const id of bilingualCatalogIds) {
    const fallback = { label: '__fallback__', description: '__fallback__' }
    const chinese = i18n.localizeNodeText(id, fallback, 'zh-CN')
    const english = i18n.localizeNodeText(id, fallback, 'en-US')
    assert.notEqual(chinese.label, fallback.label)
    assert.notEqual(english.label, fallback.label)
    assert.doesNotMatch(`${english.label} ${english.description}`, /[\u3400-\u9fff]/)
  }

  assert.equal(
    i18n.localizeNodeParameterText('timezone', { label: 'timezone' }, 'zh-CN').label,
    '时区',
  )
  assert.equal(
    businessNames.businessNodeName({
      label: 'Multi-site Data Collection',
      kind: 'agent',
      capability: 'normalize',
      params: {
        template: 'opencli-multi-source',
        sources: [{ site: 'eastmoney', args: { market: 'hs-a' } }],
      },
      language: 'en-US',
    }),
    'Collect A-share market data',
  )
  const customNodeData = {
    label: 'A 股多源真实采集',
    description: '自定义业务说明',
    nodeType: 'action',
    category: 'agent',
    canonical: {
      catalogId: 'package.opencli.multi-source-hda',
      kind: 'agent',
      capability: 'normalize',
      params: { template: 'opencli-multi-source' },
    },
  }
  assert.equal(i18n.shouldPreserveNodeAuthoredText(customNodeData), true)
})

test('market scope changes market cards without rewriting filing source arguments', async () => {
  const sourceConfig = await importTypeScript('lib/workflow/source-business-config.ts')
  const sources = [
    { id: 'market', label: '行情', sourceGroup: 'market', site: 'eastmoney', command: 'gridlist', args: { market: 'hs-a' } },
    { id: 'filings', label: '公告', sourceGroup: 'filings', site: 'eastmoney', command: 'announcement', args: { market: 'SHA,SZA,BJA' } },
    { id: 'news', label: '新闻', sourceGroup: 'news', site: 'cls', command: 'telegraph', args: { limit: 30 } },
  ]

  assert.equal(sourceConfig.sourceMarket(sources), 'hs-a')
  const updated = sourceConfig.updateSourceMarket(sources, 'bj-a')
  assert.equal(updated[0].args.market, 'bj-a')
  assert.equal(updated[1].args.market, 'SHA,SZA,BJA')
  assert.deepStrictEqual(updated[2].args, { limit: 30 })
})

test('source identity separates a capability from its configured query', async () => {
  const sourceConfig = await importTypeScript('lib/workflow/source-business-config.ts')
  const base = {
    site: ' Bilibili ',
    command: ' Search ',
    args: { limit: 20, type: 'video' },
    positionalArgs: ['A 股'],
  }

  assert.equal(
    sourceConfig.sourceCapabilityKey(base),
    sourceConfig.sourceCapabilityKey({ ...base, site: 'bilibili', command: 'search' }),
  )
  assert.equal(
    sourceConfig.sourceSlotKey(base),
    sourceConfig.sourceSlotKey({
      ...base,
      site: 'bilibili',
      command: 'search',
      args: { type: 'video', limit: 20 },
    }),
  )
  assert.notEqual(
    sourceConfig.sourceSlotKey(base),
    sourceConfig.sourceSlotKey({ ...base, positionalArgs: ['港股'] }),
  )

  const registered = sourceConfig.openCLISlotFromDataSource({
    id: 'source-1',
    name: 'B 站视频搜索',
    channel_type: 'opencli',
    channel_config: {
      site: 'bilibili',
      command: 'search',
      args: { limit: 20 },
      positional_args: ['A 股'],
    },
    enabled: true,
    tags: ['video'],
    created_at: '',
    updated_at: '',
  })
  assert.deepStrictEqual(registered.positionalArgs, ['A 股'])
})

test('backend node catalog overlays matching nodes without hiding runnable workflow capabilities', async () => {
  const [{ mergeBackendNodeCapabilityCatalog }, nodeCatalog] = await Promise.all([
    importTypeScript('lib/workflow/backend-node-capability-adapter.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
  ])
  const merged = mergeBackendNodeCapabilityCatalog(null, backendCatalog)
  const item = nodeCatalog.getWorkflowNodeCatalog('intelligence', merged)
    .find((candidate) => candidate.id === 'workflow.native.template')

  assert.ok(item)
  assert.equal(item.params.template, '{{ input }}')
  assert.equal(item.runtimeCapability.status, 'runnable')
  assert.equal(nodeCatalog.workflowCatalogIsBackendNode(item), true)
  assert.equal(
    nodeCatalog.getWorkflowNodeCatalog('intelligence', merged)
      .some((candidate) => candidate.id === 'intelligence.source.jin10'),
    true,
  )
  const projectNode = nodeCatalog.createWorkflowNodeFromCatalog(item, 'template-1', { x: 0, y: 0 })
  assert.deepEqual(projectNode.parameterInterface?.fields.map((field) => field.id), ['template'])
  assert.equal(projectNode.parameterInterface?.fields[0]?.binding.nodeId, 'template-1')
})

test('composed nodes remain addable drafts until their runtime dependencies are verified', async () => {
  const [{ mergeBackendNodeCapabilityCatalog }, nodeCatalog] = await Promise.all([
    importTypeScript('lib/workflow/backend-node-capability-adapter.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
  ])
  const catalog = {
    ...backendCatalog,
    nodes: [backendCatalog.nodes[0], composedNode],
    summary: {
      total: 2,
      byReadiness: { runnable: 1, composed: 1 },
      byOrigin: { native: 1, composite: 1 },
    },
  }
  const merged = mergeBackendNodeCapabilityCatalog(null, catalog)
  const projected = merged.catalog.find((item) => item.id === composedNode.id)
  const item = nodeCatalog.getWorkflowNodeCatalog('intelligence', merged)
    .find((candidate) => candidate.id === composedNode.id)

  assert.ok(projected)
  assert.equal(projected.status, 'preview_only')
  assert.equal(projected.backendAvailable, false)
  assert.equal(projected.manifest.nodeCatalog.readiness, 'composed')
  assert.equal(projected.manifest.canvas.locked, false)
  assert.equal(projected.manifest.canvas.runBlocked, true)
  assert.ok(item)
  assert.equal(nodeCatalog.workflowCatalogItemLocked(item), false)
})

test('a runnable label without a verified runtime binding is blocked defensively', async () => {
  const { projectBackendNodeCapability } = await importTypeScript(
    'lib/workflow/backend-node-capability-adapter.ts',
  )
  const projected = projectBackendNodeCapability({
    ...backendCatalog.nodes[0],
    runtimeBinding: null,
  }, backendCatalog)

  assert.equal(projected.status, 'blocked')
  assert.equal(projected.backendAvailable, false)
  assert.deepEqual(projected.missing, ['runtime_binding_unverified'])
  assert.equal(projected.manifest.canvas.locked, false)
  assert.equal(projected.manifest.canvas.runBlocked, true)
})

test('backend primitive nodes preserve typed parameters and runtime port identity', async () => {
  const [{ mergeBackendNodeCapabilityCatalog }, nodeCatalog, parameterInterface] = await Promise.all([
    importTypeScript('lib/workflow/backend-node-capability-adapter.ts'),
    importTypeScript('lib/workflow/node-catalog.ts'),
    importTypeScript('lib/workflow/parameter-interface.ts'),
  ])
  const switchNode = {
    ...backendCatalog.nodes[0],
    id: 'primitive.core.switch',
    label: 'Switch',
    runtimeBinding: 'workflow.native.switch',
    inputPorts: [{ name: 'in', type: 'any', required: true }],
    outputPorts: [{ name: 'out', type: 'any', required: false }],
    parameters: [
      {
        name: 'cases',
        label: 'Cases',
        type: 'array',
        required: true,
        default: [{ id: 'ready', condition: { field: 'ready', operator: 'exists' } }],
        options: [],
      },
      {
        name: 'condition',
        label: 'Condition',
        type: 'object',
        required: true,
        default: { field: 'ready', operator: 'exists' },
        options: [],
      },
    ],
  }
  const merged = mergeBackendNodeCapabilityCatalog(null, {
    ...backendCatalog,
    nodes: [switchNode],
  })
  const item = nodeCatalog.getWorkflowNodeCatalog('intelligence', merged)
    .find((candidate) => candidate.id === switchNode.id)

  assert.ok(item)
  const projectNode = nodeCatalog.createWorkflowNodeFromCatalog(item, 'switch-1', { x: 0, y: 0 })
  assert.equal(projectNode.ui.primitiveId, switchNode.id)
  assert.deepEqual(projectNode.ui.primitivePorts, [
    { id: 'in', direction: 'input', type: 'any', required: true },
    { id: 'out', direction: 'output', type: 'any', required: false },
  ])
  assert.deepEqual(
    projectNode.parameterInterface?.fields.map((field) => [field.id, field.type]),
    [['cases', 'json'], ['condition', 'json']],
  )
  assert.deepEqual(
    parameterInterface.parseJsonParameterValue('[{"id":"ready"}]'),
    { ok: true, value: [{ id: 'ready' }] },
  )
})

test('backend presentation parameters build a reusable public node form', async () => {
  const { createBackendParameterInterface } = await importTypeScript('lib/workflow/parameter-interface.ts')
  const parameterInterface = createBackendParameterInterface('native-1', {
    presentation: {
      parameters: [
        { name: 'maxRounds', label: 'Simulation rounds', type: 'integer', default: 3, minimum: 1 },
        { name: 'requirement', label: 'Requirement', type: 'string', default: 'Explore evidence.' },
        { name: 'platforms', label: 'Platforms', type: 'array', default: ['twitter'] },
      ],
    },
  })

  assert.ok(parameterInterface)
  assert.deepEqual(
    parameterInterface.fields.map((field) => [field.id, field.type, field.value]),
    [
      ['maxRounds', 'number', 3],
      ['requirement', 'textarea', 'Explore evidence.'],
      ['platforms', 'json', ['twitter']],
    ],
  )
  assert.equal(parameterInterface.fields[0].binding.nodeId, 'native-1')
})

test('Plugin Center and Studio consume the same backend catalog projection', async () => {
  const [client, hook, page, palette] = await Promise.all([
    readFrontendSource('lib/plugins/backend-node-capabilities.ts'),
    readFrontendSource('lib/workflow/use-workflow-capabilities.ts'),
    readFrontendSource('app/(app)/plugins/page.tsx'),
    readFrontendSource('components/flow/command-palette.tsx'),
  ])

  assert.match(client, /\/api\/v1\/plugins\/capabilities/)
  assert.match(hook, /mergeBackendNodeCapabilityCatalog/)
  assert.match(page, /nodeCatalog\.summary\.total/)
  assert.match(page, /nodeCatalogCounts\.runnable/)
  assert.match(page, /return node\.runtimeReady/)
  assert.doesNotMatch(page, /node\.readiness === 'runnable' \|\| node\.readiness === 'composed'/)
  assert.match(page, /组合方案可预览，等待依赖就绪/)
  assert.match(page, /providerNodeViews/)
  assert.match(palette, /workflowCatalogItemIsOpenCLIAdapterPreset/)
  assert.match(palette, /return workflowCatalogItemLocked\(item\)/)
  assert.doesNotMatch(
    palette,
    /item\.runtimeCapability && item\.runtimeCapability\.status !== "runnable"/,
  )
  assert.match(palette, /\[capabilities, catalogItems, compatiblePort, inNodeNetwork, language, queryText, workflowProfile\]/)
  assert.match(palette, /插件与后端工具/)
})

test('node picker and inspector share the workflow language setting', async () => {
  const [palette, inspector, node] = await Promise.all([
    readFrontendSource('components/flow/command-palette.tsx'),
    readFrontendSource('components/flow/inspector.tsx'),
    readFrontendSource('components/flow/nodes/workflow-node.tsx'),
  ])

  assert.match(palette, /setLanguage\("language", candidate\)/)
  assert.match(palette, /CATEGORY_LABELS\[category\]\?\.\[language\]/)
  assert.match(palette, /openCLIAdapterNodePresentation\(item, language\)/)
  assert.match(inspector, /localizeNodeParameterText/)
  assert.match(inspector, /localizeNodeParameterText\(\s*field\.binding\.fieldId/)
  assert.match(inspector, /setLanguage\("language", nextLanguage\)/)
  assert.match(inspector, /language=\{language\}/)
  assert.match(inspector, /shouldPreserveNodeAuthoredText/)
  assert.match(inspector, /businessLevel && !prefersCustomLabel/)
  assert.match(node, /businessNodeName\([\s\S]{0,320}language,?\s*\}\)/)
})

test('tool picker exposes access and readiness as separate filter groups', async () => {
  const palette = await readFrontendSource('components/flow/command-palette.tsx')

  assert.match(palette, /accessFilter:\s*"能力类型"/)
  assert.match(palette, /readinessFilter:\s*"就绪状态"/)
  assert.match(palette, /role="group" aria-label=\{copy\.accessFilter\}/)
  assert.match(palette, /role="group" aria-label=\{copy\.readinessFilter\}/)
})

test('OpenCLI preset results use a navigable two-pane catalog without changing node creation', async () => {
  const palette = await readFrontendSource('components/flow/command-palette.tsx')

  assert.match(palette, /data-testid="opencli-preset-layout"/)
  assert.match(palette, /data-testid="opencli-group-navigation"/)
  assert.match(palette, /href=\{`#opencli-group-\$\{group\.id\}`\}/)
  assert.match(palette, /lg:grid-cols-2/)
  assert.match(palette, /onClick=\{\(\) => addOpenCLIAdapter\(item\)\}/)
})

test('Studio materializes every searchable OpenCLI capability preset as a node', async () => {
  const [adapterNodes, adapterCatalog, pluginCatalog, palette, editor] = await Promise.all([
    importTypeScript('lib/workflow/backend-opencli-adapter-nodes.ts'),
    importTypeScript('lib/workflow/opencli-adapter-catalog.ts'),
    importTypeScript('lib/plugins/opencli-adapter-catalog.ts'),
    readFrontendSource('components/flow/command-palette.tsx'),
    readFrontendSource('components/flow/workflow-editor.tsx'),
  ])
  const sourcePreset = {
    id: 'opencli.adapter.example.search',
    label: 'Example · search',
    description: 'Search public records',
    status: 'runnable',
    site: 'example',
    command: 'search',
    access: 'read',
    browser: false,
    strategy: null,
    domain: 'example.com',
    catalogId: 'intelligence.source.opencli-slot',
    kind: 'source',
    capability: 'fetch',
    presetKind: 'source_slot',
    runtimeReadiness: 'source_slot_ready',
    requiredArgs: [],
    args: [],
    adapter: { id: 'opencli-example' },
    params: { site: 'example', command: 'search' },
    manifest: {
      canvas: {
        materialization: 'tool_capability_review_required',
      },
    },
  }
  const writePreset = {
    ...sourcePreset,
    id: 'opencli.adapter.example.publish',
    label: 'Example · publish',
    command: 'publish',
    access: 'write',
    kind: 'action',
    capability: 'store',
    presetKind: 'tool_capability',
    runtimeReadiness: 'tool_capability_review_required',
    status: 'blocked',
  }

  assert.equal(adapterNodes.openCLIAdapterNodeMaterialization(sourcePreset), 'source_slot_ready')
  assert.match(adapterNodes.openCLIAdapterNodePresentation(sourcePreset, 'zh-CN').description, /从 example 读取 search 数据/)
  assert.equal(adapterNodes.openCLIAdapterNodePresentation(sourcePreset, 'en-US').description, 'Search public records')
  assert.match(adapterNodes.openCLIAdapterNodeSearchText(sourcePreset), /read source source-slot 数据读取/)
  assert.match(adapterNodes.openCLIAdapterNodeSearchText({
    ...sourcePreset,
    id: 'opencli.adapter.bilibili.subtitle',
    site: 'bilibili',
    command: 'subtitle',
  }), /b 站 · 字幕提取/)
  const bilibiliRankingSearchText = adapterNodes.openCLIAdapterNodeSearchText({
    ...sourcePreset,
    id: 'opencli.adapter.bilibili.ranking',
    site: 'bilibili',
    command: 'ranking',
  })
  assert.match(bilibiliRankingSearchText, /b 站 · 视频排行榜/)
  assert.match(bilibiliRankingSearchText, /b站/)
  assert.match(adapterNodes.openCLIAdapterNodeSearchText(writePreset), /write tool action 操作工具/)
  const oodaSourceIds = [
    'opencli.adapter.eastmoney.index-quote',
    'opencli.adapter.eastmoney.money-flow',
    'opencli.adapter.tdx.hot-rank',
    'opencli.adapter.xueqiu.stock-social',
    'opencli.adapter.eastmoney.bbsj-summary',
    'opencli.adapter.cninfo.disclosure-pdf',
    'opencli.adapter.sse.announcements',
    'opencli.adapter.cls.telegraph',
    'opencli.adapter.jin10.kuaixun',
    'opencli.adapter.gelonghui.kuaixun',
    'opencli.adapter.xueqiu.news',
    'opencli.adapter.douyin.search',
    'opencli.adapter.bilibili.subtitle',
  ]
  assert.deepStrictEqual(
    adapterNodes.featuredOpenCLIAdapterNodes(oodaSourceIds.map((id) => ({ id }))).map((node) => node.id),
    oodaSourceIds,
  )
  assert.deepStrictEqual(
    adapterNodes.featuredOpenCLIAdapterGroups(oodaSourceIds.map((id) => ({ id })), 'zh-CN')
      .map((group) => group.label),
    ['行情、资金与交易结构', '财报、公告、研报与 PDF', '财经媒体与实时快讯', '社交舆情与全网观察', '视频与多媒体情报'],
  )
  assert.match(palette, /消息与数据来源/)
  assert.doesNotMatch(palette, /国内全网 OODA 数据源/)
  assert.match(palette, /行情、官方披露、财经媒体、社交与视频/)
  const inspector = await readFrontendSource('components/flow/inspector.tsx')
  assert.match(inspector, /addContentSources/)
  assert.match(inspector, /OPENCLI_SITUATION_SOURCES/)
  assert.match(inspector, /restoreSource/)
  assert.match(inspector, /key=\{configurationNodeId\}/)
  assert.match(inspector, /presets\.filter\(\(source\) => !selectedKeys\.has\(sourceSlotKey\(source\)\)\)/)
  assert.match(inspector, /sourceCardLabel/)
  assert.match(inspector, /视频按来源卡片独立配置/)
  assert.match(palette, /loginRequired:\s*"需登录"/)
  assert.doesNotMatch(palette, /featuredOpenCLIAdapterNodes\(matchingOpenCLINodes\)\.filter/)
  assert.equal(adapterCatalog.openCLIAdapterNodeToCatalogItem(sourcePreset).params.opencliAdapterNodeId, sourcePreset.id)
  const writeItem = adapterCatalog.openCLIAdapterNodeToCatalogItem(writePreset)
  assert.equal(writeItem.kind, 'action')
  assert.equal(writeItem.adapter, undefined)
  assert.equal(writeItem.runtimeCapability.status, 'blocked')
  assert.equal(writeItem.params.opencliAdapterNode.id, writePreset.id)
  const configuredSource = adapterNodes.workflowCatalogItemForOpenCLIAdapterNode({
    ...sourcePreset,
    status: 'blocked',
    runtimeReadiness: 'source_slot_requires_params',
    requiredArgs: ['keyword'],
    args: [{ name: 'keyword', required: true, valueRequired: true, positional: false, choices: [] }],
  }, { keyword: 'OpenCLI' })
  assert.equal(configuredSource.params.opencliAdapterNodeId, sourcePreset.id)
  assert.equal(configuredSource.params.args.keyword, 'OpenCLI')
  assert.equal(
    adapterNodes.workflowCatalogItemIsOpenCLIAdapterPreset({
      id: sourcePreset.id,
      runtimeCapability: { source: 'backend.workflow.opencli_adapter_nodes' },
    }),
    true,
  )
  const directoryFixture = [
    ...Array.from({ length: 75 }, (_, index) => ({
      ...sourcePreset,
      id: `opencli.adapter.a-site-${String(index).padStart(2, '0')}.list`,
      site: `a-site-${String(index).padStart(2, '0')}`,
      command: 'list',
      adapter: { id: `opencli-a-site-${String(index).padStart(2, '0')}` },
    })),
    {
      ...sourcePreset,
      id: 'opencli.adapter.discord-app.messages',
      site: 'discord-app',
      command: 'messages',
      adapter: { id: 'opencli-discord-app' },
    },
    {
      ...sourcePreset,
      id: 'opencli.adapter.weixin.search-articles',
      site: 'weixin',
      command: 'search-articles',
      adapter: { id: 'opencli-weixin' },
    },
    {
      ...sourcePreset,
      id: 'opencli.adapter.cninfo-reports.market-reports',
      site: 'cninfo-reports',
      command: 'market-reports',
      adapter: { id: 'opencli-cninfo-reports' },
    },
  ]
  const directory = pluginCatalog.groupOpenCLIAdapterPlugins(directoryFixture)
  assert.equal(directory.length, 78)
  assert.equal(directory.find((site) => site.site === 'discord-app').siteCategory, 'local-app')
  assert.equal(directory.find((site) => site.site === 'weixin').commands[0].id, 'opencli.adapter.weixin.search-articles')
  assert.equal(directory.find((site) => site.site === 'cninfo-reports').siteCategory, 'finance')
  assert.deepStrictEqual(pluginCatalog.openCLIKeyboardCandidates('', null, directoryFixture), [])
  assert.equal(
    pluginCatalog.openCLIKeyboardCandidates(
      '',
      directory.find((site) => site.site === 'weixin'),
      directoryFixture,
    )[0].id,
    'opencli.adapter.weixin.search-articles',
  )
  assert.equal(
    adapterNodes.workflowCatalogItemForOpenCLIAdapterNode(
      directory.find((site) => site.site === 'cninfo-reports').commands[0],
    ).params.opencliAdapterNodeId,
    'opencli.adapter.cninfo-reports.market-reports',
  )
  assert.match(palette, /adapterCatalogResponse\?\.nodes \?\? fallbackOpenCLINodes/)
  assert.match(palette, /includeWrite: true, limit: 5000/)
  assert.match(palette, /!workflowCatalogItemIsOpenCLIAdapterPreset\(item\)/)
  assert.match(palette, /OPENCLI_SEARCH_RESULT_LIMIT = 120/)
  assert.match(palette, /groupOpenCLIAdapterPlugins\(matchingOpenCLINodes\)/)
  assert.match(palette, /openCLIKeyboardCandidates/)
  assert.doesNotMatch(palette, /OPENCLI_RESULT_LIMIT = 60/)
  assert.match(palette, /opencliPresetGroups/)
  assert.match(palette, /item\.site.*openCLIPresetKind\(item\)/s)
  assert.match(palette, /运行前设置/)
  assert.match(palette, /source_slot_requires_params/)
  assert.match(
    palette,
    /materialization === "tool_capability_review_required"\)\s*\{\s*addWorkflowNodeFromCatalog\(openCLIAdapterNodeToCatalogItem\(item\), anchorPosition\(\)\)/s,
  )
  assert.match(palette, /return materialization === "unavailable"/)
  assert.doesNotMatch(palette, /当前不能加入画布|requires review and cannot be added yet/)
  assert.doesNotMatch(palette, /workflowCatalogItemRunnable/)
  assert.match(editor, /mergeWorkflowNodeCatalog\([\s\S]*openCLIAdapterCatalogItems,[\s\S]*\)/)
  assert.doesNotMatch(editor, /openCLIAdapterCatalogItems\.filter/)
})

test('node capabilities live inside Plugin Center and legacy factor links redirect there', async () => {
  const [plugins, legacyPage, navigation] = await Promise.all([
    readFrontendSource('app/(app)/plugins/page.tsx'),
    readFrontendSource('app/(app)/factors/page.tsx'),
    readFrontendSource('lib/navigation.ts'),
  ])

  for (const label of ['源库', '模板', '工具', 'Agent', '触发器', '扩展']) {
    assert.match(plugins, new RegExp(label))
  }
  assert.match(plugins, /activeSubtype: PluginSubtype/)
  assert.match(plugins, /nodeCatalog\?\.nodes\.length/)
  assert.match(plugins, /提供的工作流能力/)
  assert.match(plugins, /activeTab === 'capabilities' && nodeCatalog/)
  assert.doesNotMatch(
    plugins,
    /\.\.\.\(nodeCatalog\?\.nodes\.length \? \[backendNodeCatalogProvider\(nodeCatalog\)\]/,
  )
  assert.match(legacyPage, /redirect\('\/plugins\?tab=capabilities'\)/)
  assert.doesNotMatch(navigation, /\/factors|因子库/)
  assert.doesNotMatch(plugins, /市场动量|收益率|量化因子/)
})
