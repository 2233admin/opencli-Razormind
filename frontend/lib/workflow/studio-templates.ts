import type { ProjectAppType } from '@/lib/api/types'

import { PACKAGED_WORKFLOW_PROJECT, buildPackagedWorkflowProject } from './collection-pipeline'
import { buildGaojixingDoubaoWorkflow } from './gaojixing-doubao-workflow'
import {
  buildAshareDisclosureRiskWorkflow,
  buildAshareMarketWorkflow,
  buildAshareSelfMediaWorkflow,
  buildAshareStockResearchWorkflow,
  buildAshareThemeRadarWorkflow,
  buildOpenCLISituationAwarenessWorkflow,
} from './opencli-business-workflows'
import {
  DEFAULT_OPENCLI_HDA_SOURCES,
  WORKFLOW_NODE_CATALOG,
  createWorkflowNodeFromCatalog,
  opencliAdaptersForSourceSlots,
} from './node-catalog'
import { parseWorkflowProject, workflowNodeSchema, type WorkflowProjectNode } from './schema'

export const STUDIO_TEMPLATES = [
  { id: 'gaojixing-doubao-evidence', variant: 'collection-to-consumption', appType: 'workflow', title: '高吉星豆包证据审计', description: '每次运行必须提供一个新题包；系统冻结独立批次快照、自动计算非品牌题与品牌题数量，并用两个深 HDA 完成逐题证据结构审计、阶段门禁、验证恢复及证据文件存在与引用一致性终审。当前不发起新豆包搜索。', category: '真实业务测试', steps: ['豆包证据批次审计', '批次证据结构终审与交付'] },
  { id: 'ashare-market-intelligence', variant: 'collection-to-consumption', appType: 'workflow', title: 'A 股全市场数据采集', description: '并行采集国内行情、公告财报、宏观监管、财经媒体与社区热度，形成全市场基础数据池。', category: '真实业务测试', steps: ['32 个国内来源', '清洗与准入', '数据工作台'] },
  { id: 'ashare-stock-research', variant: 'collection-to-consumption', appType: 'workflow', title: 'A 股个股全景采集', description: '围绕一只股票采集行情、K 线、资金、财务、公告、研报、持仓和社区讨论；默认 600519，股吧与雪球需登录。', category: '真实业务测试', steps: ['个股参数', '13 类证据', '个股数据集'] },
  { id: 'ashare-theme-radar', variant: 'collection-to-consumption', appType: 'workflow', title: 'A 股题材与资金雷达', description: '汇集概念板块、行业资金、强势股归因、热度排行和实时快讯；通达信热榜需登录。', category: '真实业务测试', steps: ['板块与资金', '题材信号', '主题数据集'] },
  { id: 'ashare-disclosure-risk', variant: 'collection-to-consumption', appType: 'workflow', title: 'A 股公告监管风险', description: '聚合公司公告、业绩预告、股权质押、交易所问询、证监会公告和风险快讯。', category: '真实业务测试', steps: ['披露与问询', '风险清洗', '监管数据集'] },
  { id: 'ashare-self-media-listening', variant: 'collection-to-consumption', appType: 'workflow', title: 'A 股自媒体与社区舆情', description: '采集公众号、股吧、雪球、微博、小红书、B站、抖音与知乎内容，并逐来源暴露登录和健康状态。', category: '真实业务测试', steps: ['跨平台搜索', '舆情清洗', '自媒体数据集'] },
  { id: 'opencli-situation-awareness', variant: 'collection-to-consumption', appType: 'workflow', title: 'OpenCLI 态势感知框架', description: '采集实时事件、新闻和视频字幕，保留证据血缘，并投影到数据工作台与逻辑证据页。', category: '真实业务测试', steps: ['多模态证据采集', '证据准入', '数据与证据工作台'] },
  { id: 'opencli-live-pipeline', variant: 'collection-to-consumption', appType: 'workflow', title: 'OpenCLI 实时采集清洗发送', description: '从 OpenCLI 动态数据源实时提取，完成标准化、去重、Records 入库并发送结果。', category: '完整链路', steps: ['OpenCLI 实时采集', '清洗与 Records', 'Webhook 发送'] },
  { id: 'feishu-douyin-doubao', variant: 'collection-to-consumption', appType: 'workflow', title: '飞书关键词 → 豆包', description: '从飞书多维表格读取待采集词条，逐条原样交给豆包回答，保留回答、数据、链接、分享信息和建议关键词后写入 Records。', category: '真实业务测试', steps: ['飞书词条', '豆包直接回答', '完整结果入库'] },
  { id: 'financial-rss-intelligence', variant: 'collect', appType: 'workflow', title: '财经多源 RSS 情报', description: '并行采集央行政策、监管公告与研究动态，按来源 Group 清洗后写入成果与数据。', category: '采集与监控', steps: ['多源 RSS', 'Group 标准化', 'Records 入库'] },
  { id: 'website-watch', variant: 'collect', appType: 'workflow', title: '网站变化监控', description: '定时读取指定页面，识别内容变化并形成可追溯记录。', category: '采集与监控', steps: ['网页来源', '变化检测', '记录入库'] },
  { id: 'multi-source-intake', variant: 'collect', appType: 'workflow', title: '多来源信息采集', description: '把多个网站与 CLI 数据源汇入统一的采集队列。', category: '采集与监控', steps: ['来源列表', '并行采集', '统一输出'] },
  { id: 'news-brief', variant: 'collect', appType: 'text-generator', title: '每日资讯简报', description: '持续汇总指定主题的新内容，生成每日更新素材。', category: '采集与监控', steps: ['主题检索', '内容抓取', '增量归档'] },
  { id: 'record-cleanup', variant: 'process', appType: 'workflow', title: '结构化清洗管线', description: '标准化、去重并修复来源不同的数据记录。', category: '内容处理', steps: ['原始记录', '清洗去重', '结构化结果'] },
  { id: 'content-summary', variant: 'process', appType: 'text-generator', title: '长内容摘要', description: '拆分长文档，保留关键信息并生成结构化摘要。', category: '内容处理', steps: ['内容切分', '要点提取', '摘要合并'] },
  { id: 'entity-extraction', variant: 'process', appType: 'workflow', title: '实体与关系提取', description: '识别人名、机构、产品及它们之间的关系。', category: '内容处理', steps: ['文本输入', '实体识别', '关系映射'] },
  { id: 'research-agent', variant: 'collection-to-consumption', appType: 'agent', title: '专题研究 Agent', description: '围绕一个问题检索证据、交叉验证并输出研究结论。', category: 'Agent 分析', steps: ['任务拆解', '证据研判', '结论生成'] },
  { id: 'last30days-research', variant: 'collection-to-consumption', appType: 'agent', title: '近 30 天事态感知', description: '从抖音、小红书、B站和 Twitter 采集证据，形成严格时间窗研究简报。', category: 'Agent 分析', steps: ['多平台采集', '30 天窗口研判', '证据简报'] },
  { id: 'situation-to-simulation', variant: 'collection-to-consumption', appType: 'workflow', title: '事态感知到群体推演', description: '把两个独立能力按模板连接：先形成事态报告，再作为群体推演种子。', category: '完整链路', steps: ['多平台采集', '事态感知', '群体智能推演'] },
  { id: 'native-intelligence-lifecycle', variant: 'collection-to-consumption', appType: 'workflow', title: '多平台采集研究项目', description: '从多平台证据采集开始，完成整理、关系构建、推演、访谈和研究报告；默认离线数据可直接跑通。', category: '完整链路', steps: ['证据采集', '整理与研判', '研究报告'] },
  { id: 'signal-triage', variant: 'collection-to-consumption', appType: 'agent', title: '信号研判助手', description: '对新信号进行分级、补充背景并给出处置建议。', category: 'Agent 分析', steps: ['信号接入', 'Agent 研判', '建议输出'] },
  { id: 'quality-review', variant: 'process', appType: 'agent', title: '内容质量审查', description: '按规则和样例检查内容质量，标记需要人工确认的部分。', category: 'Agent 分析', steps: ['规则读取', '质量检查', '人工复核'] },
  { id: 'webhook-delivery', variant: 'deliver', appType: 'workflow', title: 'Webhook 结果分发', description: '把工作流产物转换为稳定负载并投递到业务系统。', category: '分发与集成', steps: ['结果接收', '负载组装', 'Webhook'] },
  { id: 'database-sync', variant: 'deliver', appType: 'workflow', title: '数据表同步', description: '将处理结果按字段映射写入数据库或数据表。', category: '分发与集成', steps: ['字段映射', '批量写入', '状态回执'] },
  { id: 'collection-to-consumption', variant: 'collection-to-consumption', appType: 'workflow', title: '采集到消费完整链路', description: '采集、处理、决策、发送与运行观测的一体化模板。', category: '完整链路', steps: ['采集与解析', '处理与决策', '发送与观测'] },
] as const

