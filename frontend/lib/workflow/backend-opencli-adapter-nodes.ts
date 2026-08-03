import { workflowRequestAuthHeaders } from "./request-auth"
import type { WorkflowNodeCatalogItem } from "./node-catalog"
import type { WorkflowLanguage } from "./node-i18n"
import type { AdapterBinding } from "./schema"

type ApiResponse<T> = {
  success?: boolean
  data?: T
  error?: string
  message?: string
}

export type WorkflowOpenCLIAdapterNodeArg = {
  name: string
  type?: string | null
  required: boolean
  valueRequired: boolean
  positional: boolean
  choices: unknown[]
  default?: unknown
  help?: string | null
}

export type WorkflowOpenCLIAdapterNode = {
  id: string
  label: string
  description: string
  status: "runnable" | "blocked" | "preview_only" | "design_only"
  site: string
  command: string
  access: string
  browser: boolean
  strategy?: string | null
  domain?: string | null
  catalogId: string
  kind: string
  capability: string
  presetKind: "source_slot" | "tool_capability"
  runtimeReadiness:
    | "source_slot_ready"
    | "source_slot_requires_params"
    | "tool_capability_review_required"
  requiredArgs: string[]
  args: WorkflowOpenCLIAdapterNodeArg[]
  adapter: Record<string, unknown>
  params: Record<string, unknown>
  manifest: Record<string, unknown>
}

export type WorkflowOpenCLIAdapterNodeFacets = {
  site: Record<string, number>
  capability: Record<string, number>
  access: Record<string, number>
  browser: Record<string, number>
  status: Record<string, number>
  presetKind: Record<string, number>
  runtimeReadiness: Record<string, number>
}

export type WorkflowOpenCLIAdapterNodesResponse = {
  total: number
  summary: Record<string, unknown>
  facets: WorkflowOpenCLIAdapterNodeFacets
  nodes: WorkflowOpenCLIAdapterNode[]
}

export type WorkflowOpenCLIAdapterMaterialization =
  | "source_slot_ready"
  | "source_slot_requires_params"
  | "tool_capability_review_required"
  | "unavailable"

type FeaturedOpenCLISource = {
  id: string
  label: string
  description: string
  labelEn: string
  descriptionEn: string
}

type FeaturedOpenCLISourceGroup = {
  id: string
  label: string
  labelEn: string
  sourceIds: string[]
}

