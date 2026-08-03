"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useReactFlow } from "@xyflow/react"
import {
  ArrowLeft,
  Boxes,
  ChevronRight,
  FileUp,
  Globe,
  LayoutGrid,
  Loader2,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Wrench,
} from "lucide-react"

import { NODE_PALETTE } from "@/lib/flow/palette"
import { portTypesCompatible } from "@/lib/flow/graph"
import { getIcon } from "@/lib/flow/icons"
import { generateWorkflowLocally } from "@/lib/flow/local-generate"
import { useSettingsStore } from "@/lib/flow/settings-store"
import { useFlowStore } from "@/lib/flow/store"
import type { PaletteItem } from "@/lib/flow/types"
import {
  featuredOpenCLIAdapterGroups,
  fetchWorkflowOpenCLIAdapterNodes,
  openCLIAdapterNodeMaterialization,
  openCLIAdapterNodePresentation,
  openCLIAdapterNodeSearchText,
  workflowCatalogItemForOpenCLIAdapterNode,
  workflowCatalogItemIsOpenCLIAdapterPreset,
  type WorkflowOpenCLIAdapterNode,
  type WorkflowOpenCLIAdapterNodesResponse,
} from "@/lib/workflow/backend-opencli-adapter-nodes"
import { primitiveRuntimeCapability, runtimeStatusLabel, runtimeStatusTone } from "@/lib/workflow/capabilities"
import {
  getWorkflowNodeCatalog,
  workflowCatalogItemLocked,
  workflowCatalogPluginProvenance,
  type WorkflowNodeCatalogItem,
} from "@/lib/workflow/node-catalog"
import { workflowNodeDepthFromNetworkStack, workflowNodeLayerAtDepth } from "@/lib/workflow/node-hierarchy"
import { localizeNodeText, type WorkflowLanguage } from "@/lib/workflow/node-i18n"
import { groupPrimitivesForNodeMenu } from "@/lib/workflow/node-menu"
import { getNodeContractByCatalogId } from "@/lib/workflow/node-contracts"
import {
  getDifyCommonWorkflowPrimitives,
  getWorkflowPrimitives,
  type WorkflowPrimitive,
} from "@/lib/workflow/node-primitives"
import {
  groupOpenCLIAdapterPlugins,
  openCLIKeyboardCandidates,
  OPENCLI_SITE_CATEGORIES,
} from "@/lib/plugins/opencli-adapter-catalog"
import { openCLIAdapterNodeToCatalogItem } from "@/lib/workflow/opencli-adapter-catalog"
import { useWorkflowCapabilities } from "@/lib/workflow/use-workflow-capabilities"
import { cn } from "@/lib/utils"

const AI_EXAMPLES: Record<WorkflowLanguage, string[]> = {
  "zh-CN": [
    "用户注册后发送欢迎邮件，24 小时后如果未激活则再次提醒",
    "监听订单创建事件，校验库存，扣减库存并通知仓库发货",
    "收到客服工单，判断优先级，高优先级转人工，其余自动回复",
  ],
  "en-US": [
    "Send a welcome email after signup, then remind inactive users after 24 hours",
    "Validate inventory when an order is created, reserve stock, and notify the warehouse",
    "Classify support tickets, route urgent cases to a person, and answer the rest automatically",
  ],
}

const CATEGORY_LABELS: Record<string, Record<WorkflowLanguage, string>> = {
  package: { "zh-CN": "业务能力包", "en-US": "Business packages" },
  trigger: { "zh-CN": "触发与开始", "en-US": "Triggers & start" },
  source: { "zh-CN": "数据来源", "en-US": "Data sources" },
  processing: { "zh-CN": "数据处理", "en-US": "Data processing" },
  transform: { "zh-CN": "处理与转换", "en-US": "Transformations" },
  flow: { "zh-CN": "流程控制", "en-US": "Flow control" },
  decision: { "zh-CN": "逻辑与判断", "en-US": "Logic & decisions" },
  control: { "zh-CN": "治理与门禁", "en-US": "Governance & gates" },
  action: { "zh-CN": "动作", "en-US": "Actions" },
  output: { "zh-CN": "输出", "en-US": "Outputs" },
  sink: { "zh-CN": "数据写入", "en-US": "Data sinks" },
  annotation: { "zh-CN": "注释与辅助", "en-US": "Notes & helpers" },
  shape: { "zh-CN": "流程图形", "en-US": "Flowchart shapes" },
}

const AUXILIARY_TEXT: Record<string, Record<WorkflowLanguage, { label: string; description: string }>> = {
  "分组容器": {
    "zh-CN": { label: "分组容器", description: "将多个节点组织在一起" },
    "en-US": { label: "Group", description: "Organize several nodes as one visual group" },
  },
  "备注": {
    "zh-CN": { label: "备注", description: "添加说明文字" },
    "en-US": { label: "Note", description: "Add explanatory text to the canvas" },
  },
  "矩形": {
    "zh-CN": { label: "矩形", description: "流程步骤" },
    "en-US": { label: "Rectangle", description: "Process step" },
  },
  "圆形": {
    "zh-CN": { label: "圆形", description: "起止节点" },
    "en-US": { label: "Circle", description: "Start or end node" },
  },
  "菱形": {
    "zh-CN": { label: "菱形", description: "判定 / 决策" },
    "en-US": { label: "Diamond", description: "Decision" },
  },
  "六边形": {
    "zh-CN": { label: "六边形", description: "准备 / 预处理" },
    "en-US": { label: "Hexagon", description: "Preparation or preprocessing" },
  },
  "平行四边形": {
    "zh-CN": { label: "平行四边形", description: "输入 / 输出" },
    "en-US": { label: "Parallelogram", description: "Input or output" },
  },
  "圆柱": {
    "zh-CN": { label: "圆柱", description: "数据存储" },
    "en-US": { label: "Cylinder", description: "Data store" },
  },
}

type PickerTab = "nodes" | "tools" | "start"
type ToolFilter = "all" | "opencli" | "plugin"
type OpenCLIAccessFilter = "all" | "read" | "write"
type OpenCLIReadinessFilter = "all" | "runnable" | "blocked"

const OPENCLI_SEARCH_RESULT_LIMIT = 120

const TAB_META: { id: PickerTab; label: string }[] = [
  { id: "nodes", label: "节点" },
  { id: "tools", label: "工具" },
  { id: "start", label: "开始" },
]