export type StudioTemplateId = (typeof STUDIO_TEMPLATES)[number]['id'] | 'blank'
type StudioTemplateVariant = (typeof STUDIO_TEMPLATES)[number]['variant'] | 'blank'
type TemplateIntent = {
  cadence: string
  source: string
  objective: string
  delivery: string
}

const TEMPLATE_INTENTS: Record<(typeof STUDIO_TEMPLATES)[number]['id'], TemplateIntent> = {
  'gaojixing-doubao-evidence': { cadence: 'on-demand', source: 'doubao-evidence-hda', objective: 'collect-audit-certify', delivery: 'certification-report' },
  'ashare-market-intelligence': { cadence: '5m', source: 'opencli-ashare-live', objective: 'collect-normalize-store-financial-evidence', delivery: 'records' },
  'ashare-stock-research': { cadence: '15m', source: 'opencli-ashare-stock', objective: 'collect-single-stock-evidence', delivery: 'records' },
  'ashare-theme-radar': { cadence: '5m', source: 'opencli-ashare-theme', objective: 'collect-theme-and-capital-signals', delivery: 'records' },
  'ashare-disclosure-risk': { cadence: '15m', source: 'opencli-ashare-disclosure', objective: 'collect-disclosure-and-regulatory-risk', delivery: 'records' },
  'ashare-self-media-listening': { cadence: '30m', source: 'opencli-ashare-social', objective: 'collect-cross-platform-market-discussion', delivery: 'records' },
  'opencli-situation-awareness': { cadence: '5m', source: 'opencli-news-video-live', objective: 'collect-normalize-project-evidence', delivery: 'records-and-evidence' },
  'opencli-live-pipeline': { cadence: '5m', source: 'opencli-live-catalog', objective: 'collect-clean-store-deliver', delivery: 'webhook' },
  'feishu-douyin-doubao': { cadence: '15m', source: 'feishu-keyword-table', objective: 'collect-douyin-and-doubao-evidence', delivery: 'records' },
  'financial-rss-intelligence': { cadence: '15m', source: 'financial-rss-groups', objective: 'collect-normalize-store', delivery: 'records' },
  'website-watch': { cadence: 'hourly', source: 'webpage-url', objective: 'detect-change', delivery: 'records' },
  'multi-source-intake': { cadence: '15m', source: 'website-and-opencli-sources', objective: 'collect-and-normalize', delivery: 'records' },
  'news-brief': { cadence: 'daily', source: 'topic-feeds', objective: 'summarize-new-items', delivery: 'email' },
  'record-cleanup': { cadence: 'on-demand', source: 'imported-records', objective: 'normalize-and-dedupe', delivery: 'records' },
  'content-summary': { cadence: 'on-demand', source: 'long-form-content', objective: 'structured-summary', delivery: 'inbox' },
  'entity-extraction': { cadence: 'on-demand', source: 'text-input', objective: 'extract-entities-and-relations', delivery: 'records' },
  'research-agent': { cadence: 'on-demand', source: 'web-research', objective: 'evidence-backed-research', delivery: 'email' },
  'last30days-research': { cadence: 'daily', source: 'opencli-multi-source', objective: 'strict-window-situation-awareness', delivery: 'evidence-report' },
  'situation-to-simulation': { cadence: 'on-demand', source: 'opencli-multi-source', objective: 'situation-to-swarm-simulation', delivery: 'simulation-report' },
  'native-intelligence-lifecycle': { cadence: 'on-demand', source: 'offline-fixture', objective: 'native-intelligence-lifecycle', delivery: 'report-and-qa' },
  'signal-triage': { cadence: 'realtime', source: 'incoming-signals', objective: 'classify-and-recommend', delivery: 'inbox' },
  'quality-review': { cadence: 'on-demand', source: 'content-under-review', objective: 'quality-gate', delivery: 'human-review' },
  'webhook-delivery': { cadence: 'realtime', source: 'workflow-results', objective: 'assemble-payload', delivery: 'webhook' },
  'database-sync': { cadence: '15m', source: 'workflow-results', objective: 'map-and-upsert', delivery: 'database' },
  'collection-to-consumption': { cadence: '15m', source: 'multi-source', objective: 'collect-decide-deliver', delivery: 'multi-channel' },
}