const FEATURED_OPENCLI_SOURCES: FeaturedOpenCLISource[] = [
  { id: "opencli.adapter.eastmoney.index-quote", label: "东方财富 · 核心指数", description: "上证、深证、创业板、科创 50 与沪深 300 实时行情", labelEn: "Eastmoney · Core Indices", descriptionEn: "Live SSE, SZSE, ChiNext, STAR 50, and CSI 300 quotes" },
  { id: "opencli.adapter.sina.astock", label: "新浪 · 沪深 A 股全市场", description: "沪深 A 股实时行情与市场横截面", labelEn: "Sina · A-share Market", descriptionEn: "Live Shanghai and Shenzhen A-share market snapshot" },
  { id: "opencli.adapter.eastmoney.rank", label: "东方财富 · 市场排行", description: "沪深、北证、创业板与科创板涨跌和成交排行", labelEn: "Eastmoney · Market Ranking", descriptionEn: "Price-change and turnover rankings across mainland boards" },
  { id: "opencli.adapter.eastmoney.sectors", label: "东方财富 · 板块排行", description: "行业、概念与地域板块涨跌和成交排名", labelEn: "Eastmoney · Sector Ranking", descriptionEn: "Industry, concept, and regional sector rankings" },
  { id: "opencli.adapter.eastmoney.money-flow", label: "东方财富 · 主力资金", description: "今日、5 日与 10 日主力资金净流入排行", labelEn: "Eastmoney · Main Fund Flow", descriptionEn: "Main-capital net inflow rankings over 1, 5, and 10 days" },
  { id: "opencli.adapter.eastmoney.northbound", label: "东方财富 · 沪深港通", description: "北向资金分时净流入与通道余额快照", labelEn: "Eastmoney · Stock Connect", descriptionEn: "Northbound intraday net flow and channel balance snapshots" },
  { id: "opencli.adapter.eastmoney.limit-up", label: "东方财富 · 涨跌停", description: "A 股涨停与跌停实时推送", labelEn: "Eastmoney · Limit Moves", descriptionEn: "Live A-share limit-up and limit-down events" },
  { id: "opencli.adapter.eastmoney.longhu", label: "东方财富 · 龙虎榜", description: "交易所披露的龙虎榜、净流入、成交额与上榜原因", labelEn: "Eastmoney · Dragon-Tiger List", descriptionEn: "Exchange-disclosed active seats, flows, turnover, and reasons" },
  { id: "opencli.adapter.eastmoney.rzrq", label: "东方财富 · 融资融券", description: "沪深融资余额、融券余量与历史变化", labelEn: "Eastmoney · Margin Trading", descriptionEn: "Margin financing, securities lending, and historical balances" },
  { id: "opencli.adapter.ths.hot", label: "同花顺 · 强势股与题材", description: "当日强势股、题材归因、换手与主力净量", labelEn: "10jqka · Hot Stocks & Themes", descriptionEn: "Intraday hot stocks, theme attribution, turnover, and fund flow" },
  { id: "opencli.adapter.tdx.hot-rank", label: "通达信 · 热度排行", description: "通达信股票热度、涨跌与题材标签排行", labelEn: "Tongdaxin · Hot Ranking", descriptionEn: "Tongdaxin stock heat, price change, and theme rankings" },
  { id: "opencli.adapter.szse.market-summary", label: "深交所 · 市场总览", description: "深市证券数量、成交额与总市值统计", labelEn: "SZSE · Market Summary", descriptionEn: "Shenzhen listings, turnover, and market-cap statistics" },
  { id: "opencli.adapter.eastmoney.valuation", label: "东方财富 · A 股估值", description: "A 股 PE、PB、PS 与 PCF 估值横截面", labelEn: "Eastmoney · A-share Valuation", descriptionEn: "A-share PE, PB, PS, and PCF valuation snapshot" },
  { id: "opencli.adapter.eastmoney.bbsj-summary", label: "东方财富 · 财务摘要", description: "收入、净利润与 EPS 等上市公司关键财务指标", labelEn: "Eastmoney · Financial Summary", descriptionEn: "Revenue, net profit, EPS, and other company fundamentals" },
  { id: "opencli.adapter.eastmoney.research", label: "东方财富 · 券商研报", description: "A 股个股与行业券商研报列表", labelEn: "Eastmoney · Broker Research", descriptionEn: "A-share company and sector broker research" },
  { id: "opencli.adapter.eastmoney.notices", label: "东方财富 · 全市场公告", description: "沪深京上市公司公告与披露搜索", labelEn: "Eastmoney · Market Disclosures", descriptionEn: "Shanghai, Shenzhen, and Beijing company disclosures" },
  { id: "opencli.adapter.sse.announcements", label: "上交所 · 最新公告", description: "上交所公司公告、停复牌与监管披露", labelEn: "SSE · Latest Announcements", descriptionEn: "SSE company, suspension, and regulatory disclosures" },
  { id: "opencli.adapter.szse.home", label: "深交所 · 市场概况", description: "深交所市值、成交、上市公司数与最新公告", labelEn: "SZSE · Market Overview", descriptionEn: "SZSE market value, turnover, listings, and announcements" },
  { id: "opencli.adapter.bse.announcement", label: "北交所 · 公告与规则", description: "北交所业务通知、审核公告与规则文件", labelEn: "BSE · Announcements & Rules", descriptionEn: "BSE notices, review announcements, and rule documents" },
  { id: "opencli.adapter.eastmoney.macro-data", label: "东方财富 · 中国宏观", description: "CPI、PPI、PMI、GDP、FDI 与 M2 指标", labelEn: "Eastmoney · China Macro", descriptionEn: "China CPI, PPI, PMI, GDP, FDI, and M2 indicators" },
  { id: "opencli.adapter.cls.telegraph", label: "财联社 · 实时电报", description: "覆盖 A 股、港股、期货与宏观的分钟级快讯", labelEn: "CLS · Live Telegraph", descriptionEn: "Minute-level A-share, futures, and macro market news" },
  { id: "opencli.adapter.eastmoney.kuaixun", label: "东方财富 · 7×24 快讯", description: "公司、市场与宏观重要财经快讯", labelEn: "Eastmoney · 24/7 Flash", descriptionEn: "Company, market, and macro financial flash news" },
  { id: "opencli.adapter.sinafinance.news", label: "新浪财经 · 7×24 快讯", description: "财经突发、公司与市场资讯", labelEn: "Sina Finance · 24/7 News", descriptionEn: "Breaking company and market coverage" },
  { id: "opencli.adapter.wallstreetcn.live", label: "华尔街见闻 · 全球联动", description: "全球市场、A 股、商品与宏观实时资讯", labelEn: "WallstreetCN · Global Cross-market", descriptionEn: "Live global, A-share, commodity, and macro updates" },
  { id: "opencli.adapter.cninfo.disclosure", label: "巨潮资讯 · 上市公司披露", description: "沪深京上市公司公告、定期报告与监管披露", labelEn: "CNInfo · Company Disclosures", descriptionEn: "Mainland company announcements, periodic reports, and regulatory disclosures" },
  { id: "opencli.adapter.cninfo.disclosure-pdf", label: "巨潮资讯 · 财报 PDF", description: "下载上市公司公告与财报 PDF 附件", labelEn: "CNInfo · Filing PDFs", descriptionEn: "Download company announcement and financial-report PDF attachments" },
  { id: "opencli.adapter.sina.report", label: "新浪财经 · 公司财报", description: "按证券代码读取公司财务报表", labelEn: "Sina Finance · Company Reports", descriptionEn: "Read company financial statements by security code" },
  { id: "opencli.adapter.jin10.kuaixun", label: "金十数据 · 实时快讯", description: "宏观、外汇、商品与全球市场实时快讯", labelEn: "Jin10 · Live Market News", descriptionEn: "Live macro, FX, commodity, and global-market news" },
  { id: "opencli.adapter.gelonghui.kuaixun", label: "格隆汇 · 实时快讯", description: "A 股、港股与全球市场快讯", labelEn: "Gelonghui · Live News", descriptionEn: "Live A-share, Hong Kong, and global-market news" },
  { id: "opencli.adapter.xueqiu.hot-stocks", label: "雪球 · 人气股票", description: "雪球 A 股人气个股热度榜", labelEn: "Xueqiu · Popular Stocks", descriptionEn: "Xueqiu A-share stock popularity ranking" },
  { id: "opencli.adapter.xueqiu.stock-social", label: "雪球 · 个股社交热度", description: "个股新增关注、讨论与交易热度", labelEn: "Xueqiu · Stock Social Heat", descriptionEn: "Stock follows, discussions, and trading-interest signals" },
  { id: "opencli.adapter.xueqiu.news", label: "雪球 · 资讯与讨论", description: "雪球资讯流与投资者讨论", labelEn: "Xueqiu · News & Discussion", descriptionEn: "Xueqiu news feed and investor discussion" },
  { id: "opencli.adapter.xueqiu.search", label: "雪球 · 股票搜索", description: "按股票代码或名称检索雪球标的", labelEn: "Xueqiu · Stock Search", descriptionEn: "Search Xueqiu instruments by symbol or name" },
  { id: "opencli.adapter.stcn.telegraph", label: "证券时报 · 实时电报", description: "证券时报财经快讯与电报", labelEn: "Securities Times · Telegraph", descriptionEn: "Securities Times financial flash news" },
  { id: "opencli.adapter.yicai.kuaixun", label: "第一财经 · 实时快讯", description: "第一财经市场、公司与宏观快讯", labelEn: "Yicai · Live News", descriptionEn: "Yicai market, company, and macro flash news" },
  { id: "opencli.adapter.cnstock.kuaixun", label: "上海证券报 · 实时快讯", description: "上证报财经与上市公司快讯", labelEn: "Shanghai Securities News · Live News", descriptionEn: "Shanghai Securities News company and market updates" },
  { id: "opencli.adapter.cs.kuaixun", label: "中国证券报 · 实时快讯", description: "中证报财经与资本市场快讯", labelEn: "China Securities Journal · Live News", descriptionEn: "China Securities Journal capital-market updates" },
  { id: "opencli.adapter.douyin.search", label: "抖音 · 视频搜索", description: "按主题搜索抖音视频、作者与互动数据", labelEn: "Douyin · Video Search", descriptionEn: "Search Douyin videos, creators, and engagement by topic" },
  { id: "opencli.adapter.douyin.tophot", label: "抖音 · 实时热点", description: "抖音全站实时热点与传播热度", labelEn: "Douyin · Live Trends", descriptionEn: "Live Douyin trends and propagation signals" },
  { id: "opencli.adapter.bilibili.hot", label: "B 站 · 热门视频", description: "B 站全站热门视频与互动热度", labelEn: "Bilibili · Popular Videos", descriptionEn: "Popular Bilibili videos and engagement signals" },
  { id: "opencli.adapter.bilibili.search", label: "B 站 · 视频搜索", description: "按主题检索 B 站视频和 UP 主", labelEn: "Bilibili · Video Search", descriptionEn: "Search Bilibili videos and creators by topic" },
  { id: "opencli.adapter.bilibili.ranking", label: "B 站 · 视频排行榜", description: "B 站视频分区排行榜与热度指标", labelEn: "Bilibili · Video Rankings", descriptionEn: "Bilibili category rankings and popularity metrics" },
  { id: "opencli.adapter.bilibili.subtitle", label: "B 站 · 字幕提取", description: "按 BV 号提取视频字幕，供内容分析使用", labelEn: "Bilibili · Subtitle Extraction", descriptionEn: "Extract subtitles by BV id for content analysis" },
  { id: "opencli.adapter.bilibili.summary", label: "B 站 · 视频摘要", description: "按 BV 号读取视频摘要与结构化内容", labelEn: "Bilibili · Video Summary", descriptionEn: "Read video summaries and structured content by BV id" },
]