const PALETTE_COPY = {
  "zh-CN": {
    tabs: { nodes: "节点", tools: "工具", start: "开始" },
    pickerType: "节点选择类型",
    switchLanguage: "切换节点语言",
    search: "搜索节点选择器",
    searchNodes: "搜索节点名称、业务能力或英文关键词",
    searchTools: "搜索站点、命令、读取/写入或就绪状态",
    all: "全部",
    opencliPresets: "OpenCLI 预设",
    pluginCapabilities: "插件能力",
    accessFilter: "能力类型",
    allRoles: "全部角色",
    dataRead: "数据读取",
    operationTool: "操作工具",
    readinessFilter: "就绪状态",
    allStates: "全部状态",
    runnable: "可运行",
    needsSetup: "运行前设置",
    currentLayerOnly: "当前只展示本层可用执行节点",
    annotations: "注释与辅助",
    noNodes: "没有匹配的节点",
    opencliCapabilities: "OpenCLI 能力预设",
    loadingOpencli: "正在读取 OpenCLI 能力目录",
    featuredSites: "消息与数据来源",
    catalogIndex: "能力导航",
    featuredDescription: "按行情、官方披露、财经媒体、社交与视频浏览可用来源",
    moreOpencliPresets: "更多站点能力",
    allOpencliSites: "全部 OpenCLI 站点",
    siteDirectoryDescription: "按站点浏览完整能力目录，选择后查看该站全部命令",
    backToSiteDirectory: "返回站点目录",
    siteCommands: "站点命令",
    pluginTools: "插件与后端工具",
    noTools: "没有匹配的工具",
    createImport: "创建与导入",
    aiWorkflow: "AI 生成工作流",
    aiWorkflowDescription: "用自然语言生成可编辑的工作流草稿",
    importApp: "导入应用",
    importAppDescription: "支持 Dify、n8n、JSON、YAML 与 Mermaid",
    startFromNode: "从节点开始",
    startFromNodeDescription: "进入节点目录，手动搭建业务流程",
    canvasActions: "画布操作",
    autoLayout: "自动整理画布",
    autoLayoutDescription: "按纵向业务流重新排布当前节点",
    saveDraft: "保存当前草稿",
    saveDraftDescription: "将当前工作流保存到本地状态",
    restoreExample: "恢复示例工作流",
    restoreExampleDescription: "清空当前改动并恢复默认示例",
    morePlugins: "在插件中心查找更多",
    chooseStart: "选择一种开始方式",
    enterToAdd: "输入搜索 · Enter 添加",
    close: "Esc 关闭",
    configureSource: "配置 OpenCLI 数据源",
    backToTools: "返回工具列表",
    requiredBeforeAdd: "配置必填参数后加入画布",
    input: "输入",
    cancel: "取消",
    addSource: "添加数据源",
    backToStart: "返回开始",
    describeWorkflow: "描述你想要的流程…",
    generate: "生成",
    statusReady: "可运行",
    statusReview: "运行前审核",
    loginRequired: "需登录",
    addedReviewPending: "已加入草稿；完成审核后即可运行",
    parameters: "参数",
    shown: "当前显示",
    refineSearch: "继续输入站点、命令或就绪状态可定位其余预设。",
  },
  "en-US": {
    tabs: { nodes: "Nodes", tools: "Tools", start: "Start" },
    pickerType: "Node picker type",
    switchLanguage: "Switch node language",
    search: "Search node picker",
    searchNodes: "Search node names, business capabilities, or Chinese keywords",
    searchTools: "Search sites, commands, read/write, or readiness",
    all: "All",
    opencliPresets: "OpenCLI presets",
    pluginCapabilities: "Plugin capabilities",
    accessFilter: "Capability type",
    allRoles: "All roles",
    dataRead: "Data read",
    operationTool: "Operation tool",
    readinessFilter: "Readiness",
    allStates: "All states",
    runnable: "Runnable",
    needsSetup: "Setup before run",
    currentLayerOnly: "Only executable nodes available at this layer are shown",
    annotations: "Notes & helpers",
    noNodes: "No matching nodes",
    opencliCapabilities: "OpenCLI capability presets",
    loadingOpencli: "Loading the OpenCLI capability catalog",
    featuredSites: "Information & data sources",
    catalogIndex: "Capability navigation",
    featuredDescription: "Browse market, official disclosure, media, social, and video sources",
    moreOpencliPresets: "More site capabilities",
    allOpencliSites: "All OpenCLI sites",
    siteDirectoryDescription: "Browse the complete catalog by site, then inspect all commands for one site",
    backToSiteDirectory: "Back to site directory",
    siteCommands: "Site commands",
    pluginTools: "Plugin & backend tools",
    noTools: "No matching tools",
    createImport: "Create & import",
    aiWorkflow: "Generate workflow with AI",
    aiWorkflowDescription: "Create an editable workflow draft from natural language",
    importApp: "Import app",
    importAppDescription: "Supports Dify, n8n, JSON, YAML, and Mermaid",
    startFromNode: "Start from nodes",
    startFromNodeDescription: "Open the node catalog and build a workflow manually",
    canvasActions: "Canvas actions",
    autoLayout: "Auto-layout canvas",
    autoLayoutDescription: "Rearrange nodes as a top-to-bottom business flow",
    saveDraft: "Save current draft",
    saveDraftDescription: "Save the current workflow to local state",
    restoreExample: "Restore example workflow",
    restoreExampleDescription: "Discard current edits and restore the default example",
    morePlugins: "Find more in Plugin Center",
    chooseStart: "Choose how to start",
    enterToAdd: "Type to search · Enter to add",
    close: "Esc to close",
    configureSource: "Configure OpenCLI data source",
    backToTools: "Back to tools",
    requiredBeforeAdd: "Complete required parameters before adding to the canvas",
    input: "Enter",
    cancel: "Cancel",
    addSource: "Add data source",
    backToStart: "Back to start",
    describeWorkflow: "Describe the workflow you want…",
    generate: "Generate",
    statusReady: "Runnable",
    statusReview: "Review before run",
    loginRequired: "Login required",
    addedReviewPending: "Added to the draft; complete review before running",
    parameters: "parameters",
    shown: "Showing",
    refineSearch: "Keep typing a site, command, or readiness state to find the remaining presets.",
  },
} as const satisfies Record<WorkflowLanguage, Record<string, unknown>>

function PickerRow({
  icon: Icon,
  label,
  description,
  trailing,
  onClick,
  disabled,
}: {
  icon: ReturnType<typeof getIcon>
  label: string
  description?: string
  trailing?: React.ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex min-h-14 w-full items-center gap-3 rounded-md px-3 text-left outline-none transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-55"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-border bg-card text-primary">
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{label}</span>
        {description ? <span className="block truncate text-[11px] text-muted-foreground">{description}</span> : null}
      </span>
      {trailing ?? <ChevronRight className="size-4 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5" />}
    </button>
  )
}

function SectionLabel({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1 pt-4 text-xs font-medium text-muted-foreground">
      <span>{children}</span>
      {typeof count === "number" ? <span className="font-mono text-[10px]">{count}</span> : null}
    </div>
  )
}

function catalogItemUnavailable(item: WorkflowNodeCatalogItem): boolean {
  return workflowCatalogItemLocked(item)
}

function openCLIStatusLabel(item: WorkflowOpenCLIAdapterNode, language: WorkflowLanguage): string {
  const copy = PALETTE_COPY[language]
  const materialization = openCLIAdapterNodeMaterialization(item)
  if (materialization !== "unavailable") {
    const setup = [
      item.strategy === "cookie" ? copy.loginRequired : "",
      materialization === "source_slot_requires_params" ? `${item.requiredArgs.length} ${copy.parameters}` : "",
      materialization === "tool_capability_review_required" ? copy.statusReview : "",
    ].filter(Boolean)
    if (setup.length) return setup.join(" · ")
  }
  if (materialization === "source_slot_ready") return copy.statusReady
  return runtimeStatusLabel(item.status)
}

function openCLIPresetKind(item: WorkflowOpenCLIAdapterNode): "source_slot" | "tool_capability" {
  return item.presetKind ?? (item.access === "read" ? "source_slot" : "tool_capability")
}

function openCLIPresetUnavailable(item: WorkflowOpenCLIAdapterNode): boolean {
  const materialization = openCLIAdapterNodeMaterialization(item)
  return materialization === "unavailable"
}