export function studioAppTypeForTemplate(template: StudioTemplateId): ProjectAppType {
  if (template === 'blank') return 'workflow'
  return STUDIO_TEMPLATES.find((item) => item.id === template)?.appType ?? 'workflow'
}

export function studioGraphForTemplate(template: StudioTemplateId, name: string) {
  if (template === 'gaojixing-doubao-evidence') return buildGaojixingDoubaoWorkflow(name)
  if (template === 'native-intelligence-lifecycle') return nativeIntelligenceLifecycleGraph(name)
  if (template === 'last30days-research' || template === 'situation-to-simulation') {
    return researchSimulationGraph(template, name)
  }
  if (template === 'blank') {
    const base = PACKAGED_WORKFLOW_PROJECT
    const startItem = WORKFLOW_NODE_CATALOG.find((item) => item.id === 'intelligence.input.collection-need')
    if (!startItem) throw new Error('空白工作流起点未注册')
    const start = createWorkflowNodeFromCatalog(startItem, 'start', { x: 160, y: 180 })
    return parseWorkflowProject({
      ...base,
      id: `draft-${Date.now()}`,
      name,
      nodes: [{
        ...start,
        params: { ...start.params, text: '', mode: 'demand-draft' },
        ui: { ...start.ui, label: '开始', description: '从这里描述需求或添加第一个节点' },
      }],
      edges: [],
      adapters: [],
    })
  }
  if (template === 'opencli-live-pipeline') return opencliLivePipelineGraph(name)
  if (template === 'feishu-douyin-doubao') return feishuDoubaoGraph(name)
  if (template === 'financial-rss-intelligence') return financialRssIntelligenceGraph(name)
  if (template === 'ashare-market-intelligence') return buildAshareMarketWorkflow(name)
  if (template === 'ashare-stock-research') return buildAshareStockResearchWorkflow(name)
  if (template === 'ashare-theme-radar') return buildAshareThemeRadarWorkflow(name)
  if (template === 'ashare-disclosure-risk') return buildAshareDisclosureRiskWorkflow(name)
  if (template === 'ashare-self-media-listening') return buildAshareSelfMediaWorkflow(name)
  if (template === 'opencli-situation-awareness') return buildOpenCLISituationAwarenessWorkflow(name)

  const variant: StudioTemplateVariant = STUDIO_TEMPLATES.find((item) => item.id === template)?.variant ?? 'collection-to-consumption'
  const base = variant === 'deliver'
    ? buildPackagedWorkflowProject({ includeUnconfiguredDelivery: true })
    : PACKAGED_WORKFLOW_PROJECT
  const intent = TEMPLATE_INTENTS[template]
  const nodes = base.nodes.filter((node) => {
    const x = (node.ui?.position as { x?: number } | undefined)?.x ?? 0
    if (variant === 'collect') return x <= 400
    if (variant === 'process') return x > 400 && x <= 1100
    if (variant === 'deliver') return x > 1100
    return true
  }).map((node) => applyTemplateIntent(node, template, intent, true))
  const ids = new Set(nodes.map((node) => node.id))
  const referencedAdapterIds = collectReferencedAdapterIds(nodes)
  const adapters = base.adapters.filter((adapter) => referencedAdapterIds.has(adapter.id))
  return parseWorkflowProject({ ...base, id: `draft-${Date.now()}`, name, nodes, edges: base.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)), adapters })
}