const DOMESTIC_OODA_SOURCE_GROUPS: FeaturedOpenCLISourceGroup[] = [
  {
    id: "market",
    label: "行情、资金与交易结构",
    labelEn: "Market, flow & structure",
    sourceIds: [
      "opencli.adapter.eastmoney.index-quote",
      "opencli.adapter.sina.astock",
      "opencli.adapter.eastmoney.rank",
      "opencli.adapter.eastmoney.sectors",
      "opencli.adapter.eastmoney.money-flow",
      "opencli.adapter.eastmoney.northbound",
      "opencli.adapter.eastmoney.limit-up",
      "opencli.adapter.eastmoney.longhu",
      "opencli.adapter.eastmoney.rzrq",
      "opencli.adapter.eastmoney.valuation",
      "opencli.adapter.eastmoney.hot-rank",
      "opencli.adapter.ths.hot",
      "opencli.adapter.tdx.hot-rank",
      "opencli.adapter.szse.market-summary",
      "opencli.adapter.xueqiu.hot-stocks",
      "opencli.adapter.xueqiu.stock-social",
      "opencli.adapter.xueqiu.industries",
      "opencli.adapter.xueqiu.industry-stocks",
    ],
  },
  {
    id: "filings",
    label: "财报、公告、研报与 PDF",
    labelEn: "Filings, research & PDFs",
    sourceIds: [
      "opencli.adapter.eastmoney.bbsj-summary",
      "opencli.adapter.eastmoney.bbsj",
      "opencli.adapter.eastmoney.research",
      "opencli.adapter.eastmoney.notices",
      "opencli.adapter.eastmoney.announcement",
      "opencli.adapter.eastmoney.yjyg",
      "opencli.adapter.cninfo.disclosure",
      "opencli.adapter.cninfo.disclosure-pdf",
      "opencli.adapter.cninfo.report-schedule",
      "opencli.adapter.cninfo.yjyg",
      "opencli.adapter.cninfo.inquiry",
      "opencli.adapter.sina.report",
      "opencli.adapter.sse.announcements",
      "opencli.adapter.sse.inquiry",
      "opencli.adapter.szse.inquiry",
      "opencli.adapter.bse.announcement",
      "opencli.adapter.bse.inquiry",
      "opencli.adapter.csrc.announcement",
    ],
  },
  {
    id: "macro",
    label: "宏观、政策与监管",
    labelEn: "Macro, policy & regulation",
    sourceIds: [
      "opencli.adapter.eastmoney.macro-data",
      "opencli.adapter.eastmoney.lpr",
      "opencli.adapter.eastmoney.moneysupply",
      "opencli.adapter.eastmoney.shibor",
      "opencli.adapter.eastmoney.fe-calendar",
      "opencli.adapter.pboc.credit",
      "opencli.adapter.pboc.lpr",
      "opencli.adapter.mof.announcement",
      "opencli.adapter.nfra.announcement",
      "opencli.adapter.safe.announcement",
      "opencli.adapter.statsgov.nbs",
      "opencli.adapter.statsgov.monthly-data",
      "opencli.adapter.chinamoney.dr007",
    ],
  },
  {
    id: "news",
    label: "财经媒体与实时快讯",
    labelEn: "Financial media & live news",
    sourceIds: [
      "opencli.adapter.cls.telegraph",
      "opencli.adapter.eastmoney.kuaixun",
      "opencli.adapter.sinafinance.news",
      "opencli.adapter.sinafinance.rolling-news",
      "opencli.adapter.wallstreetcn.live",
      "opencli.adapter.wallstreetcn.articles",
      "opencli.adapter.jin10.kuaixun",
      "opencli.adapter.gelonghui.kuaixun",
      "opencli.adapter.stcn.telegraph",
      "opencli.adapter.yicai.kuaixun",
      "opencli.adapter.cnstock.kuaixun",
      "opencli.adapter.cs.kuaixun",
      "opencli.adapter.36kr.news",
      "opencli.adapter.36kr.hot",
      "opencli.adapter.toutiao.hot",
    ],
  },
  {
    id: "social",
    label: "社交舆情与全网观察",
    labelEn: "Social sentiment & web observation",
    sourceIds: [
      "opencli.adapter.xueqiu.feed",
      "opencli.adapter.xueqiu.hot",
      "opencli.adapter.xueqiu.news",
      "opencli.adapter.xueqiu.search",
      "opencli.adapter.xueqiu.comments",
      "opencli.adapter.eastmoney.guba",
      "opencli.adapter.weibo.hot",
      "opencli.adapter.weibo.search",
      "opencli.adapter.weixin.search-articles",
      "opencli.adapter.xiaohongshu.search",
      "opencli.adapter.xiaohongshu.feed",
    ],
  },
  {
    id: "video",
    label: "视频与多媒体情报",
    labelEn: "Video & multimedia intelligence",
    sourceIds: [
      "opencli.adapter.douyin.search",
      "opencli.adapter.douyin.tophot",
      "opencli.adapter.bilibili.hot",
      "opencli.adapter.bilibili.search",
      "opencli.adapter.bilibili.ranking",
      "opencli.adapter.bilibili.subtitle",
      "opencli.adapter.bilibili.summary",
    ],
  },
]

