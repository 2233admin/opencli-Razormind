import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('task list links every work item to its operational detail', async () => {
  const tasks = await read('app/(app)/tasks/page.tsx')
  assert.match(tasks, /title="任务与通知"/)
  assert.match(tasks, /ACTION_CENTER_TABS/)
  assert.match(tasks, /href=\{`\/tasks\/\$\{t\.id\}`\}/)
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
  assert.match(plugins, /useWorkflowCapabilities\(true\)/)
  assert.match(plugins, /PLUGIN_PROVIDERS/)
  assert.match(plugins, /安装插件包/)
  assert.match(plugins, /router\.push\('\/plugins\/opencli'\)/)
  assert.match(plugins, /RssCatalogImportDialog/)
  assert.match(plugins, /DifyPackageImportDialog/)
  assert.match(plugins, /useBackendPluginCatalog\(true\)/)
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

test('studio node selector exposes the complete Dify-compatible component split', async () => {
  const selector = await read('components/flow/command-palette.tsx')
  const nodeCatalog = await read('lib/workflow/node-catalog.ts')

  assert.match(selector, /type SelectorTab = "blocks" \| "sources" \| "tools" \| "start" \| "snippets"/)
  assert.match(selector, /\["blocks", "节点"\]/)
  assert.match(selector, /\["sources", "数据源"\]/)
  assert.match(selector, /\["tools", "工具"\]/)
  assert.match(selector, /\["start", "开始"\]/)
  assert.match(selector, /\["snippets", "片段"\]/)
  assert.match(selector, /item\.category === "source"/)
  assert.match(selector, /item\.category === "package"/)
  assert.match(selector, /item\.category === "trigger"/)
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

test('control center wires kill switch, advisory report, and ODP state into one panel', async () => {
  const [page, navigation, hooks] = await Promise.all([
    read('app/(app)/control/page.tsx'),
    read('lib/navigation.ts'),
    read('lib/api/hooks.ts'),
  ])

  assert.match(page, /useKillSwitch\(/)
  assert.match(page, /useSetKillSwitch\(\)/)
  assert.match(page, /useAdvisoryReport\(/)
  assert.match(page, /useOdpState\(/)
  assert.match(page, /useControlActions\(/)
  assert.match(page, /执行熔断开关/)
  assert.match(page, /咨询报告/)
  assert.match(page, /ODP 数据面状态/)
  assert.match(page, /审计台账/)
  assert.match(page, /handleKillToggle/)
  assert.match(page, /refetchInterval: 30_000/)
  assert.match(page, /refetchInterval: 15_000/)
  assert.match(page, /确认熔断全部自动执行/)
  assert.match(page, /recovery_rate/)
  assert.match(page, /oldest_pending_idle_ms/)
  assert.match(page, /formatMs/)
  assert.match(navigation, /'\/control'/)
  assert.match(navigation, /控制中心/)
  assert.match(hooks, /queryKey: \['kill-switch'\]/)
  assert.match(hooks, /queryKey: \['advisory-report'\]/)
  assert.match(hooks, /queryKey: \['odp-state'\]/)
  assert.match(hooks, /refetchInterval: options\?\.refetchInterval/)
})