function feishuDoubaoGraph(name: string) {
  const catalog = (id: string) => {
    const item = WORKFLOW_NODE_CATALOG.find((candidate) => candidate.id === id)
    if (!item) throw new Error(`工作流节点未注册：${id}`)
    return item
  }
  const schedule = createWorkflowNodeFromCatalog(catalog('intelligence.schedule.cron'), 'schedule', { x: 80, y: 260 })
  const feishu = createWorkflowNodeFromCatalog(catalog('intelligence.source.feishu-table'), 'feishu-keywords', { x: 320, y: 260 })
  feishu.params = {
    ...feishu.params,
    table_id: 'tblS6dfkT1dE0SXd',
    keyword_field: '推荐追问',
    number_field: '题号',
    status_field: '',
    eligible_status: '',
    max_rows: 2000,
    source_group: 'feishu-recommended-followups',
  }
  const doubao = createWorkflowNodeFromCatalog(catalog('intelligence.source.doubao-research'), 'doubao-research', { x: 820, y: 260 })
  doubao.params = {
    ...doubao.params,
    question: '{{keyword}}',
    questionFrom: 'keyword',
    executionMode: 'agent',
    agentRuntime: 'bbx',
    site_session: 'persistent',
  }
  const hygiene = createWorkflowNodeFromCatalog(catalog('package.processing.record-hygiene'), 'record-hygiene', { x: 1080, y: 260 })
  const records = createWorkflowNodeFromCatalog(catalog('intelligence.sink.records'), 'records', { x: 1380, y: 260 })
  records.params = {
    ...records.params,
    feishuWriteback: {
      enabled: false,
      spreadsheetToken: '',
      sheetId: '',
      sheetName: '',
      stage: '非品牌题',
      sequenceColumn: '序号',
      idempotencyColumn: '运行ID',
      columns: [
        '序号', '题号', '阶段', '原问句', '完整回答', '关键词数', '关键词（全部）',
        '参考资料数', '参考资料（全部）', '推荐追问数', '推荐追问（全部）', '商品链接（全部）',
        '视频内容（全部）', '高吉星是否出现', '高吉星观察', '正式会话链接', '分享链接',
        '连续截图', '完成时间', '证据状态', '运行ID',
      ],
    },
  }
  return parseWorkflowProject({
    ...PACKAGED_WORKFLOW_PROJECT, id: `draft-${Date.now()}`, name,
    agentPermissions: {
      ...PACKAGED_WORKFLOW_PROJECT.agentPermissions,
      canFetchNetwork: true,
      canMutateExternalSites: false,
    },
    adapters: [
      { id: 'feishu-table-source', type: 'source', provider: 'feishu', mode: 'live', config: { channel: 'feishu_table', channelType: 'feishu_table' } },
      { id: 'doubao-research-source', type: 'source', provider: 'doubao', mode: 'live', config: { channel: 'doubao_research', channelType: 'doubao_research' } },
    ],
    nodes: [schedule, feishu, doubao, hygiene, records],
    edges: [
      { id: 'schedule-feishu', source: schedule.id, target: feishu.id },
      { id: 'feishu-doubao', source: feishu.id, sourcePort: 'out', target: doubao.id, targetPort: 'in' },
      { id: 'doubao-hygiene', source: doubao.id, sourcePort: 'out', target: hygiene.id, targetPort: 'in' },
      { id: 'hygiene-records', source: hygiene.id, sourcePort: 'out', target: records.id, targetPort: 'records' },
    ],
  })
}