export type FeaturedOpenCLIAdapterGroup = {
  id: string
  label: string
  nodes: WorkflowOpenCLIAdapterNode[]
}

export function featuredOpenCLIAdapterGroups(
  nodes: WorkflowOpenCLIAdapterNode[],
  language: WorkflowLanguage = "zh-CN",
): FeaturedOpenCLIAdapterGroup[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const seen = new Set<string>()
  return DOMESTIC_OODA_SOURCE_GROUPS.flatMap((group) => {
    const groupNodes = group.sourceIds.flatMap((id) => {
      const node = byId.get(id)
      if (!node || seen.has(node.id)) return []
      seen.add(node.id)
      return [node]
    })
    return groupNodes.length
      ? [{ id: group.id, label: language === "zh-CN" ? group.label : group.labelEn, nodes: groupNodes }]
      : []
  })
}

export function featuredOpenCLIAdapterNodes(
  nodes: WorkflowOpenCLIAdapterNode[],
): WorkflowOpenCLIAdapterNode[] {
  return featuredOpenCLIAdapterGroups(nodes).flatMap((group) => group.nodes)
}

export function openCLIAdapterNodePresentation(
  node: WorkflowOpenCLIAdapterNode,
  language: WorkflowLanguage = "zh-CN",
): { label: string; description: string } {
  const featured = FEATURED_OPENCLI_SOURCES.find((candidate) => candidate.id === node.id)
  if (featured) {
    return language === "zh-CN"
      ? { label: featured.label, description: featured.description }
      : { label: featured.labelEn, description: featured.descriptionEn }
  }
  if (language === "zh-CN") {
    return {
      label: node.label,
      description: node.access === "read"
        ? `从 ${node.site} 读取 ${node.command} 数据`
        : `在 ${node.site} 执行 ${node.command} 操作（需审核）`,
    }
  }
  return {
    label: node.label,
    description: node.description || `Run opencli ${node.site} ${node.command} live`,
  }
}

