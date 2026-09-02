import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('task list links every work item to its operational detail', async () => {
  const tasks = await read('app/(app)/tasks/page.tsx')
  assert.match(tasks, /title="任务与通知"/)
  assert.match(tasks, /ACTION_CENTER_TABS/)
  assert.match(tasks, /href=\{(?:`\/tasks\/\$\{t\.id\}`|taskDetailPath\(t\.id, returnTo\))\}/)
})

test('work item detail keeps runs, events, results, and audit in one context', async () => {
  const detail = await read('app/(app)/tasks/[id]/page.tsx')
  assert.match(detail, /listTaskRuns\(id\)/)
  assert.match(detail, /listRunEvents\(id, selectedRun!\.id\)/)
  assert.match(detail, /执行摘要/)
  assert.match(detail, /执行时间线/)
  assert.match(detail, /检查数据成果/)
  assert.match(detail, /查看控制与审计/)
})

test('plugin hub keeps provider management without hiding provider capabilities', async () => {
  const sources = await read('app/(app)/sources/page.tsx')
  const plugins = await read('app/(app)/plugins/page.tsx')
  const opencli = await read('app/(app)/plugins/opencli/page.tsx')
  const rssImport = await read('components/plugins/rss-catalog-import-dialog.tsx')
  const providerCatalog = await read('lib/plugins/provider-catalog.ts')

  assert.match(plugins, /已安装/)
  assert.match(plugins, /探索市场/)
  assert.match(plugins, /PLUGIN_PROVIDER_CATEGORIES/)
  for (const label of ['模型', '工具', '数据源', 'Agent 策略', '触发器', '扩展', '工具包']) {
    assert.match(providerCatalog, new RegExp(label))
  }
  assert.match(plugins, /useWorkflowCapabilities\(true,\s*workspaceId\)/)
  assert.match(plugins, /PLUGIN_PROVIDERS/)
  assert.match(plugins, /安装插件包/)
  assert.match(plugins, /router\.push\([\s\S]*\/plugins\/opencli/)
  assert.match(plugins, /RssCatalogImportDialog/)
  assert.match(plugins, /DifyPackageImportDialog/)
  assert.match(plugins, /useBackendPluginCatalog\(workspaceId\)/)
  assert.match(plugins, /导入 OPML 订阅清单/)
  assert.match(providerCatalog, /category: 'datasource'/)
  assert.match(providerCatalog, /category: 'tool'/)
  assert.match(providerCatalog, /category: 'bundle'/)
  assert.doesNotMatch(plugins, /NodeCapabilityTab|节点能力目录|全部节点|待完善|加入管线|创建自定义节点/)
  assert.doesNotMatch(plugins, /数据源连接器|内置来源包|数据源工作台|公共来源目录|选择并导入/)
  assert.match(opencli, /useOpenCLIAdapterRegistry\(true\)/)
  assert.match(opencli, /OPENCLI_SITE_CATEGORIES/)
  assert.match(opencli, /搜索网站、域名或能力/)
  assert.match(opencli, /plugin\.commands/)
  assert.match(opencli, /refresh\(\)/)
  assert.match(rssImport, /api\.importRssCatalog/)
  assert.match(rssImport, /所有(?:源|条目)默认停用/)
  assert.match(sources, /redirect\('\/records'\)/)
})

test('studio node selector keeps Dify-compatible nodes in the unified node, tool, and start split', async () => {
  const selector = await read('components/flow/command-palette.tsx')
  const nodeCatalog = await read('lib/workflow/node-catalog.ts')

  assert.match(selector, /type PickerTab = "nodes" \| "tools" \| "start"/)
  assert.match(selector, /\{ id: "nodes", label: "节点" \}/)
  assert.match(selector, /\{ id: "tools", label: "工具" \}/)
  assert.match(selector, /\{ id: "start", label: "开始" \}/)
  assert.match(selector, /activeTab === "nodes"/)
  assert.match(selector, /nodeCatalogGroups\.map/)
  assert.match(selector, /activeTab === "tools"/)
  assert.match(selector, /workflowCatalogItemIsOpenCLIAdapterPreset/)
  assert.match(selector, /workflowCatalogPluginProvenance/)
  assert.match(selector, /activeTab === "start"/)
  assert.match(selector, /package: \{ "zh-CN": "业务能力包"/)
  assert.match(selector, /source: \{ "zh-CN": "数据来源"/)
  assert.match(selector, /trigger: \{ "zh-CN": "触发与开始"/)
  for (const id of [
    'workflow.block.agent',
    'workflow.block.llm',
    'workflow.block.knowledge-retrieval',
    'workflow.block.if-else',
    'workflow.block.iteration',
    'workflow.block.loop',
    'workflow.block.code',
    'workflow.block.template-transform',
    'workflow.block.variable-aggregator',
    'workflow.block.document-extractor',
    'workflow.block.parameter-extractor',
    'workflow.block.http-request',
    'workflow.block.list-filter',
  ]) {
    assert.match(nodeCatalog, new RegExp(id.replaceAll('.', '\\.')))
  }
  assert.doesNotMatch(selector, /一级业务节点 · Dify 风格/)
})

test('data explorer shows record shape and pipeline lineage without a source workspace', async () => {
  const [sources, plugins, navigation] = await Promise.all([
    read('app/(app)/sources/page.tsx'),
    read('app/(app)/plugins/page.tsx'),
    read('lib/navigation.ts'),
  ])
  const records = await read('app/(app)/records/page.tsx')

  assert.match(sources, /redirect\('\/records'\)/)
  assert.match(records, /数据预览/)
  assert.match(records, /管线血缘/)
  assert.match(records, /workflow_id/)
  assert.match(records, /workflow_run_id/)
  assert.match(records, /source_id/)
  assert.doesNotMatch(records, /useSources|selectedSourceId|数据集|按采集入口切换/)
  assert.doesNotMatch(plugins, /数据源连接器|内置来源包|数据源工作台/)
  assert.doesNotMatch(navigation, /href: '\/sources', label: '数据源'/)
})

test('OpenCLI is one provider with a full live website adapter directory', async () => {
  const [plugins, opencli, catalog, registryHook, adapterClient] = await Promise.all([
    read('app/(app)/plugins/page.tsx'),
    read('app/(app)/plugins/opencli/page.tsx'),
    read('lib/plugins/opencli-adapter-catalog.ts'),
    read('lib/plugins/use-opencli-adapter-registry.ts'),
    read('lib/workflow/backend-opencli-adapter-nodes.ts'),
  ])

  assert.match(plugins, /useOpenCLIAdapterRegistry\(true\)/)
  assert.match(plugins, /summary\.adapterCount/)
  assert.match(plugins, /OpenCLI/)
  assert.match(opencli, /网站适配/)
  assert.match(opencli, /搜索网站、域名或能力/)
  assert.match(opencli, /OPENCLI_SITE_CATEGORIES/)
  assert.match(opencli, /selectedPlugin/)
  assert.match(catalog, /new Map<string, WorkflowOpenCLIAdapterNode\[\]>/)
  assert.match(catalog, /groupOpenCLIAdapterPlugins/)
  assert.match(catalog, /OPENCLI_SITE_CATEGORIES/)
  assert.match(catalog, /classifyOpenCLISiteCategory/)
  assert.match(catalog, /SITE_CATEGORY_MEMBERS/)
  assert.match(catalog, /SITE_PRESENTATION_OVERRIDES/)
  assert.match(catalog, /百度财经/)
  assert.match(catalog, /百度学术/)
  assert.match(catalog, /siteFeatures/)
  assert.match(catalog, /siteCategory:/)
  assert.match(catalog, /parameterReadyCount: commands\.filter/)
  assert.match(registryHook, /includeWrite: true/)
  assert.match(registryHook, /refresh: forceRefresh/)
  assert.match(registryHook, /signal/)
  assert.match(registryHook, /load\(\{ signal: controller\.signal \}\)/)
  assert.match(adapterClient, /params\.set\("refresh"/)
  assert.match(adapterClient, /signal: options\.signal/)
})