function opencliLivePipelineGraph(name: string) {
  const catalog = (id: string) => {
    const item = WORKFLOW_NODE_CATALOG.find((candidate) => candidate.id === id)
    if (!item) throw new Error(`工作流节点未注册：${id}`)
    return item
  }
  const schedule = createWorkflowNodeFromCatalog(catalog('intelligence.schedule.cron'), 'schedule', { x: 80, y: 220 })
  const source: WorkflowProjectNode = {
    id: 'source-opencli-bbc-news',
    kind: 'source',
    capability: 'fetch',
    adapter: 'opencli-bbc',
    params: {
      site: 'bbc',
      command: 'news',
      format: 'json',
      args: {},
      sourceGroup: 'bbc',
      opencliAdapterNodeId: 'opencli.adapter.bbc.news',
    },
    ui: {
      label: 'BBC · news',
      description: '默认实时示例；通过“添加节点”可搜索并加入全部 OpenCLI 读命令',
      icon: 'Globe',
      color: 'var(--chart-4)',
      position: { x: 340, y: 220 },
      catalogId: 'intelligence.source.opencli-slot',
    },
  }
  const hygiene = createWorkflowNodeFromCatalog(catalog('package.processing.record-hygiene'), 'record-hygiene', { x: 700, y: 150 })
  const records = createWorkflowNodeFromCatalog(catalog('intelligence.sink.records'), 'records', { x: 1020, y: 80 })
  const notifyItem = catalog('intelligence.output.webhook')
  const notify = createWorkflowNodeFromCatalog(notifyItem, 'notify-webhook', { x: 1020, y: 270 })
  const adapters = [
    {
      id: 'opencli-bbc',
      type: 'source' as const,
      provider: 'opencli',
      mode: 'live' as const,
      config: { channel: 'opencli' },
    },
    ...(notifyItem.requiredAdapters ?? []),
  ]
  return parseWorkflowProject({
    ...PACKAGED_WORKFLOW_PROJECT,
    id: `draft-${Date.now()}`,
    name,
    adapters,
    agentPermissions: {
      ...PACKAGED_WORKFLOW_PROJECT.agentPermissions,
      canFetchNetwork: true,
      canWriteInbox: true,
      canSendNotifications: true,
    },
    nodes: [schedule, source, hygiene, records, notify],
    edges: [
      { id: 'schedule-source', source: schedule.id, target: source.id },
      { id: 'source-record-hygiene', source: source.id, sourcePort: 'out', target: hygiene.id, targetPort: 'in' },
      { id: 'record-hygiene-records', source: hygiene.id, sourcePort: 'out', target: records.id, targetPort: 'records' },
      { id: 'record-hygiene-notify', source: hygiene.id, sourcePort: 'out', target: notify.id, targetPort: 'in' },
    ],
  })
}