export function openCLIAdapterNodeMaterialization(
  node: WorkflowOpenCLIAdapterNode,
): WorkflowOpenCLIAdapterMaterialization {
  if (node.status === "preview_only" || node.status === "design_only") return "unavailable"
  if (
    node.runtimeReadiness === "source_slot_ready" ||
    node.runtimeReadiness === "source_slot_requires_params" ||
    node.runtimeReadiness === "tool_capability_review_required"
  ) {
    return node.runtimeReadiness
  }
  const canvas = readRecord(node.manifest.canvas)
  const materialization = canvas?.materialization
  if (
    materialization === "source_slot_ready" ||
    materialization === "source_slot_requires_params" ||
    materialization === "tool_capability_review_required"
  ) {
    return materialization
  }
  return "unavailable"
}

export function openCLIAdapterNodeSearchText(node: WorkflowOpenCLIAdapterNode): string {
  const materialization = openCLIAdapterNodeMaterialization(node)
  const chinese = openCLIAdapterNodePresentation(node, "zh-CN")
  const english = openCLIAdapterNodePresentation(node, "en-US")
  const roleAliases = node.access === "read"
    ? "read source source-slot 数据读取 数据源"
    : "write tool action 操作工具 写入"
  const readinessAliases = materialization === "source_slot_ready"
    ? "runnable ready 可添加 已就绪"
    : materialization === "source_slot_requires_params"
      ? "blocked requires-params 需配置 必填参数"
      : materialization === "tool_capability_review_required"
        ? "blocked review-required 需审核"
        : `${node.status} unavailable 不可用`
  return [
    node.id,
    node.label,
    node.description,
    chinese.label,
    chinese.description,
    chinese.label.replace(/\s+/g, ""),
    english.label,
    english.description,
    node.site,
    node.command,
    node.capability,
    node.kind,
    node.domain,
    node.strategy,
    materialization,
    roleAliases,
    readinessAliases,
    ...node.requiredArgs,
  ].filter(Boolean).join(" ").toLowerCase()
}