function OpenCLIPickerRow({
  item,
  language,
  onClick,
}: {
  item: WorkflowOpenCLIAdapterNode
  language: WorkflowLanguage
  onClick: () => void
}) {
  const Icon = openCLIPresetKind(item) === "source_slot" ? Globe : Wrench
  const presentation = openCLIAdapterNodePresentation(item, language)
  const unavailable = openCLIPresetUnavailable(item)
  const needsSetup = item.strategy === "cookie" ||
    openCLIAdapterNodeMaterialization(item) !== "source_slot_ready"

  return (
    <button
      type="button"
      className="group flex min-h-20 w-full items-start gap-3 rounded-md border border-ops-line bg-ops-panel p-3 text-left outline-none transition-colors hover:border-ops-line-strong hover:bg-ops-raised focus-visible:ring-2 focus-visible:ring-primary-400/50 disabled:cursor-not-allowed disabled:opacity-55"
      disabled={unavailable}
      onClick={onClick}
    >
      <span className="grid size-8 shrink-0 place-items-center rounded-sm border border-ops-line bg-ops-raised text-primary-400">
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-3xs uppercase tracking-wide text-zinc-500">
          {item.site} / {item.command}
        </span>
        <span className="mt-1 block truncate text-xs font-medium text-zinc-100">
          {presentation.label}
        </span>
        <span className="mt-0.5 line-clamp-2 text-2xs leading-4 text-zinc-500">
          {presentation.description}
        </span>
      </span>
      <span
        className={cn(
          "mt-0.5 shrink-0 rounded-xs border px-1.5 py-0.5 font-mono text-3xs",
          needsSetup
            ? "border-warning/40 text-warning"
            : "border-success/40 text-success",
        )}
      >
        {openCLIStatusLabel(item, language)}
      </span>
    </button>
  )
}

export type CompatibleConnectionPort = {
  handleType: "source" | "target"
  type: string
}

function catalogAcceptsConnection(
  item: WorkflowNodeCatalogItem,
  compatiblePort: CompatibleConnectionPort | undefined,
) {
  if (!compatiblePort) return true
  const runtimePorts = compatiblePort.handleType === "source"
    ? item.runtimeContract?.inputShape.ports
    : item.runtimeContract?.outputShape.ports
  const direction = compatiblePort.handleType === "source" ? "input" : "output"
  const staticPorts = getNodeContractByCatalogId(item.id)?.ports.filter((port) => port.direction === direction)
  const ports = runtimePorts?.length ? runtimePorts : staticPorts
  const originType = compatiblePort.type.trim().toLowerCase()
  return Boolean(ports?.some((port) =>
    (originType === "unknown" || port.type.trim().toLowerCase() !== "unknown") &&
    portTypesCompatible(compatiblePort.type, port.type),
  ))
}

function primitiveAcceptsConnection(
  item: WorkflowPrimitive,
  compatiblePort: CompatibleConnectionPort | undefined,
) {
  if (!compatiblePort) return true
  const direction = compatiblePort.handleType === "source" ? "input" : "output"
  return item.ports.some(
    (port) => port.direction === direction && portTypesCompatible(compatiblePort.type, port.type),
  )
}