function financialRssIntelligenceGraph(name: string) {
  const catalog = (id: string) => {
    const item = WORKFLOW_NODE_CATALOG.find((candidate) => candidate.id === id)
    if (!item) throw new Error(`工作流节点未注册：${id}`)
    return item
  }
  const schedule = createWorkflowNodeFromCatalog(catalog('intelligence.schedule.cron'), 'schedule-finance-rss', { x: 80, y: 240 })
  schedule.params = { ...schedule.params, interval: '15m', timezone: 'Asia/Shanghai' }

  const rssItem = catalog('intelligence.source.rss')
  const sourceDefinitions = [
    {
      id: 'rss-federal-reserve',
      label: '美联储 · 政策与公告',
      description: 'Federal Reserve 官方新闻与政策公告 RSS',
      feedUrl: 'https://www.federalreserve.gov/feeds/press_all.xml',
      sourceGroup: 'macro-policy',
      site: 'federal-reserve',
    },
    {
      id: 'rss-sec-regulation',
      label: 'SEC · 市场监管',
      description: 'SEC 官方 Press Releases RSS',
      feedUrl: 'https://www.sec.gov/news/pressreleases.rss',
      sourceGroup: 'market-regulation',
      site: 'sec',
    },
    {
      id: 'rss-ecb-research',
      label: 'ECB · 央行研究',
      description: 'ECB 官方新闻、讲话与研究动态 RSS',
      feedUrl: 'https://www.ecb.europa.eu/rss/press.html',
      sourceGroup: 'central-bank-research',
      site: 'ecb',
    },
  ] as const
  const sources = sourceDefinitions.map((definition, index) => {
    const source = createWorkflowNodeFromCatalog(rssItem, definition.id, { x: 280, y: index * 160 })
    const params = {
      ...source.params,
      feedUrl: definition.feedUrl,
      maxEntries: 20,
      sourceGroup: definition.sourceGroup,
      sourceKey: definition.id,
      site: definition.site,
    }
    return {
      ...source,
      params,
      ...(source.parameterInterface
        ? {
            parameterInterface: {
              ...source.parameterInterface,
              fields: source.parameterInterface.fields.map((field) =>
                field.binding.source === 'params' && field.binding.fieldId in params
                  ? { ...field, value: params[field.binding.fieldId as keyof typeof params] }
                  : field,
              ),
            },
          }
        : {}),
      ui: {
        ...source.ui,
        label: definition.label,
        description: definition.description,
      },
    }
  })
  const sourcePoolItem = catalog('intelligence.source.pool')
  const sourcePoolBase = createWorkflowNodeFromCatalog(sourcePoolItem, 'source-pool-finance-rss', { x: 297, y: 245 })
  const sourcePool = {
    ...sourcePoolBase,
    parameterInterface: undefined,
    params: {
      ...sourcePoolBase.params,
      sourceCount: sources.length,
      sourceGroups: sourceDefinitions.map((definition) => definition.sourceGroup),
      fanout: 'parallel',
    },
    topicCollapse: {
      groupId: 'financial-rss-source-pool',
      nodeCount: sources.length,
      mode: 'locked' as const,
      packageInternal: true,
    },
    internals: {
      locked: true,
      nodes: sources,
      edges: [],
    },
    ui: {
      ...sourcePoolBase.ui,
      label: '财经情报数据源池',
      description: '集中管理并并行采集财经 RSS 来源',
      preferCustomLabel: true,
      runtimeContract: {
        schemaVersion: 1,
        bindingId: 'workflow.source-pool.boundary',
        status: 'projection_only',
        inputShape: {
          ports: [{ name: 'in', type: 'trigger' }],
          params: ['sourceCount', 'sourceGroups', 'fanout'],
        },
        outputShape: {
          ports: [{ name: 'items', type: 'items[]' }],
          artifacts: [],
        },
        permissionGate: { required: [] },
        configGate: { required: [] },
        eventShape: { events: [] },
        fixtureCoverage: { cases: [] },
        certification: { realNodeIoContract: true, realWebhookDelivery: false },
        canvas: { exposeResourceInternals: false },
      },
    },
  }
  const normalize = createWorkflowNodeFromCatalog(catalog('intelligence.processing.normalize'), 'normalize-finance-rss', { x: 700, y: 240 })
  const acceptance = createWorkflowNodeFromCatalog(catalog('intelligence.control.record-acceptance'), 'accept-finance-rss', { x: 980, y: 240 })
  const records = createWorkflowNodeFromCatalog(catalog('intelligence.sink.records'), 'records-finance-rss', { x: 1260, y: 240 })

  return parseWorkflowProject({
    ...PACKAGED_WORKFLOW_PROJECT,
    id: `draft-${Date.now()}`,
    name,
    adapters: [...(rssItem.requiredAdapters ?? [])],
    agentPermissions: {
      ...PACKAGED_WORKFLOW_PROJECT.agentPermissions,
      canFetchNetwork: true,
      canWriteInbox: true,
      canSendNotifications: false,
      allowedDomains: ['federalreserve.gov', 'sec.gov', 'ecb.europa.eu'],
    },
    nodes: [schedule, sourcePool, normalize, acceptance, records],
    edges: [
      {
        id: `${schedule.id}-${sourcePool.id}`,
        source: schedule.id,
        target: sourcePool.id,
        sourcePort: 'tick',
        targetPort: 'in',
      },
      {
        id: `${sourcePool.id}-${normalize.id}`,
        source: sourcePool.id,
        target: normalize.id,
        sourcePort: 'items',
        targetPort: 'in',
      },
      {
        id: `${normalize.id}-${acceptance.id}`,
        source: normalize.id,
        target: acceptance.id,
        sourcePort: 'out',
        targetPort: 'candidates',
      },
      {
        id: `${acceptance.id}-${records.id}`,
        source: acceptance.id,
        target: records.id,
        sourcePort: 'records',
        targetPort: 'records',
      },
    ],
  })
}