export function workflowCatalogItemIsOpenCLIAdapterPreset(
  item: WorkflowNodeCatalogItem,
): boolean {
  return item.runtimeCapability?.source === "backend.workflow.opencli_adapter_nodes" ||
    item.id.startsWith("opencli.adapter.")
}

export async function fetchWorkflowOpenCLIAdapterNodes(
  options: {
    authorization?: string | null
    site?: string
    q?: string
    access?: "read" | "write"
    capability?: "fetch" | "store"
    browser?: boolean
    presetKind?: "source_slot" | "tool_capability"
    runtimeReadiness?:
      | "source_slot_ready"
      | "source_slot_requires_params"
      | "tool_capability_review_required"
    includeWrite?: boolean
    limit?: number
    refresh?: boolean
    signal?: AbortSignal
  } = {},
): Promise<WorkflowOpenCLIAdapterNodesResponse> {
  const params = new URLSearchParams()
  if (options.site) params.set("site", options.site)
  if (options.q) params.set("q", options.q)
  if (options.access) params.set("access", options.access)
  if (options.capability) params.set("capability", options.capability)
  if (typeof options.browser === "boolean") params.set("browser", String(options.browser))
  if (options.presetKind) params.set("presetKind", options.presetKind)
  if (options.runtimeReadiness) params.set("runtimeReadiness", options.runtimeReadiness)
  if (typeof options.includeWrite === "boolean") {
    params.set("includeWrite", String(options.includeWrite))
  }
  if (typeof options.limit === "number") params.set("limit", String(options.limit))
  if (typeof options.refresh === "boolean") params.set("refresh", String(options.refresh))
  const query = params.toString()
  const response = await fetch(`/api/workflow/opencli-adapter-nodes${query ? `?${query}` : ""}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
    signal: options.signal,
  })
  const payload = (await response.json().catch(() => null)) as ApiResponse<WorkflowOpenCLIAdapterNodesResponse> | null
  if (!response.ok || !payload?.data) {
    throw new Error(payload?.message ?? payload?.error ?? `OpenCLI adapter node fetch failed (${response.status})`)
  }
  return payload.data
}

export function workflowCatalogItemForOpenCLIAdapterNode(
  node: WorkflowOpenCLIAdapterNode,
  requiredValues: Record<string, string> = {},
): WorkflowNodeCatalogItem {
  const presentation = openCLIAdapterNodePresentation(node)
  const isWrite = node.access !== "read"
  const args = { ...((node.params.args as Record<string, unknown> | undefined) ?? {}) }
  const positionalArgs = Array.isArray(node.params.positional_args)
    ? [...node.params.positional_args]
    : []
  for (const arg of node.args) {
    const value = requiredValues[arg.name]
    if (!value) continue
    if (arg.positional) positionalArgs.push(value)
    else args[arg.name] = value
  }
  const adapter = node.adapter as AdapterBinding
  return {
    id: node.catalogId,
    idPrefix: `${isWrite ? "action" : "source"}-opencli-${safeIdPart(node.site)}-${safeIdPart(node.command)}`,
    label: presentation.label,
    description: presentation.description,
    category: isWrite ? "output" : "source",
    profile: "intelligence",
    kind: isWrite ? "action" : "source",
    capability: isWrite ? "store" : "fetch",
    icon: isWrite ? "Wrench" : "Globe",
    color: isWrite ? "var(--chart-3)" : "var(--chart-4)",
    adapter: adapter.id,
    requiredAdapters: [adapter],
    params: {
      ...node.params,
      args,
      ...(positionalArgs.length ? { positional_args: positionalArgs } : {}),
      opencliAdapterNodeId: node.id,
      opencliAccess: node.access,
      sourceGroup: node.site,
    },
    proposalState: isWrite ? "accepted" : undefined,
    agentPermissionPatch: isWrite
      ? { canMutateExternalSites: true }
      : undefined,
    keywords: [
      "opencli",
      "realtime",
      "实时",
      "采集",
      node.site,
      node.command,
      node.access,
      node.label,
      node.description,
    ].filter(Boolean),
  }
}

function safeIdPart(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "source"
}

function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  return value as Record<string, unknown>
}