export function CommandPalette({
  adapterCatalogError,
  adapterCatalogLoading,
  adapterCatalogResponse,
  catalogItems,
  open,
  onClose,
  onMessage,
  onNodeCreated,
  getAnchor,
  initialTab = "nodes",
  onImportApp,
  compatiblePort,
}: {
  adapterCatalogError?: string | null
  adapterCatalogLoading?: boolean
  adapterCatalogResponse?: WorkflowOpenCLIAdapterNodesResponse | null
  catalogItems?: WorkflowNodeCatalogItem[]
  open: boolean
  onClose: () => void
  onMessage?: (msg: string) => void
  onNodeCreated?: () => void
  getAnchor?: () => { x: number; y: number }
  initialTab?: PickerTab
  onImportApp?: () => void
  compatiblePort?: CompatibleConnectionPort
}) {
  const [activeTab, setActiveTab] = useState<PickerTab>(initialTab)
  const [toolFilter, setToolFilter] = useState<ToolFilter>("all")
  const [opencliAccessFilter, setOpencliAccessFilter] = useState<OpenCLIAccessFilter>("all")
  const [opencliReadinessFilter, setOpencliReadinessFilter] = useState<OpenCLIReadinessFilter>("all")
  const [query, setQuery] = useState("")
  const [aiMode, setAiMode] = useState(false)
  const [aiPrompt, setAiPrompt] = useState("")
  const [loading, setLoading] = useState(false)
  const [opencliLoading, setOpencliLoading] = useState(false)
  const [fallbackOpenCLINodes, setFallbackOpenCLINodes] = useState<WorkflowOpenCLIAdapterNode[]>([])
  const [fallbackOpenCLIError, setFallbackOpenCLIError] = useState<string | null>(null)
  const [selectedOpenCLISiteId, setSelectedOpenCLISiteId] = useState<string | null>(null)
  const [selectedOpenCLI, setSelectedOpenCLI] = useState<WorkflowOpenCLIAdapterNode | null>(null)
  const [requiredValues, setRequiredValues] = useState<Record<string, string>>({})
  const inputRef = useRef<HTMLInputElement>(null)
  const aiRef = useRef<HTMLTextAreaElement>(null)

  const { screenToFlowPosition } = useReactFlow()
  const addNodeFromPalette = useFlowStore((state) => state.addNodeFromPalette)
  const addPrimitiveNode = useFlowStore((state) => state.addPrimitiveNode)
  const addWorkflowNodeFromCatalog = useFlowStore((state) => state.addWorkflowNodeFromCatalog)
  const applyGeneratedWorkflow = useFlowStore((state) => state.applyGeneratedWorkflow)
  const autoLayout = useFlowStore((state) => state.autoLayout)
  const save = useFlowStore((state) => state.save)
  const reset = useFlowStore((state) => state.reset)
  const workflowProfile = useFlowStore((state) => state.workflowProject.profile)
  const networkStackLength = useFlowStore((state) => state.networkStack.length)
  const inNodeNetwork = networkStackLength > 0
  const nodeDepth = workflowNodeDepthFromNetworkStack(networkStackLength)
  const nodeLayer = workflowNodeLayerAtDepth(nodeDepth)
  const language = useSettingsStore((state) => state.language)
  const setLanguage = useSettingsStore((state) => state.set)
  const copy = PALETTE_COPY[language]
  const { capabilities } = useWorkflowCapabilities(open)

  useEffect(() => {
    if (!open) return
    setActiveTab(initialTab)
    setToolFilter("all")
    setOpencliAccessFilter("all")
    setOpencliReadinessFilter("all")
    setQuery("")
    setAiMode(false)
    setAiPrompt("")
    setSelectedOpenCLISiteId(null)
    setSelectedOpenCLI(null)
    setRequiredValues({})
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [initialTab, open])

  useEffect(() => {
    if (!open || adapterCatalogResponse !== undefined || fallbackOpenCLINodes.length) return
    const controller = new AbortController()
    setOpencliLoading(true)
    setFallbackOpenCLIError(null)
    void fetchWorkflowOpenCLIAdapterNodes({ includeWrite: true, limit: 5000, signal: controller.signal })
      .then((result) => setFallbackOpenCLINodes(result.nodes))
      .catch((error) => {
        if (controller.signal.aborted) return
        setFallbackOpenCLIError(error instanceof Error ? error.message : "OpenCLI adapter catalog unavailable")
      })
      .finally(() => {
        if (!controller.signal.aborted) setOpencliLoading(false)
      })
    return () => controller.abort()
  }, [adapterCatalogResponse, fallbackOpenCLINodes.length, open])

  useEffect(() => {
    if (aiMode) requestAnimationFrame(() => aiRef.current?.focus())
  }, [aiMode])

  const close = useCallback(() => {
    if (!loading) onClose()
  }, [loading, onClose])

  const anchorPosition = useCallback(
    () => getAnchor?.() ?? screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }),
    [getAnchor, screenToFlowPosition],
  )

  const addOperator = useCallback(
    (item: PaletteItem) => {
      addNodeFromPalette(item, anchorPosition())
      onMessage?.(language === "zh-CN" ? `已添加：${item.label}` : `Added: ${item.label}`)
      onNodeCreated?.()
      onClose()
    },
    [addNodeFromPalette, anchorPosition, language, onClose, onMessage, onNodeCreated],
  )

  const addCatalogOperator = useCallback(
    (item: WorkflowNodeCatalogItem) => {
      if (catalogItemUnavailable(item)) {
        onMessage?.(
          item.runtimeCapability?.reason ??
          (language === "zh-CN"
            ? "该插件能力尚未绑定运行适配器"
            : "This plugin capability has no runtime adapter yet"),
        )
        return
      }
      addWorkflowNodeFromCatalog(item, anchorPosition())
      const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
      onMessage?.(language === "zh-CN" ? `已添加业务节点：${text.label}` : `Added business node: ${text.label}`)
      onNodeCreated?.()
      onClose()
    },
    [addWorkflowNodeFromCatalog, anchorPosition, language, onClose, onMessage, onNodeCreated],
  )

  const addPrimitive = useCallback(
    (item: WorkflowPrimitive) => {
      addPrimitiveNode(item, anchorPosition(), primitiveRuntimeCapability(capabilities, item.id))
      const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
      onMessage?.(language === "zh-CN" ? `已添加执行节点：${text.label}` : `Added execution node: ${text.label}`)
      onNodeCreated?.()
      onClose()
    },
    [addPrimitiveNode, anchorPosition, capabilities, language, onClose, onMessage, onNodeCreated],
  )

  const addOpenCLIAdapter = useCallback(
    (item: WorkflowOpenCLIAdapterNode, values: Record<string, string> = {}) => {
      const materialization = openCLIAdapterNodeMaterialization(item)
      if (materialization === "source_slot_requires_params") {
        if (item.requiredArgs.some((name) => !values[name]?.trim())) {
          setSelectedOpenCLI(item)
          setRequiredValues(values)
          return
        }
        addWorkflowNodeFromCatalog(workflowCatalogItemForOpenCLIAdapterNode(item, values), anchorPosition())
      } else if (materialization === "source_slot_ready" && item.status === "runnable") {
        addWorkflowNodeFromCatalog(workflowCatalogItemForOpenCLIAdapterNode(item), anchorPosition())
      } else if (materialization === "tool_capability_review_required") {
        addWorkflowNodeFromCatalog(openCLIAdapterNodeToCatalogItem(item), anchorPosition())
      } else {
        onMessage?.(
          language === "zh-CN"
            ? "该 OpenCLI 能力当前不可加入画布"
            : "This OpenCLI capability cannot be added to the canvas",
        )
        return
      }
      const presentation = openCLIAdapterNodePresentation(item, language)
      onMessage?.(
        materialization === "tool_capability_review_required"
          ? `${copy.addedReviewPending}：${presentation.label}`
          : language === "zh-CN"
            ? `已添加 OpenCLI 能力预设：${presentation.label}`
            : `Added OpenCLI capability preset: ${presentation.label}`,
      )
      onNodeCreated?.()
      onClose()
    },
    [addWorkflowNodeFromCatalog, anchorPosition, copy.addedReviewPending, language, onClose, onMessage, onNodeCreated],
  )

  const generate = useCallback(
    async (text: string) => {
      if (!text.trim() || loading) return
      setLoading(true)
      try {
        const response = await fetch("/api/generate-workflow", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: text }),
        })
        const data = await response.json()
        if (!response.ok) throw new Error(data?.detail ?? "failed")
        applyGeneratedWorkflow(data)
        onMessage?.(
          language === "zh-CN"
            ? `已生成工作流：${data.title ?? "未命名"}`
            : `Workflow generated: ${data.title ?? "Untitled"}`,
        )
      } catch {
        const spec = generateWorkflowLocally(text)
        applyGeneratedWorkflow(spec)
        onMessage?.(
          language === "zh-CN"
            ? `已生成工作流（本地引擎）：${spec.title}`
            : `Workflow generated locally: ${spec.title}`,
        )
      } finally {
        setLoading(false)
        onClose()
      }
    },
    [applyGeneratedWorkflow, language, loading, onClose, onMessage],
  )

  const queryText = query.trim().toLowerCase()
  const allCatalogItems = inNodeNetwork
    ? []
    : (catalogItems ?? getWorkflowNodeCatalog(workflowProfile, capabilities))
  const catalogOperators = allCatalogItems.filter(
    (item) =>
      catalogAcceptsConnection(item, compatiblePort) &&
      !workflowCatalogItemIsOpenCLIAdapterPreset(item) &&
      workflowCatalogPluginProvenance(item) === null &&
      item.runtimeCapability?.source !== "backend.workflow.tool_capabilities",
  )
  const catalogOperatorIds = new Set(catalogOperators.map((item) => item.id))
  const pluginTools = allCatalogItems.filter(
    (item) =>
      catalogAcceptsConnection(item, compatiblePort) &&
      !workflowCatalogItemIsOpenCLIAdapterPreset(item) &&
      (
        workflowCatalogPluginProvenance(item) !== null ||
        item.runtimeCapability?.source === "backend.workflow.tool_capabilities"
      ),
  )
  const opencliNodes = (adapterCatalogResponse?.nodes ?? fallbackOpenCLINodes).filter(
    (item) => catalogAcceptsConnection(openCLIAdapterNodeToCatalogItem(item), compatiblePort),
  )
  const catalogBusy = adapterCatalogResponse !== undefined
    ? Boolean(adapterCatalogLoading)
    : opencliLoading
  const catalogError = adapterCatalogResponse !== undefined
    ? adapterCatalogError
    : fallbackOpenCLIError
  const matchesCatalog = (item: WorkflowNodeCatalogItem) => {
    if (!queryText) return true
    const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
    return `${item.label} ${text.label} ${text.description ?? ""} ${item.kind} ${item.capability} ${item.keywords.join(" ")}`
      .toLowerCase()
      .includes(queryText)
  }
  const nodeCatalogGroups = useMemo(() => {
    const groups = new Map<string, WorkflowNodeCatalogItem[]>()
    for (const item of catalogOperators.filter(matchesCatalog)) {
      const current = groups.get(item.category) ?? []
      current.push(item)
      groups.set(item.category, current)
    }
    return [...groups.entries()]
    // catalogOperators and matchesCatalog are derived from current render inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capabilities, catalogItems, compatiblePort, inNodeNetwork, language, queryText, workflowProfile])
  const primitiveGroups = groupPrimitivesForNodeMenu(
    (inNodeNetwork ? getWorkflowPrimitives() : getDifyCommonWorkflowPrimitives()).filter((item) => {
      if (catalogOperatorIds.has(item.id)) return false
      if (!primitiveAcceptsConnection(item, compatiblePort)) return false
      if (!queryText) return true
      const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
      return `${item.label} ${text.label} ${text.description ?? ""} ${item.category} ${item.keywords.join(" ")}`.toLowerCase().includes(queryText)
    }),
    language,
  )
  const auxiliaryOperators = (compatiblePort ? [] : NODE_PALETTE)
    .filter((item) => item.category === "annotation" || item.category === "shape").filter(
    (item) => {
      const localized = AUXILIARY_TEXT[item.label]?.[language]
      return !queryText ||
        `${item.label} ${item.description} ${localized?.label ?? ""} ${localized?.description ?? ""} ${item.nodeType}`
          .toLowerCase()
          .includes(queryText)
    },
  )
  const matchesOpenCLI = (item: WorkflowOpenCLIAdapterNode) =>
    (!queryText || openCLIAdapterNodeSearchText(item).includes(queryText)) &&
    (opencliAccessFilter === "all" || item.access === opencliAccessFilter) &&
    (
      opencliReadinessFilter === "all" ||
      (opencliReadinessFilter === "runnable" && item.status === "runnable") ||
      (opencliReadinessFilter === "blocked" && item.status !== "runnable")
    )
  const matchingOpenCLINodes = opencliNodes.filter(matchesOpenCLI)
  const commonOpenCLIGroups = featuredOpenCLIAdapterGroups(matchingOpenCLINodes, language)
  const commonOpenCLINodes = commonOpenCLIGroups.flatMap((group) => group.nodes)
  const commonOpenCLIIds = new Set(commonOpenCLINodes.map((item) => item.id))
  const opencliSitePlugins = groupOpenCLIAdapterPlugins(matchingOpenCLINodes)
  const opencliSiteCategoryGroups = OPENCLI_SITE_CATEGORIES
    .map((category) => ({
      ...category,
      sites: opencliSitePlugins.filter((plugin) => plugin.siteCategory === category.key),
    }))
    .filter((category) => category.sites.length > 0)
  const selectedOpenCLISite = opencliSitePlugins.find((plugin) => plugin.id === selectedOpenCLISiteId) ?? null
  const visibleOpenCLINodes = queryText
    ? matchingOpenCLINodes
      .filter((item) => !commonOpenCLIIds.has(item.id))
      .slice(0, OPENCLI_SEARCH_RESULT_LIMIT)
    : selectedOpenCLISite?.commands ?? []
  const opencliPresetGroups = (() => {
    const groups = new Map<string, WorkflowOpenCLIAdapterNode[]>()
    for (const item of visibleOpenCLINodes) {
      const key = `${item.site}:${openCLIPresetKind(item)}`
      const current = groups.get(key) ?? []
      current.push(item)
      groups.set(key, current)
    }
    return [...groups.entries()]
  })()
  const filteredPluginTools = pluginTools.filter(matchesCatalog)

  const firstNode = nodeCatalogGroups
    .flatMap(([, items]) => items)
    .find((item) => !catalogItemUnavailable(item))
  const firstPrimitive = primitiveGroups[0]?.items[0]
  const firstAuxiliary = auxiliaryOperators[0]
  const firstOpenCLI = openCLIKeyboardCandidates(
    queryText,
    selectedOpenCLISite,
    matchingOpenCLINodes,
  ).find(
    (item) => !openCLIPresetUnavailable(item),
  )
  const firstPluginTool = filteredPluginTools.find((item) => !catalogItemUnavailable(item))

  if (!open || typeof document === "undefined") return null

  if (selectedOpenCLI) {
    const missingRequired = selectedOpenCLI.requiredArgs.filter((name) => !requiredValues[name]?.trim())
    const selectedPresentation = openCLIAdapterNodePresentation(selectedOpenCLI, language)
    return createPortal(
      <div className="fixed inset-0 z-50 flex items-start justify-center bg-background/80 px-4 pt-[10vh]" role="dialog" aria-modal="true" aria-label={copy.configureSource}>
        <form className="w-[34rem] overflow-hidden rounded-lg border bg-popover shadow-2xl" onSubmit={(event) => { event.preventDefault(); addOpenCLIAdapter(selectedOpenCLI, requiredValues) }}>
          <div className="flex items-center gap-3 border-b px-4 py-3">
            <button type="button" className="grid size-9 place-items-center rounded-md hover:bg-accent" onClick={() => setSelectedOpenCLI(null)} aria-label={copy.backToTools}><ArrowLeft className="size-4" /></button>
            <div className="min-w-0"><div className="truncate text-sm font-medium">{selectedPresentation.label}</div><div className="truncate text-xs text-muted-foreground">{copy.requiredBeforeAdd}</div></div>
          </div>
          <div className="grid max-h-[52vh] gap-3 overflow-y-auto p-4">
            {selectedOpenCLI.args.filter((arg) => arg.required).map((arg) => {
              const value = requiredValues[arg.name] ?? (arg.default == null ? "" : String(arg.default))
              const onChange = (next: string) => setRequiredValues((current) => ({ ...current, [arg.name]: next }))
              return (
                <label key={arg.name} className="grid gap-1.5 text-xs">
                  <span>{arg.name}<span className="ml-1 text-destructive">*</span></span>
                  {arg.choices.length > 0 ? (
                    <select value={value} onChange={(event) => onChange(event.target.value)} className="min-h-11 rounded-md border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/50" autoFocus={selectedOpenCLI.requiredArgs[0] === arg.name}>
                      <option value="">{arg.help ?? `${copy.input} ${arg.name}`}</option>
                      {arg.choices.map((choice) => <option key={String(choice)} value={String(choice)}>{String(choice)}</option>)}
                    </select>
                  ) : (
                    <input type={arg.type?.toLowerCase().includes("int") || arg.type?.toLowerCase().includes("float") ? "number" : "text"} value={value} onChange={(event) => onChange(event.target.value)} placeholder={arg.help ?? `${copy.input} ${arg.name}`} className="min-h-11 rounded-md border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/50" autoFocus={selectedOpenCLI.requiredArgs[0] === arg.name} />
                  )}
                </label>
              )
            })}
          </div>
          <div className="flex justify-end gap-2 border-t p-4"><button type="button" className="min-h-10 rounded-md border px-4 text-xs" onClick={() => setSelectedOpenCLI(null)}>{copy.cancel}</button><button type="submit" className="min-h-10 rounded-md bg-primary px-4 text-xs text-primary-foreground disabled:opacity-50" disabled={missingRequired.length > 0}>{copy.addSource}</button></div>
        </form>
      </div>,
      document.body,
    )
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-background/80 px-4 pt-[7vh]" onClick={close} onKeyDown={(event) => { if (event.key === "Escape") close() }} role="dialog" aria-modal="true" aria-label={copy.search}>
      <div
        className={cn(
          "flex max-h-[82vh] w-full flex-col overflow-hidden rounded-lg border border-ops-line bg-ops-raised shadow-overlay",
          activeTab === "tools" ? "max-w-5xl" : "max-w-3xl",
        )}
        onClick={(event) => event.stopPropagation()}
      >
        {aiMode ? (
          <div className="p-5">
            <button type="button" onClick={() => setAiMode(false)} className="mb-4 flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" />{copy.backToStart}</button>
            <div className="mb-3 flex items-center gap-2"><Sparkles className="size-4 text-primary" /><span className="text-sm font-medium">{copy.aiWorkflow}</span></div>
            <textarea ref={aiRef} value={aiPrompt} onChange={(event) => setAiPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void generate(aiPrompt) } }} placeholder={copy.describeWorkflow} className="min-h-28 w-full resize-none rounded-lg border bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-ring/50" disabled={loading} />
            <div className="mt-3 grid gap-1">{AI_EXAMPLES[language].map((example) => <button key={example} type="button" onClick={() => void generate(example)} className="truncate rounded-md px-3 py-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground">{example}</button>)}</div>
            <div className="mt-4 flex justify-end"><button type="button" onClick={() => void generate(aiPrompt)} disabled={loading || !aiPrompt.trim()} className="flex min-h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-40">{loading ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}{copy.generate}</button></div>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 border-b px-4 pt-2">
              <div className="flex items-center gap-1" role="tablist" aria-label={copy.pickerType}>
                {TAB_META.map((tab) => (
                  <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} onClick={() => { setActiveTab(tab.id); setQuery(""); setSelectedOpenCLISiteId(null); requestAnimationFrame(() => inputRef.current?.focus()) }} className={cn("min-h-12 border-b-2 px-4 text-sm font-medium transition-colors", activeTab === tab.id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>{copy.tabs[tab.id]}</button>
                ))}
              </div>
              <div className="ml-auto flex items-center rounded-xs border bg-background p-0.5" role="group" aria-label={copy.switchLanguage}>
                {(["zh-CN", "en-US"] as const).map((candidate) => (
                  <button
                    key={candidate}
                    type="button"
                    aria-pressed={language === candidate}
                    onClick={() => setLanguage("language", candidate)}
                    className={cn(
                      "min-h-7 rounded-xs px-2 font-mono text-2xs transition-colors",
                      language === candidate
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {candidate === "zh-CN" ? "中" : "EN"}
                  </button>
                ))}
              </div>
              <button type="button" onClick={close} className="rounded-xs px-2 py-1 font-mono text-3xs text-muted-foreground hover:bg-accent">ESC</button>
            </div>

            {activeTab !== "start" ? <div className="border-b p-4">
              <label className="flex min-h-12 items-center gap-3 rounded-lg border bg-background px-4 focus-within:ring-2 focus-within:ring-ring/50">
                <Search className="size-5 text-muted-foreground" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value)
                    if (event.target.value) setSelectedOpenCLISiteId(null)
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" || event.nativeEvent.isComposing) return
                    if (activeTab === "nodes") {
                      if (firstNode) addCatalogOperator(firstNode)
                      else if (firstPrimitive) addPrimitive(firstPrimitive)
                      else if (firstAuxiliary) addOperator(firstAuxiliary)
                    } else if (toolFilter !== "plugin" && firstOpenCLI) {
                      addOpenCLIAdapter(firstOpenCLI)
                    } else if (firstPluginTool) {
                      addCatalogOperator(firstPluginTool)
                    }
                  }}
                  placeholder={activeTab === "nodes" ? copy.searchNodes : copy.searchTools}
                  className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
                  aria-label={copy.search}
                />
              </label>
              {activeTab === "tools" ? (
                <div className="mt-3 grid gap-2">
                  <div className="flex items-center gap-2" aria-label={language === "zh-CN" ? "工具来源筛选" : "Tool source filter"}>
                    {([['all', copy.all], ['opencli', copy.opencliPresets], ['plugin', copy.pluginCapabilities]] as const).map(([id, label]) => <button key={id} type="button" aria-pressed={toolFilter === id} onClick={() => { setToolFilter(id); setSelectedOpenCLISiteId(null) }} className={cn("rounded-md px-3 py-1.5 text-xs", toolFilter === id ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>{label}</button>)}
                  </div>
                  {toolFilter !== "plugin" ? (
                    <div className="grid gap-2 sm:grid-cols-2">
                      <div className="grid gap-1.5" role="group" aria-label={copy.accessFilter}>
                        <span className="text-3xs text-muted-foreground">{copy.accessFilter}</span>
                        <div className="flex flex-wrap gap-1.5">
                          {([['all', copy.allRoles], ['read', copy.dataRead], ['write', copy.operationTool]] as const).map(([id, label]) => <button key={id} type="button" aria-pressed={opencliAccessFilter === id} onClick={() => { setOpencliAccessFilter(id); setSelectedOpenCLISiteId(null) }} className={cn("rounded-md border px-2.5 py-1 text-2xs", opencliAccessFilter === id ? "border-primary/40 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground")}>{label}</button>)}
                        </div>
                      </div>
                      <div className="grid gap-1.5" role="group" aria-label={copy.readinessFilter}>
                        <span className="text-3xs text-muted-foreground">{copy.readinessFilter}</span>
                        <div className="flex flex-wrap gap-1.5">
                          {([['all', copy.allStates], ['runnable', copy.runnable], ['blocked', copy.needsSetup]] as const).map(([id, label]) => <button key={id} type="button" aria-pressed={opencliReadinessFilter === id} onClick={() => { setOpencliReadinessFilter(id); setSelectedOpenCLISiteId(null) }} className={cn("rounded-md border px-2.5 py-1 text-2xs", opencliReadinessFilter === id ? "border-primary/40 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground")}>{label}</button>)}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div> : null}

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {activeTab === "nodes" ? (
                <>
                  {inNodeNetwork ? <div className="mb-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">L{nodeDepth} · {nodeLayer.label}</span> · {copy.currentLayerOnly}</div> : null}
                  {nodeCatalogGroups.map(([category, items]) => (
                    <section key={category}>
                      <SectionLabel count={items.length}>{CATEGORY_LABELS[category]?.[language] ?? category}</SectionLabel>
                      {items.map((item) => {
                        const Icon = getIcon(item.icon)
                        const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
                        return (
                          <PickerRow
                            key={item.id}
                            icon={Icon}
                            label={text.label}
                            description={text.description}
                            disabled={catalogItemUnavailable(item)}
                            onClick={() => addCatalogOperator(item)}
                            trailing={<span className={cn("rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase", runtimeStatusTone(item.runtimeCapability?.status))}>{runtimeStatusLabel(item.runtimeCapability?.status)}</span>}
                          />
                        )
                      })}
                    </section>
                  ))}
                  {primitiveGroups.map((group) => <section key={group.category}><SectionLabel count={group.items.length}>{group.label}</SectionLabel>{group.items.map((item) => { const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language); return <PickerRow key={item.id} icon={getIcon(item.icon)} label={text.label} description={text.description} onClick={() => addPrimitive(item)} /> })}</section>)}
                  {auxiliaryOperators.length ? <section><SectionLabel count={auxiliaryOperators.length}>{copy.annotations}</SectionLabel>{auxiliaryOperators.map((item) => { const text = AUXILIARY_TEXT[item.label]?.[language] ?? item; return <PickerRow key={`${item.nodeType}-${item.shape ?? item.label}`} icon={getIcon(item.icon)} label={text.label} description={text.description} onClick={() => addOperator({ ...item, label: text.label, description: text.description })} /> })}</section> : null}
                  {nodeCatalogGroups.length === 0 && primitiveGroups.length === 0 && auxiliaryOperators.length === 0 ? <p className="py-12 text-center text-sm text-muted-foreground">{copy.noNodes}</p> : null}
                </>
              ) : null}

              {activeTab === "tools" ? (
                <>
                  {toolFilter !== "plugin" ? (
                    <section>
                      {catalogBusy ? <div className="flex items-center gap-2 px-3 py-5 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{copy.loadingOpencli}</div> : null}
                      {!catalogBusy && matchingOpenCLINodes.length > 0 ? (
                        <div
                          className="grid min-h-0 gap-3 lg:grid-cols-[13rem_minmax(0,1fr)]"
                          data-testid="opencli-preset-layout"
                        >
                          <aside
                            className="self-start rounded-md border border-ops-line bg-ops-panel p-2 lg:sticky lg:top-0"
                            data-testid="opencli-group-navigation"
                          >
                            <div className="border-b border-ops-line px-2 pb-3 pt-1">
                              <span className="block text-3xs font-medium uppercase tracking-wide text-zinc-500">
                                {copy.catalogIndex}
                              </span>
                              <span className="mt-1 flex items-end justify-between gap-2">
                                <span className="text-xs font-medium text-zinc-100">{copy.opencliCapabilities}</span>
                                <span className="font-mono text-2xs text-primary-400">{matchingOpenCLINodes.length}</span>
                              </span>
                            </div>
                            <nav className="mt-2 grid gap-1" aria-label={copy.catalogIndex}>
                              {selectedOpenCLISite ? (
                                <button
                                  type="button"
                                  onClick={() => setSelectedOpenCLISiteId(null)}
                                  className="flex min-h-9 items-center justify-between gap-2 rounded-xs border-l-2 border-transparent px-2 text-2xs text-zinc-400 transition-colors hover:border-primary-400 hover:bg-ops-raised hover:text-zinc-100 focus-visible:border-primary-400 focus-visible:text-zinc-100"
                                >
                                  <span className="flex min-w-0 items-center gap-2">
                                    <ArrowLeft className="size-3.5 shrink-0" />
                                    <span className="truncate">{copy.backToSiteDirectory}</span>
                                  </span>
                                </button>
                              ) : (
                                <>
                                  {commonOpenCLIGroups.map((group) => (
                                    <a
                                      key={group.id}
                                      href={`#opencli-group-${group.id}`}
                                      className="flex min-h-9 items-center justify-between gap-2 rounded-xs border-l-2 border-transparent px-2 text-2xs text-zinc-400 transition-colors hover:border-primary-400 hover:bg-ops-raised hover:text-zinc-100 focus-visible:border-primary-400 focus-visible:text-zinc-100"
                                    >
                                      <span className="min-w-0 truncate">{group.label}</span>
                                      <span className="font-mono text-3xs text-zinc-500">{group.nodes.length}</span>
                                    </a>
                                  ))}
                                  {!queryText ? (
                                    <a
                                      href="#opencli-site-directory"
                                      className="flex min-h-9 items-center justify-between gap-2 rounded-xs border-l-2 border-transparent px-2 text-2xs text-zinc-400 transition-colors hover:border-primary-400 hover:bg-ops-raised hover:text-zinc-100 focus-visible:border-primary-400 focus-visible:text-zinc-100"
                                    >
                                      <span>{copy.allOpencliSites}</span>
                                      <span className="font-mono text-3xs text-zinc-500">{opencliSitePlugins.length}</span>
                                    </a>
                                  ) : null}
                                  {queryText && opencliPresetGroups.length ? (
                                    <a
                                      href="#opencli-more-presets"
                                      className="flex min-h-9 items-center justify-between gap-2 rounded-xs border-l-2 border-transparent px-2 text-2xs text-zinc-400 transition-colors hover:border-primary-400 hover:bg-ops-raised hover:text-zinc-100 focus-visible:border-primary-400 focus-visible:text-zinc-100"
                                    >
                                      <span>{copy.moreOpencliPresets}</span>
                                      <span className="font-mono text-3xs text-zinc-500">{visibleOpenCLINodes.length}</span>
                                    </a>
                                  ) : null}
                                </>
                              )}
                            </nav>
                          </aside>

                          <div className="min-w-0">
                            {!selectedOpenCLISite && commonOpenCLINodes.length > 0 ? (
                              <section className="rounded-md border border-ops-line bg-ops-black p-3">
                                <div className="flex items-end justify-between gap-4 border-b border-ops-line px-1 pb-3">
                                  <span>
                                    <span className="block text-sm font-medium text-zinc-100">{copy.featuredSites}</span>
                                    <span className="mt-0.5 block text-2xs text-zinc-500">{copy.featuredDescription}</span>
                                  </span>
                                  <span className="font-mono text-2xs text-primary-400">{commonOpenCLINodes.length}</span>
                                </div>
                                <div className="mt-2 grid gap-4">
                                  {commonOpenCLIGroups.map((group) => (
                                    <section
                                      key={group.id}
                                      id={`opencli-group-${group.id}`}
                                      className="scroll-mt-3"
                                    >
                                      <SectionLabel count={group.nodes.length}>{group.label}</SectionLabel>
                                      <div className="grid gap-2 lg:grid-cols-2">
                                        {group.nodes.map((item) => (
                                          <OpenCLIPickerRow
                                            key={item.id}
                                            item={item}
                                            language={language}
                                            onClick={() => addOpenCLIAdapter(item)}
                                          />
                                        ))}
                                      </div>
                                    </section>
                                  ))}
                                </div>
                              </section>
                            ) : null}

                            {selectedOpenCLISite ? (
                              <section className="rounded-md border border-ops-line bg-ops-black p-3">
                                <div className="flex items-start justify-between gap-4 border-b border-ops-line px-1 pb-3">
                                  <span className="min-w-0">
                                    <button
                                      type="button"
                                      onClick={() => setSelectedOpenCLISiteId(null)}
                                      className="mb-2 inline-flex min-h-8 items-center gap-2 rounded-xs px-2 text-2xs text-zinc-400 hover:bg-ops-raised hover:text-zinc-100"
                                    >
                                      <ArrowLeft className="size-3.5" />
                                      {copy.backToSiteDirectory}
                                    </button>
                                    <span className="block truncate text-sm font-medium text-zinc-100">
                                      {selectedOpenCLISite.label}
                                    </span>
                                    <span className="mt-0.5 block font-mono text-3xs text-zinc-500">
                                      {selectedOpenCLISite.site} · {copy.siteCommands}
                                    </span>
                                  </span>
                                  <span className="font-mono text-2xs text-primary-400">
                                    {selectedOpenCLISite.commands.length}
                                  </span>
                                </div>
                                <div className="mt-2 grid gap-4">
                                  {opencliPresetGroups.map(([groupKey, items]) => {
                                    const [, presetKind] = groupKey.split(":")
                                    return (
                                      <section key={groupKey}>
                                        <SectionLabel count={items.length}>
                                          {presetKind === "source_slot" ? copy.dataRead : copy.operationTool}
                                        </SectionLabel>
                                        <div className="grid gap-2 lg:grid-cols-2">
                                          {items.map((item) => (
                                            <OpenCLIPickerRow
                                              key={item.id}
                                              item={item}
                                              language={language}
                                              onClick={() => addOpenCLIAdapter(item)}
                                            />
                                          ))}
                                        </div>
                                      </section>
                                    )
                                  })}
                                </div>
                              </section>
                            ) : null}

                            {!selectedOpenCLISite && !queryText ? (
                              <section
                                id="opencli-site-directory"
                                className={cn(
                                  "rounded-md border border-ops-line bg-ops-black p-3 scroll-mt-3",
                                  commonOpenCLINodes.length > 0 && "mt-3",
                                )}
                              >
                                <div className="flex items-end justify-between gap-4 border-b border-ops-line px-1 pb-3">
                                  <span>
                                    <span className="block text-sm font-medium text-zinc-100">{copy.allOpencliSites}</span>
                                    <span className="mt-0.5 block text-2xs text-zinc-500">{copy.siteDirectoryDescription}</span>
                                  </span>
                                  <span className="font-mono text-2xs text-primary-400">{opencliSitePlugins.length}</span>
                                </div>
                                <div className="mt-2 grid gap-4">
                                  {opencliSiteCategoryGroups.map((category) => (
                                    <section key={category.key}>
                                      <SectionLabel count={category.sites.length}>
                                        {language === "zh-CN" ? category.label : category.labelEn}
                                      </SectionLabel>
                                      <div className="grid gap-2 sm:grid-cols-2">
                                        {category.sites.map((site) => (
                                          <button
                                            key={site.id}
                                            type="button"
                                            onClick={() => setSelectedOpenCLISiteId(site.id)}
                                            className="group flex min-h-12 items-center gap-3 rounded-xs border border-ops-line bg-ops-panel px-3 text-left outline-none transition-colors hover:border-ops-line-strong hover:bg-ops-raised focus-visible:ring-2 focus-visible:ring-primary-400/50"
                                            aria-label={`${site.label}, ${site.commandCount} ${copy.siteCommands}`}
                                          >
                                            <Globe className="size-4 shrink-0 text-primary-400" />
                                            <span className="min-w-0 flex-1">
                                              <span className="block truncate text-xs font-medium text-zinc-100">{site.label}</span>
                                              <span className="block truncate font-mono text-3xs text-zinc-500">{site.site}</span>
                                            </span>
                                            <span className="font-mono text-3xs text-zinc-500">{site.commandCount}</span>
                                            <ChevronRight className="size-3.5 text-zinc-500 transition-transform group-hover:translate-x-0.5" />
                                          </button>
                                        ))}
                                      </div>
                                    </section>
                                  ))}
                                </div>
                              </section>
                            ) : null}

                            {!selectedOpenCLISite && queryText && opencliPresetGroups.length ? (
                              <section
                                id="opencli-more-presets"
                                className="mt-3 rounded-md border border-ops-line bg-ops-black p-3 scroll-mt-3"
                              >
                                <div className="flex items-center justify-between gap-4 border-b border-ops-line px-1 pb-3">
                                  <span className="text-sm font-medium text-zinc-100">{copy.moreOpencliPresets}</span>
                                  <span className="font-mono text-2xs text-zinc-500">{visibleOpenCLINodes.length}</span>
                                </div>
                                <div className="mt-2 grid gap-4">
                                  {opencliPresetGroups.map(([groupKey, items]) => {
                                    const [site, presetKind] = groupKey.split(":")
                                    const groupLabel = `${site} · ${presetKind === "source_slot" ? copy.dataRead : copy.operationTool}`
                                    return (
                                      <section key={groupKey}>
                                        <SectionLabel count={items.length}>{groupLabel}</SectionLabel>
                                        <div className="grid gap-2 lg:grid-cols-2">
                                          {items.map((item) => (
                                            <OpenCLIPickerRow
                                              key={item.id}
                                              item={item}
                                              language={language}
                                              onClick={() => addOpenCLIAdapter(item)}
                                            />
                                          ))}
                                        </div>
                                      </section>
                                    )
                                  })}
                                </div>
                              </section>
                            ) : null}
                          </div>
                        </div>
                      ) : null}
                      {!catalogBusy && queryText && matchingOpenCLINodes.length > commonOpenCLINodes.length + visibleOpenCLINodes.length ? (
                        <p className="px-3 py-3 text-2xs text-muted-foreground">
                          {copy.shown} {commonOpenCLINodes.length + visibleOpenCLINodes.length} / {matchingOpenCLINodes.length}. {copy.refineSearch}
                        </p>
                      ) : null}
                    </section>
                  ) : null}
                  {toolFilter !== "opencli" ? (
                    <section>
                      <SectionLabel count={filteredPluginTools.length}>{copy.pluginTools}</SectionLabel>
                      {filteredPluginTools.map((item) => {
                        const text = localizeNodeText(item.id, { label: item.label, description: item.description }, language)
                        const provenance = workflowCatalogPluginProvenance(item)
                        return <PickerRow key={`tool-${item.id}`} icon={getIcon(item.icon)} label={text.label} description={provenance ? `${provenance.providerKey} · ${provenance.version}` : text.description} disabled={catalogItemUnavailable(item)} onClick={() => addCatalogOperator(item)} />
                      })}
                    </section>
                  ) : null}
                  {!catalogBusy && catalogError ? <p className="px-3 py-2 text-xs text-destructive">{catalogError}</p> : null}
                  {!catalogBusy && ((toolFilter === "opencli" && matchingOpenCLINodes.length === 0) || (toolFilter === "plugin" && filteredPluginTools.length === 0) || (toolFilter === "all" && matchingOpenCLINodes.length === 0 && filteredPluginTools.length === 0)) ? <p className="py-12 text-center text-sm text-muted-foreground">{copy.noTools}</p> : null}
                </>
              ) : null}

              {activeTab === "start" ? (
                <section><SectionLabel>{copy.createImport}</SectionLabel>
                  <PickerRow icon={Sparkles} label={copy.aiWorkflow} description={copy.aiWorkflowDescription} onClick={() => setAiMode(true)} />
                  <PickerRow icon={FileUp} label={copy.importApp} description={copy.importAppDescription} onClick={() => { onClose(); onImportApp?.() }} />
                  <PickerRow icon={Boxes} label={copy.startFromNode} description={copy.startFromNodeDescription} onClick={() => { setActiveTab("nodes"); setQuery("") }} />
                  <SectionLabel>{copy.canvasActions}</SectionLabel>
                  <PickerRow icon={LayoutGrid} label={copy.autoLayout} description={copy.autoLayoutDescription} onClick={() => { void autoLayout("TB", "elk", true); onMessage?.(language === "zh-CN" ? "已应用自动布局" : "Auto-layout applied"); onClose() }} />
                  <PickerRow icon={Save} label={copy.saveDraft} description={copy.saveDraftDescription} onClick={() => { save(); onMessage?.(language === "zh-CN" ? "已保存到本地" : "Saved locally"); onClose() }} />
                  <PickerRow icon={RotateCcw} label={copy.restoreExample} description={copy.restoreExampleDescription} onClick={() => { reset(); onMessage?.(language === "zh-CN" ? "已恢复示例工作流" : "Example workflow restored"); onClose() }} />
                </section>
              ) : null}
            </div>

            {activeTab === "tools" ? <a href="/plugins" className="flex min-h-12 items-center justify-between border-t px-5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"><span>{copy.morePlugins}</span><ChevronRight className="size-4" /></a> : null}
            <div className="flex min-h-10 items-center justify-between border-t px-4 font-mono text-3xs text-muted-foreground"><span>{activeTab === "start" ? copy.chooseStart : copy.enterToAdd}</span><span>{copy.close}</span></div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