function applyTemplateIntent(node: WorkflowProjectNode, templateId: Exclude<StudioTemplateId, 'blank'>, intent: TemplateIntent, topLevel = false): WorkflowProjectNode {
  const params = { ...node.params }
  if (topLevel) Object.assign(params, { templateId, cadence: intent.cadence, source: intent.source, objective: intent.objective, delivery: intent.delivery })
  if (node.kind === 'schedule') params.interval = intent.cadence
  if (node.kind === 'source' && node.capability === 'fetch') params.sourceTemplate = intent.source
  if (node.kind === 'notify' && node.capability === 'send') params.deliveryTemplate = intent.delivery
  if (node.kind === 'agent') params.objective = intent.objective

  return {
    ...node,
    params,
    internals: node.internals
      ? { ...node.internals, nodes: node.internals.nodes.map((child) => applyTemplateIntent(workflowNodeSchema.parse(child), templateId, intent)) }
      : undefined,
  }
}

function collectReferencedAdapterIds(nodes: WorkflowProjectNode[]): Set<string> {
  const ids = new Set<string>()
  const visit = (node: WorkflowProjectNode) => {
    if (node.adapter) ids.add(node.adapter)
    node.internals?.nodes.forEach((child) => visit(workflowNodeSchema.parse(child)))
  }
  nodes.forEach(visit)
  return ids
}

function nativeIntelligenceLifecycleGraph(name: string) {
  return parseWorkflowProject({
    id: `draft-${Date.now()}`,
    name,
    profile: 'intelligence',
    version: 1,
    nodes: [
      {
        id: 'native-intelligence-lifecycle',
        kind: 'agent',
        capability: 'normalize',
        params: {
          template: 'native-intelligence-lifecycle',
          runtime: 'iii',
          lockedInternals: true,
          offline: true,
          credentialFree: true,
          sourceMode: 'offline_fixture',
          fixtureId: 'native-intelligence-offline-v1',
        },
        ui: {
          catalogId: 'package.intelligence.native-lifecycle',
          label: '采集研究与报告',
          position: { x: 120, y: 160 },
        },
      },
    ],
    edges: [],
    adapters: [],
    agentPermissions: {
      canFetchNetwork: false,
      canSendNotifications: false,
      canWriteInbox: true,
    },
  })
}

function researchSimulationGraph(
  template: 'last30days-research' | 'situation-to-simulation',
  name: string,
) {
  const collection: WorkflowProjectNode = {
    id: 'opencli-sources',
    kind: 'agent',
    capability: 'normalize',
    params: {
      template: 'opencli-multi-source',
      runtime: 'iii',
      lockedInternals: true,
      sources: DEFAULT_OPENCLI_HDA_SOURCES,
    },
    ui: {
      catalogId: 'package.opencli.multi-source-hda',
      label: '多站点数据采集',
      position: { x: 80, y: 160 },
    },
  }
  const situation: WorkflowProjectNode = {
    id: 'situation-awareness',
    kind: 'agent',
    capability: 'normalize',
    params: {
      template: 'situation-awareness',
      runtime: 'iii',
      lockedInternals: true,
      provider: 'opencli-native',
      query: '人工智能',
      windowDays: 30,
      baselineDays: 30,
      includeUnknownDates: false,
      topK: 10,
    },
    ui: {
      catalogId: 'package.intelligence.situation-awareness',
      label: '近 30 天事态感知',
      position: { x: 520, y: 160 },
    },
  }
  const swarm: WorkflowProjectNode = {
    id: 'swarm-forecast',
    kind: 'agent',
    capability: 'normalize',
    params: {
      template: 'swarm-forecast',
      runtime: 'iii',
      lockedInternals: true,
      provider: 'local',
      requirement: '推演事态在不同群体中的传播、立场变化和可能结果',
      agentCount: 12,
      maxRounds: 8,
      platforms: ['twitter', 'reddit'],
      enableGraphMemoryUpdate: false,
    },
    ui: {
      catalogId: 'package.simulation.swarm-forecast',
      label: '群体智能推演',
      position: { x: 960, y: 160 },
    },
  }
  const includeSwarm = template === 'situation-to-simulation'
  return parseWorkflowProject({
    id: `draft-${Date.now()}`,
    name,
    profile: 'intelligence',
    version: 1,
    nodes: includeSwarm ? [collection, situation, swarm] : [collection, situation],
    edges: [
      {
        id: 'collection-situation',
        source: collection.id,
        target: situation.id,
        sourcePort: 'out',
        targetPort: 'in',
      },
      ...(includeSwarm
        ? [{
            id: 'situation-swarm',
            source: situation.id,
            target: swarm.id,
            sourcePort: 'out',
            targetPort: 'in',
          }]
        : []),
    ],
    adapters: opencliAdaptersForSourceSlots(DEFAULT_OPENCLI_HDA_SOURCES),
    agentPermissions: {
      canFetchNetwork: true,
      canSendNotifications: false,
      canWriteInbox: true,
    },
  })
}

export function studioSlug(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'project'
}
