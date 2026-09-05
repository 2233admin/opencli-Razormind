"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import {
  AlertTriangle,
  ChevronRight,
  CircleHelp,
  Database,
  ExternalLink,
  GitBranch,
  Plus,
  PlugZap,
  RotateCcw,
  Search,
  Trash2,
  Unplug,
} from "lucide-react"
import { driver, type Driver } from "driver.js"
import "driver.js/dist/driver.css"
import { useReactFlow } from "@xyflow/react"
import { Ripple } from "@/components/canvasui/Ripple"
import { clearParameterDraftEntry, useFlowStore } from "@/lib/flow/store"
import { portTypesCompatible, wouldCreateCycle } from "@/lib/flow/graph"
import { useSettingsStore } from "@/lib/flow/settings-store"
import {
  useProjectSourceBindingRevisions,
  useProjectSourceBindings,
  useSources,
} from "@/lib/api/hooks"
import type { SourceBinding, SourceBindingRevision } from "@/lib/api/types"
import type {
  FieldConfig,
  GeneratedWorkflowEdgeMapping,
  SemanticLinkMeta,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
} from "@/lib/flow/types"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { NodeInternalStatus } from "@/lib/workflow/node-internals"
import { getNodeTemplate } from "@/lib/workflow/node-templates"
import {
  buildParameterInterfaceView,
  parseJsonParameterValue,
  type ParameterInterfaceViewField,
} from "@/lib/workflow/parameter-interface"
import { blockedActionViewForRuntime } from "@/lib/workflow/capabilities"
import { businessNodeName } from "@/lib/workflow/business-node-experience"
import { buildCanonicalNodeViewContract } from "@/lib/workflow/canonical-node-contract"
import {
  getNodeDisplayId,
  localizeNodeParameterText,
  localizeNodeText,
  shouldPreserveNodeAuthoredText,
  type WorkflowLanguage,
} from "@/lib/workflow/node-i18n"
import {
  buildWorkflowOutlineRows,
  filterWorkflowOutlineRows,
  visibleWorkflowOutlineRows,
  workflowDirectUpstreamNodeIds,
  workflowInputReferenceForPort,
  workflowOutlineRowHasChildren,
} from "@/lib/workflow/workflow-outline"
import { findWorkflowProjectNodeByCanvasId } from "@/lib/workflow/node-path"
import {
  collectorKindForCatalogId,
  createCollectorSource,
  isCollectorNodeParams,
  IMAGE_ASSET_CATALOG_ID,
  IMAGE_GENERATION_CATALOG_ID,
  isOpenCLISourceSlotArray,
  type CollectorNodeParams,
  type CollectorSourceDefinition,
  type OpenCLISourceSlot,
} from "@/lib/workflow/node-catalog"
import {
  ASHARE_OPENCLI_SOURCES,
  OPENCLI_SITUATION_SOURCES,
} from "@/lib/workflow/opencli-business-workflows"
import {
  matchWorkflowFleetCapability,
  type WorkflowFleetCapabilityMatchResponse,
} from "@/lib/workflow/backend-fleet"
import {
  openCLISlotFromDataSource,
  SOURCE_ARGUMENT_LABELS,
  SOURCE_MARKET_OPTIONS,
  sourceBusinessArguments,
  sourceBusinessQuery,
  sourceMarket,
  sourceSlotKey,
  updateSourceBusinessQuery,
  updateSourceMarket,
} from "@/lib/workflow/source-business-config"
import type {
  WorkflowCapability,
  WorkflowNodeKind,
  WorkflowProject,
  WorkflowProjectNode,
} from "@/lib/workflow/schema"
import {
  MonoRow,
  PanelShell,
  SectionCaption,
  workflowStatusDotClass,
  workflowStatusText,
} from "./inspector-shell"
import { cn } from "@/lib/utils"
import {
  FeishuBitableTargetEditor,
  type FeishuBitableTargetParams,
} from "./feishu-bitable-target-editor"

const edgeTypeOptions = [
  { value: "workflow", label: "默认（贝塞尔曲线）" },
  { value: "editable", label: "可编辑路径" },
  { value: "routed", label: "智能避障（正交路由）" },
]

const edgeTypeHints: Record<string, string> = {
  workflow: "标准平滑曲线连线。",
  editable: "选中后可拖动控制点调整路径，双击线条添加控制点、双击控制点删除。",
  routed: "自动绕开中间节点的正交折线，适合密集流程图。",
}

const BUILT_IN_SOURCE_IDS = new Set([
  ...ASHARE_OPENCLI_SOURCES,
  ...OPENCLI_SITUATION_SOURCES,
].map((source) => source.id))

const SOURCE_ID_ACRONYMS: Record<string, string> = {
  bse: "BSE",
  cninfo: "CNInfo",
  pdf: "PDF",
  sse: "SSE",
  szse: "SZSE",
  ths: "THS",
}

const SOURCE_POOL_TOUR_STORAGE_KEY = "opencli:source-pool-tour:v1"

const SOURCE_POOL_TOUR_COPY = {
  "zh-CN": {
    button: "查看来源池操作引导",
    next: "下一步",
    previous: "上一步",
    done: "完成",
    steps: [
      {
        title: "来源池",
        description: "来源按业务分组呈现；每张卡片对应一个可独立配置的 OpenCLI 来源。",
      },
      {
        title: "搜索与定位",
        description: "按名称、站点、命令或分组过滤来源，匹配的分组会自动展开。",
      },
      {
        title: "固定 Binding Revision",
        description: "展开来源分组和卡片，选择当前项目的 Source Binding，并显式固定不可变 Revision。",
      },
      {
        title: "Fleet 预检",
        description: "先检查 Binding 生命周期、Worker、站点和命令能力；预检不会执行来源。",
      },
      {
        title: "提交真实运行",
        description: "确认预检后，使用画布顶部“运行”提交当前工作流；Preview 仍只做预览。",
      },
    ],
  },
  "en-US": {
    button: "Open source pool guide",
    next: "Next",
    previous: "Previous",
    done: "Done",
    steps: [
      {
        title: "Source pool",
        description: "Sources are grouped by business purpose; each card is an independently configured OpenCLI source.",
      },
      {
        title: "Search and locate",
        description: "Filter by name, site, command, or group. Matching groups open automatically.",
      },
      {
        title: "Pin a Binding Revision",
        description: "Open a source group and card, choose a Source Binding from this project, then pin an immutable Revision.",
      },
      {
        title: "Fleet preflight",
        description: "Check the Binding lifecycle, Worker, site, and command capability without executing the source.",
      },
      {
        title: "Submit a real run",
        description: "After preflight, use Run in the canvas header to submit the workflow. Preview remains non-executing.",
      },
    ],
  },
} as const

function SourcePoolTour({ language }: { language: WorkflowLanguage }) {
  const tourRef = useRef<Driver | null>(null)
  const tourCopy = SOURCE_POOL_TOUR_COPY[language]
  const startTour = useCallback(() => {
    tourRef.current?.destroy()
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const tour = driver({
      animate: !reduceMotion,
      allowClose: true,
      allowKeyboardControl: true,
      smoothScroll: !reduceMotion,
      overlayColor: "var(--oc-bg)",
      overlayOpacity: 0.82,
      stagePadding: 5,
      stageRadius: 2,
      popoverClass: "opencli-source-tour",
      showProgress: true,
      progressText: "{{current}} / {{total}}",
      nextBtnText: tourCopy.next,
      prevBtnText: tourCopy.previous,
      doneBtnText: tourCopy.done,
      skipMissingElement: true,
      onDestroyed: () => {
        window.localStorage.setItem(SOURCE_POOL_TOUR_STORAGE_KEY, "1")
        tourRef.current = null
      },
      steps: [
        {
          element: '[data-testid="source-pool-editor"]',
          popover: { ...tourCopy.steps[0], side: "left", align: "start" },
        },
        {
          element: '[data-testid="source-pool-search"]',
          popover: { ...tourCopy.steps[1], side: "bottom", align: "start" },
        },
        {
          element: '[data-testid="source-pool-group"]',
          popover: { ...tourCopy.steps[2], side: "left", align: "start" },
        },
        {
          element: '[data-testid="source-pool-preflight"]',
          popover: { ...tourCopy.steps[3], side: "bottom", align: "end" },
        },
        {
          element: '[data-testid="workflow-run"]',
          popover: { ...tourCopy.steps[4], side: "bottom", align: "end" },
        },
      ],
    })
    tourRef.current = tour
    tour.drive()
  }, [tourCopy])

  useEffect(() => {
    if (window.localStorage.getItem(SOURCE_POOL_TOUR_STORAGE_KEY)) return
    const timer = window.setTimeout(startTour, 500)
    return () => {
      window.clearTimeout(timer)
      tourRef.current?.destroy()
    }
  }, [startTour])

  return (
    <button
      type="button"
      aria-label={tourCopy.button}
      title={tourCopy.button}
      onClick={startTour}
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-xs border border-ops-line text-zinc-500 transition-colors hover:border-ops-line-strong hover:text-zinc-100"
    >
      <CircleHelp className="size-3.5" />
    </button>
  )
}

function sourceCardLabel(source: OpenCLISourceSlot, language: WorkflowLanguage): string {
  if (language === "zh-CN" || !BUILT_IN_SOURCE_IDS.has(source.id)) return source.label
  return source.id
    .split("-")
    .map((part) => SOURCE_ID_ACRONYMS[part] ?? `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ")
}

const INSPECTOR_COPY = {
  "zh-CN": {
    switchLanguage: "切换节点语言",
    tabs: { outline: "大纲", config: "配置", run: "上次运行", trace: "Trace" },
    outlineHelp: "按真实连线生成流程大纲。单击选择并定位，双击或按 Enter 打开配置。",
    connectedFlow: "流程结构",
    needsAttention: "待连接节点",
    noNodes: "当前工作流还没有节点。",
    nodes: "节点",
    connections: "连线",
    outlineSearch: "搜索节点、类型或说明",
    collapseNode: "折叠节点",
    expandNode: "展开节点",
    promptSection: "节点提示词配置",
    promptHelp: "这里只显示节点已保存的提示词配置和测试用例。AI 编辑生成的是待审阅提案，确认应用后才会更新工作流。",
    configuredPrompt: "已配置提示词",
    testInput: "测试输入",
    expectedOutput: "期望输出",
    noPrompt: "当前节点未配置提示词测试用例。真实运行输入和输出只在执行时产生，请在“运行结果”或 Run Trace 中查看。",
    runResult: "运行结果",
    runHelp: "明确的运行结果会记录在 Run Trace 中；此处展示该节点的运行身份与产物摘要。",
    trace: "执行过程",
    traceHelp: "试运行后，可在 Run Trace 中按节点 ID 查看有序执行事件。",
    businessConfig: "业务配置",
    businessHelp: "名称和说明会直接显示在画布节点上。",
    systemNode: "系统能力",
    nodeName: "节点名称",
    nodeNamePlaceholder: "例如：采集 A 股市场数据",
    nodeDescription: "节点说明",
    nodeDescriptionPlaceholder: "说明这个节点为业务流程完成什么",
    blockedAction: "受阻动作",
    noPublicParameters: "此分组没有可配置参数。",
    contract: "运行契约",
    required: "必填",
    internals: "内部步骤",
    steps: "步",
    advanced: "高级设置",
    interface: "接口",
    inputs: "输入连接",
    inputsHelp: "每个输入端口只列出类型兼容的真实上游输出。可在这里重接或解绑。",
    inputUnbound: "未连接",
    noCompatibleOutputs: "没有可用的兼容输出。",
    insertVariable: "引用上游输出",
    fieldMapping: "字段映射",
    fieldMappingGap: "上下游契约尚未提供字段 schema，暂不允许手填来源或目标字段路径。已有旧映射只读保留。",
    legacyMapping: "旧映射",
    transform: "高级转换",
    compatibility: "兼容性",
    compilable: "可编译",
    blocked: "阻止发布 / 运行",
    noContract: "后端尚未为此节点投影输入/输出契约。",
    debug: "调试信息",
    dataSources: "数据来源",
    sourceHelp: "配置当前工作流的 OpenCLI 来源槽位；运行前会检查连接与 Worker。",
    manageSources: "管理连接",
    sources: "个来源",
    parallel: "智能多源 · 并行执行",
    addConnectedSource: "添加已连接的数据源",
    loading: "加载中",
    addSource: "添加数据源",
    sourceUnavailable: "数据源目录暂时不可用。现有配置仍可使用，连接后端后即可选择其他来源。",
    noOtherSources: "全局目录中暂无其他已连接的 OpenCLI 数据源。",
    allSourcesAdded: "所有已连接的 OpenCLI 数据源都已添加到当前节点。",
    collectionTopic: "采集主题",
    collectionTopicPlaceholder: "例如：人工智能、贵州茅台",
    collectionTopicHint: "一次设置会同步到所有搜索型来源。",
    market: "市场范围",
    contentType: "采集内容",
    addContent: "添加一类来源",
    contentTypes: {
      market: "行情",
      filings: "公告财报",
      macro: "宏观",
      news: "新闻",
      social: "社区舆情",
      video: "视频",
    },
    configureSource: "设置",
    positionalArgument: "搜索词 / 资源 ID",
    removedSource: "已移除",
    undoRemove: "撤销",
    collectionOptions: "采集选项",
    items: "项",
    opencliMapping: "OpenCLI 映射",
    site: "站点",
    command: "命令",
    autoSaveHint: "配置会自动保存。使用 Preview 预检运行能力，使用画布顶部“运行”提交真实运行。",
    parameters: "参数",
    jsonObjectRequired: "参数必须是 JSON 对象",
    invalidJson: "JSON 格式不正确",
    removeSource: "移除来源",
    sourceName: "名称",
  },
  "en-US": {
    switchLanguage: "Switch node language",
    tabs: { outline: "Outline", config: "Configure", run: "Last run", trace: "Trace" },
    outlineHelp: "Derived from real connections. Click to select and locate; double-click or press Enter to configure.",
    connectedFlow: "Flow",
    needsAttention: "Needs connection",
    noNodes: "This workflow has no nodes yet.",
    nodes: "nodes",
    connections: "connections",
    outlineSearch: "Search nodes, types, or descriptions",
    collapseNode: "Collapse node",
    expandNode: "Expand node",
    promptSection: "Node prompt configuration",
    promptHelp: "This shows only saved prompt configuration and test cases. AI edits remain review proposals until you apply them.",
    configuredPrompt: "Configured prompt",
    testInput: "Test input",
    expectedOutput: "Expected output",
    noPrompt: "This node has no prompt test case. Real inputs and outputs are created only during a run; inspect Run Result or Run Trace.",
    runResult: "Run result",
    runHelp: "Authoritative results live in Run Trace; this view summarizes the node identity and produced artifacts.",
    trace: "Execution trace",
    traceHelp: "After a test run, inspect ordered execution events by node ID in Run Trace.",
    businessConfig: "Business configuration",
    businessHelp: "The name and description appear directly on the canvas node.",
    systemNode: "System capability",
    nodeName: "Node name",
    nodeNamePlaceholder: "Example: Collect A-share market data",
    nodeDescription: "Node description",
    nodeDescriptionPlaceholder: "Explain what this node does for the business workflow",
    blockedAction: "Blocked action",
    noPublicParameters: "No configurable parameters in this group.",
    contract: "Runtime contract",
    required: "required",
    internals: "Internal steps",
    steps: "steps",
    advanced: "Advanced",
    interface: "Interface",
    inputs: "Inputs",
    inputsHelp: "Each input lists only type-compatible outputs from real upstream nodes. Reconnect or unbind here.",
    inputUnbound: "Unbound",
    noCompatibleOutputs: "No compatible outputs are available.",
    insertVariable: "Reference upstream output",
    fieldMapping: "Field mapping",
    fieldMappingGap: "The upstream and downstream contracts do not expose field schemas yet. Manual source and target field paths are disabled; legacy mappings remain read-only.",
    legacyMapping: "Legacy mapping",
    transform: "Advanced transform",
    compatibility: "Compatibility",
    compilable: "Compilable",
    blocked: "Blocks publish / run",
    noContract: "No backend input/output contract is projected for this node.",
    debug: "Debug",
    dataSources: "Data sources",
    sourceHelp: "Configure this workflow's OpenCLI source slots; connections and Workers are checked before Run.",
    manageSources: "Manage connections",
    sources: "sources",
    parallel: "Smart multi-source · parallel execution",
    addConnectedSource: "Add a connected data source",
    loading: "Loading",
    addSource: "Add source",
    sourceUnavailable: "The data source catalog is temporarily unavailable. Existing configuration remains usable.",
    noOtherSources: "No other connected OpenCLI data sources are available in the global catalog.",
    allSourcesAdded: "All connected OpenCLI data sources are already added to this node.",
    collectionTopic: "Collection topic",
    collectionTopicPlaceholder: "Example: artificial intelligence, Apple",
    collectionTopicHint: "One value is synchronized to every search-based source.",
    market: "Market scope",
    contentType: "Content",
    addContent: "Add a source group",
    contentTypes: {
      market: "Market",
      filings: "Filings",
      macro: "Macro",
      news: "News",
      social: "Social",
      video: "Video",
    },
    configureSource: "Configure",
    positionalArgument: "Search term / resource ID",
    removedSource: "Removed",
    undoRemove: "Undo",
    collectionOptions: "Collection options",
    items: "items",
    opencliMapping: "OpenCLI mapping",
    site: "Site",
    command: "Command",
    autoSaveHint: "Configuration saves automatically. Use Preview for readiness checks and Run for a real execution.",
    parameters: "Parameters",
    jsonObjectRequired: "Parameters must be a JSON object",
    invalidJson: "Invalid JSON",
    removeSource: "Remove source",
    sourceName: "Name",
  },
} as const

const PARAMETER_GROUP_TEXT: Record<string, Record<WorkflowLanguage, string>> = {
  "Parameter Interface": { "zh-CN": "参数配置", "en-US": "Parameters" },
  Configuration: { "zh-CN": "配置", "en-US": "Configuration" },
  Internals: { "zh-CN": "内部参数", "en-US": "Internals" },
  Advanced: { "zh-CN": "高级参数", "en-US": "Advanced" },
  Input: { "zh-CN": "输入", "en-US": "Input" },
  Output: { "zh-CN": "输出", "en-US": "Output" },
}

const internalStatusLabel: Record<NodeInternalStatus, string> = {
  ready: "READY",
  simulated: "SIM",
  future: "NEXT",
}

const internalStatusClass: Record<NodeInternalStatus, string> = {
  ready: "border-[#4ade80]/30 bg-[#4ade80]/10 text-[#4ade80]",
  simulated: "border-[#a0c3ec]/30 bg-[#a0c3ec]/10 text-[#a0c3ec]",
  future: "border-border bg-muted text-muted-foreground",
}

const houdiniInputClass =
  "h-7 rounded-[2px] border-[#2c3036] bg-[#07080a] px-2 font-mono text-[11px] text-foreground shadow-inner outline-none transition-colors placeholder:text-muted-foreground/45 focus-visible:border-[#5f6976] focus-visible:ring-0 focus-visible:ring-offset-0 disabled:opacity-60 read-only:opacity-80"

const houdiniSelectTriggerClass =
  "h-7 rounded-[2px] border-[#2c3036] bg-[#07080a] px-2 font-mono text-[11px] shadow-inner focus:ring-0 focus:ring-offset-0"

const houdiniTextareaClass =
  "min-h-20 rounded-[2px] border-[#2c3036] bg-[#07080a] px-2 py-1.5 font-mono text-[11px] leading-relaxed shadow-inner focus-visible:ring-0 focus-visible:ring-offset-0"

const houdiniDetailsClass = "overflow-hidden rounded-[3px] border border-[#20242a] bg-[#111317]/74"

const houdiniSummaryClass =
  "flex cursor-pointer list-none items-center justify-between gap-3 bg-[#171a1f] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground transition-colors hover:text-foreground"

type InspectorMode = keyof typeof INSPECTOR_COPY["zh-CN"]["tabs"]

function InspectorLanguageToggle({
  language,
  onChange,
}: {
  language: WorkflowLanguage
  onChange: (language: WorkflowLanguage) => void
}) {
  const copy = INSPECTOR_COPY[language]
  return (
    <div className="flex items-center justify-end">
      <div className="flex items-center rounded-xs border bg-background p-0.5" role="group" aria-label={copy.switchLanguage}>
        {(["zh-CN", "en-US"] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            aria-pressed={language === candidate}
            onClick={() => onChange(candidate)}
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
    </div>
  )
}

function InspectorModeTabs({
  active,
  copy,
  hasSelection,
  onChange,
}: {
  active: InspectorMode
  copy: typeof INSPECTOR_COPY[WorkflowLanguage]
  hasSelection: boolean
  onChange: (mode: InspectorMode) => void
}) {
  return (
    <div className="grid grid-cols-4 overflow-hidden rounded-[3px] border border-[#20242a] bg-[#171a1f] font-mono text-[10px] uppercase">
      {(["outline", "config", "run", "trace"] as const).map((mode) => {
        const disabled = mode !== "outline" && !hasSelection
        return (
          <button
            key={mode}
            type="button"
            disabled={disabled}
            onClick={() => onChange(mode)}
            className={cn(
              "border-r border-[#2b3037] px-2 py-2 transition-colors last:border-r-0",
              active === mode
                ? "bg-[#050607] text-foreground"
                : "text-muted-foreground hover:bg-[#252a31] hover:text-foreground",
              disabled && "cursor-not-allowed opacity-35",
            )}
          >
            {copy.tabs[mode]}
          </button>
        )
      })}
    </div>
  )
}

function WorkflowOutlinePanel({
  businessLevel,
  compact,
  edges,
  language,
  nodes,
  onClose,
  onLanguageChange,
  onModeChange,
  onOpenNode,
  onSelectNode,
  selectedNodeId,
  title,
  workflowProject,
}: {
  businessLevel: boolean
  compact: boolean
  edges: WorkflowEdge[]
  language: WorkflowLanguage
  nodes: WorkflowNode[]
  onClose: () => void
  onLanguageChange: (language: WorkflowLanguage) => void
  onModeChange: (mode: InspectorMode) => void
  onOpenNode: (nodeId: string) => void
  onSelectNode: (nodeId: string) => void
  selectedNodeId?: string
  title: string
  workflowProject: WorkflowProject
}) {
  const copy = INSPECTOR_COPY[language]
  const [outlineQuery, setOutlineQuery] = useState("")
  const [collapsedNodeIds, setCollapsedNodeIds] = useState<Set<string>>(() => new Set())
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const rows = buildWorkflowOutlineRows(nodes, edges)
  const outlineRowIndexById = new Map(rows.map((row, index) => [row.nodeId, index]))
  const filteredRows = filterWorkflowOutlineRows(rows, outlineQuery, (nodeId) => {
    const node = nodeById.get(nodeId)
    if (!node) return ""
    const localized = localizeNodeText(
      getNodeDisplayId(node.data),
      { label: node.data.label, description: node.data.description },
      language,
    )
    return `${node.data.label} ${node.data.description ?? ""} ${localized.label} ${localized.description ?? ""} ${node.data.nodeType}`
  })
  const visibleRows = outlineQuery.trim()
    ? filteredRows
    : visibleWorkflowOutlineRows(filteredRows, collapsedNodeIds)
  const connectedRows = visibleRows.filter((row) => !row.disconnected)
  const disconnectedRows = visibleRows.filter((row) => row.disconnected)
  const toggleOutlineNode = (nodeId: string) => {
    setCollapsedNodeIds((current) => {
      const next = new Set(current)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }
  const renderRows = (sectionRows: typeof rows) => (
    <div role="tree" className="overflow-hidden rounded-[3px] border border-[#20242a] bg-[#101216]/84">
      {sectionRows.map((row) => {
        const node = nodeById.get(row.nodeId)
        if (!node) return null
        const projectNode = findWorkflowProjectNodeByCanvasId(workflowProject, node.id)
        const configurationNode = findImplementationNode(projectNode) ?? projectNode
        const nodeViewContract = buildCanonicalNodeViewContract(projectNode, node.data, node.id)
        const systemText = localizeNodeText(
          getNodeDisplayId(node.data),
          { label: node.data.label, description: node.data.description },
          language,
        )
        const prefersCustomLabel =
          projectNode?.ui?.preferCustomLabel === true || shouldPreserveNodeAuthoredText(node.data)
        const localized = prefersCustomLabel
          ? { label: node.data.label, description: node.data.description }
          : systemText
        const displayLabel = businessLevel && !prefersCustomLabel
          ? businessNodeName({
              label: localized.label,
              kind: nodeViewContract.identity.kind as WorkflowNodeKind,
              capability: nodeViewContract.identity.capability as WorkflowCapability,
              params: configurationNode?.params ?? readCanonical(node.data)?.params,
              language,
            })
          : localized.label
        const collapsed = collapsedNodeIds.has(node.id)
        const status = node.data.status ?? "idle"
        const rowIndex = outlineRowIndexById.get(node.id)
        const hasChildren = rowIndex !== undefined && workflowOutlineRowHasChildren(rows, rowIndex)
        return (
          <div
            key={node.id}
            role="treeitem"
            aria-level={row.depth + 1}
            aria-selected={selectedNodeId === node.id}
            style={{ paddingLeft: 12 + row.depth * 16 }}
            className={cn(
              "flex w-full items-stretch border-b border-[#20242a] pr-1 text-left transition-colors last:border-b-0 hover:bg-[#1b1f25]",
              selectedNodeId === node.id && "bg-[#20252c] text-foreground",
            )}
          >
            {hasChildren ? (
              <button
                type="button"
                aria-label={collapsed ? copy.expandNode : copy.collapseNode}
                aria-expanded={!collapsed}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleOutlineNode(node.id)
                }}
                className="flex w-6 shrink-0 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
              >
                <ChevronRight className={cn("size-3 transition-transform", !collapsed && "rotate-90")} />
              </button>
            ) : (
              <span className="w-6 shrink-0" aria-hidden />
            )}
            <button
              type="button"
              onClick={() => onSelectNode(node.id)}
              onDoubleClick={() => onOpenNode(node.id)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return
                event.preventDefault()
                onOpenNode(node.id)
              }}
              className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-2 text-left"
            >
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full border",
                  workflowStatusDotClass[status] ?? workflowStatusDotClass.idle,
                )}
                title={workflowStatusText[status] ?? status}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] font-medium text-foreground">{displayLabel}</span>
                <span className="block truncate font-mono text-[9px] text-muted-foreground">
                  {row.branchLabel ? `${row.branchLabel} · ` : ""}{node.data.nodeType}
                </span>
              </span>
            </button>
          </div>
        )
      })}
    </div>
  )

  return (
    <PanelShell compact={compact} title={title} typeLine="WORKFLOW::OUTLINE · V1.0" onClose={onClose}>
      <div className="space-y-4 p-4">
        <InspectorLanguageToggle language={language} onChange={onLanguageChange} />
        <InspectorModeTabs
          active="outline"
          copy={copy}
          hasSelection={Boolean(selectedNodeId)}
          onChange={onModeChange}
        />
        <div className="rounded-[3px] border border-[#20242a] bg-[#101216]/84 p-3">
          <p className="text-[11px] leading-relaxed text-muted-foreground">{copy.outlineHelp}</p>
          <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/70">
            {nodes.length} {copy.nodes} · {edges.length} {copy.connections}
          </p>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            data-testid="workflow-outline-search"
            value={outlineQuery}
            onChange={(event) => setOutlineQuery(event.target.value)}
            placeholder={copy.outlineSearch}
            aria-label={copy.outlineSearch}
            className={cn(houdiniInputClass, "w-full pl-8")}
          />
        </div>
        {nodes.length === 0 ? (
          <p className="rounded-[3px] border border-dashed border-[#2a3038] p-4 text-[11px] text-muted-foreground">
            {copy.noNodes}
          </p>
        ) : null}
        {connectedRows.length > 0 ? (
          <section className="space-y-2">
            <div className="flex items-center gap-2">
              <GitBranch className="size-3 text-muted-foreground" />
              <SectionCaption>{copy.connectedFlow}</SectionCaption>
            </div>
            {renderRows(connectedRows)}
          </section>
        ) : null}
        {disconnectedRows.length > 0 ? (
          <section className="space-y-2">
            <div className="flex items-center gap-2">
              <Unplug className="size-3 text-[#f59e0b]" />
              <SectionCaption>{copy.needsAttention}</SectionCaption>
            </div>
            {renderRows(disconnectedRows)}
          </section>
        ) : null}
      </div>
    </PanelShell>
  )
}

type ProjectNodeWithIdentity = {
  params: Record<string, unknown>
  ui?: Record<string, unknown>
}

function hydrateProjectNodeIdentity<T extends ProjectNodeWithIdentity>(
  projectNode: T | undefined,
  data: WorkflowNodeData,
): T | undefined {
  const canonical = readCanonical(data)
  if (!projectNode || !canonical) return projectNode
  const catalogId = typeof projectNode.ui?.catalogId === "string" ? projectNode.ui.catalogId : canonical.catalogId
  const params = canonical.params ? { ...canonical.params, ...projectNode.params } : projectNode.params
  return {
    ...projectNode,
    params,
    ui: {
      ...projectNode.ui,
      ...(catalogId ? { catalogId } : {}),
    },
  }
}

type CanonicalNodeData = {
  catalogId?: string
  params?: Record<string, unknown>
}

function readCanonical(data: WorkflowNodeData): CanonicalNodeData | undefined {
  const canonical = data.canonical
  if (!canonical || typeof canonical !== "object" || Array.isArray(canonical)) return undefined
  return canonical as CanonicalNodeData
}

function nodeParameterDisplayValue(value: unknown): string | undefined {
  if (typeof value === "string") return value.trim() ? value : undefined
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  return undefined
}

const UNBOUND_INPUT_VALUE = "__opencli_unbound__"
const UNBOUND_SOURCE_BINDING_VALUE = "__opencli_source_binding_unbound__"
const UNBOUND_SOURCE_BINDING_REVISION_VALUE = "__opencli_source_binding_revision_unbound__"

type SourceFleetPreflightEntry = {
  status: "ready" | "blocked" | "error"
  match?: WorkflowFleetCapabilityMatchResponse
  message?: string
}

const EMPTY_SOURCE_BINDING_REVISIONS: SourceBindingRevision[] = []

function inputBindingValue(nodeId: string, portId: string | null): string {
  return JSON.stringify([nodeId, portId])
}

function parseInputBindingValue(value: string): { nodeId: string; portId: string | null } | undefined {
  try {
    const parsed: unknown = JSON.parse(value)
    if (!Array.isArray(parsed) || typeof parsed[0] !== "string") return undefined
    if (parsed[1] !== null && typeof parsed[1] !== "string") return undefined
    return { nodeId: parsed[0], portId: parsed[1] }
  } catch {
    return undefined
  }
}

export function Inspector({ compact = false, onClose }: { compact?: boolean; onClose: () => void }) {
  const nodes = useFlowStore((s) => s.nodes)
  const edges = useFlowStore((s) => s.edges)
  const workflowProject = useFlowStore((s) => s.workflowProject)
  const networkStackLength = useFlowStore((s) => s.networkStack.length)
  const updateNodeData = useFlowStore((s) => s.updateNodeData)
  const updateEdgeData = useFlowStore((s) => s.updateEdgeData)
  const updateEdgeType = useFlowStore((s) => s.updateEdgeType)
  const toggleEdgeAnimated = useFlowStore((s) => s.toggleEdgeAnimated)
  const updateWorkflowNodeParams = useFlowStore((s) => s.updateWorkflowNodeParams)
  const updateParameterInterfaceField = useFlowStore((s) => s.updateParameterInterfaceField)
  const connectNodes = useFlowStore((s) => s.connectNodes)
  const onReconnect = useFlowStore((s) => s.onReconnect)
  const removeEdgesByIds = useFlowStore((s) => s.removeEdgesByIds)
  const takeSnapshot = useFlowStore((s) => s.takeSnapshot)
  const setNodes = useFlowStore((s) => s.setNodes)
  const onEdgesChange = useFlowStore((s) => s.onEdgesChange)
  const [nodeTab, setNodeTab] = useState<InspectorMode>("outline")
  const [parameterGroupTab, setParameterGroupTab] = useState("")
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({})
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({})
  const [pinnedNodeId, setPinnedNodeId] = useState<string>()
  const language = useSettingsStore((state) => state.language)
  const setLanguage = useSettingsStore((state) => state.set)
  const copy = INSPECTOR_COPY[language]
  const { getInternalNode, setCenter } = useReactFlow<WorkflowNode, WorkflowEdge>()
  const searchParams = useSearchParams()

  const canvasSelected = nodes.filter((n) => n.selected)
  const pinnedNode = pinnedNodeId ? nodes.find((node) => node.id === pinnedNodeId) : undefined
  const selected = pinnedNode ? [pinnedNode] : canvasSelected
  const selectedEdges = edges.filter((e) => e.selected)
  const selectedNodeId = selected.length === 1 ? selected[0].id : undefined

  useEffect(() => {
    if (pinnedNodeId && !pinnedNode) setPinnedNodeId(undefined)
  }, [pinnedNode, pinnedNodeId])

  const locateOutlineNode = (nodeId: string) => {
    const outlineNode = nodes.find((candidate) => candidate.id === nodeId)
    if (!outlineNode) return
    const internalNode = getInternalNode(nodeId)
    const position = internalNode?.internals.positionAbsolute ?? outlineNode.position
    const width = internalNode?.measured.width ?? outlineNode.measured?.width ?? 0
    const height = internalNode?.measured.height ?? outlineNode.measured?.height ?? 0
    void setCenter(position.x + width / 2, position.y + height / 2, { zoom: 1, duration: 300 })
  }
  const selectOutlineNode = (nodeId: string) => {
    if (pinnedNodeId) setPinnedNodeId(nodeId)
    setNodes((current) => current.map((candidate) => ({
      ...candidate,
      selected: candidate.id === nodeId,
    })))
    onEdgesChange(
      edges
        .filter((edge) => edge.selected)
        .map((edge) => ({ id: edge.id, type: "select" as const, selected: false })),
    )
    locateOutlineNode(nodeId)
  }

  /* ---- edge parameter interface ---- */
  if (selected.length === 0 && selectedEdges.length === 1) {
    const edge = selectedEdges[0]
    const edgeType = edge.type ?? "workflow"
    const mapping: GeneratedWorkflowEdgeMapping = edge.data?.mapping ?? {
      mode: "auto",
      fields: [],
      preserveRaw: true,
      compatible: true,
      conflicts: [],
    }
    const updateMapping = (patch: Partial<GeneratedWorkflowEdgeMapping>) => {
      updateEdgeData(edge.id, {
        mapping: {
          ...mapping,
          ...patch,
          preserveRaw: true,
        },
      })
    }
    const updateMappingTransform = (
      index: number,
      transform: string | undefined,
    ) => {
      updateMapping({
        fields: mapping.fields.map((field, fieldIndex) =>
          fieldIndex === index ? { ...field, transform } : field,
        ),
      })
    }
    return (
      <PanelShell
        compact={compact}
        title="Connection"
        typeLine={`EDGE::${edgeType.toUpperCase()}`}
        onClose={onClose}
      >
        <div className="space-y-4 p-4">
          <div className="space-y-1.5">
            <Label htmlFor="edge-label" className="font-mono text-[10px] uppercase tracking-wider">
              Label
            </Label>
            <Input
              id="edge-label"
              value={(edge.data?.label as string) ?? ""}
              onFocus={takeSnapshot}
              onChange={(e) => updateEdgeData(edge.id, { label: e.target.value })}
              placeholder="例如：成功 / 失败"
            />
          </div>

          <div className="space-y-3 rounded-md border bg-card p-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <SectionCaption>{copy.fieldMapping}</SectionCaption>
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {copy.fieldMappingGap}
                </p>
              </div>
              <span className="shrink-0 rounded-xs border border-ops-line bg-ops-raised px-2 py-1 font-mono text-3xs uppercase text-zinc-400">
                {mapping.mode}
              </span>
            </div>

            {mapping.fields.map((field, index) => (
              <div key={index} className="space-y-2 rounded-md border bg-background p-2">
                <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 font-mono text-2xs">
                  <span className="truncate rounded-xs border border-ops-line bg-ops-raised px-2 py-1.5 text-zinc-300" title={field.source}>
                    {field.source}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">→</span>
                  <span className="truncate rounded-xs border border-ops-line bg-ops-raised px-2 py-1.5 text-zinc-300" title={field.target}>
                    {field.target}
                  </span>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`edge-transform-${index}`} className="font-mono text-3xs uppercase tracking-wider text-zinc-500">
                    {copy.transform}
                  </Label>
                  <Input
                    id={`edge-transform-${index}`}
                    aria-label={`映射 ${index + 1} 转换`}
                    value={field.transform ?? ""}
                    onFocus={takeSnapshot}
                    onChange={(event) => updateMappingTransform(index, event.target.value || undefined)}
                    placeholder={language === "zh-CN" ? "可选转换表达式" : "Optional transform expression"}
                    className={houdiniInputClass}
                  />
                </div>
              </div>
            ))}

            {mapping.fields.length === 0 ? (
              <p className="rounded-xs border border-dashed border-ops-line p-3 text-2xs leading-relaxed text-zinc-500">
                {copy.fieldMappingGap}
              </p>
            ) : (
              <p className="font-mono text-3xs uppercase tracking-wider text-zinc-500">
                {copy.legacyMapping} · {mapping.fields.length}
              </p>
            )}

            <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
              <span className="text-muted-foreground">{copy.compatibility}</span>
              <span className={mapping.compatible ? "text-success" : "text-destructive"}>
                {mapping.compatible ? copy.compilable : copy.blocked}
              </span>
            </div>
            {mapping.conflicts.map((conflict) => (
              <p key={conflict} className="text-[11px] leading-relaxed text-destructive">
                {conflict}
              </p>
            ))}
          </div>

          <div className="space-y-1.5">
            <Label className="font-mono text-[10px] uppercase tracking-wider">Type</Label>
            <Select value={edgeType} onValueChange={(v) => v && updateEdgeType(edge.id, v)}>
              <SelectTrigger>
                <SelectValue>
                  {(value: string | null) => edgeTypeOptions.find((o) => o.value === value)?.label ?? value}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {edgeTypeOptions.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-[11px] leading-relaxed text-muted-foreground">{edgeTypeHints[edgeType]}</p>
          </div>

          <Separator />

          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label htmlFor="edge-anim" className="font-mono text-[10px] uppercase tracking-wider">
                Flow Animation
              </Label>
              <p className="text-[11px] text-muted-foreground">显示流向的虚线动画</p>
            </div>
            <input
              id="edge-anim"
              type="checkbox"
              checked={!!edge.animated}
              onChange={() => toggleEdgeAnimated(edge.id)}
              className="houdini-checkbox"
            />
          </div>

          <Separator />
          <div className="space-y-1.5 rounded-md border bg-card p-3">
            <SectionCaption>Debug</SectionCaption>
            <MonoRow k="id" v={edge.id} />
            <MonoRow k="wire" v={`${edge.source} → ${edge.target}`} />
          </div>
        </div>
      </PanelShell>
    )
  }

  if (selected.length !== 1 || nodeTab === "outline") {
    return (
      <WorkflowOutlinePanel
        businessLevel={networkStackLength === 0}
        compact={compact}
        edges={edges}
        language={language}
        nodes={nodes}
        onClose={onClose}
        onLanguageChange={(nextLanguage) => setLanguage("language", nextLanguage)}
        onModeChange={setNodeTab}
        onOpenNode={(nodeId) => {
          selectOutlineNode(nodeId)
          setNodeTab("config")
        }}
        onSelectNode={selectOutlineNode}
        selectedNodeId={selectedNodeId}
        title={workflowProject.name}
        workflowProject={workflowProject}
      />
    )
  }

  /* ---- node parameter interface ---- */
  const node = selected[0]
  const data = node.data as WorkflowNodeData
  const canonical = data.canonical as { kind?: string; capability?: string; adapter?: string; params?: Record<string, unknown> } | undefined
  const displayId = getNodeDisplayId(data)
  const imageStudioParams = canonical?.params
  const canvasDocumentId = typeof imageStudioParams?.canvasDocumentId === "string"
    ? imageStudioParams.canvasDocumentId
    : ""
  const imageStudioQuery = new URLSearchParams({
    workspace: searchParams.get("workspace") ?? "",
    project: searchParams.get("project") ?? "",
    workflow: searchParams.get("workflow") ?? workflowProject.id,
    node: node.id,
    document: canvasDocumentId,
  })
  const imageStudioHref = `/studio/workflow/image?${imageStudioQuery.toString()}`
  const imageAssetHref = `${imageStudioHref}&mode=gallery`
  const configuredSnapshotId = typeof imageStudioParams?.snapshotId === "string"
    ? imageStudioParams.snapshotId
    : undefined
  const imageSnapshotId = data.imageStudioSummary?.snapshotId ?? configuredSnapshotId ?? "draft"
  const semantic = data.semantic && typeof data.semantic === "object"
    ? data.semantic as SemanticLinkMeta
    : undefined
  const weight = typeof data.weight === "number" ? data.weight : undefined
  const evidenceBatches = data.runtimeEvidenceBatches ?? []
  const evidenceItemCount = evidenceBatches.reduce((total, batch) => total + batch.itemCount, 0)
  const evidenceRecordCount = evidenceBatches.reduce((total, batch) => total + batch.recordCount, 0)
  const evidenceState = evidenceBatches.some((batch) => batch.status === "blocked" || batch.status === "failed")
    ? "blocked"
    : evidenceBatches.length > 0
      ? "ready"
      : undefined
  const projectNode = hydrateProjectNodeIdentity(
    findWorkflowProjectNodeByCanvasId(workflowProject, node.id),
    data,
  )
  const implementationNode = findImplementationNode(projectNode)
  const configurationNode = implementationNode ?? projectNode
  const configurationNodeId = implementationNode
    ? `${node.id}__${implementationNode.id}`
    : node.id
  const projectAdapter = configurationNode?.adapter
    ? workflowProject.adapters.find((candidate) => candidate.id === configurationNode.adapter)
    : undefined
  const nodeTemplate = getNodeTemplate(configurationNode)
  const nodeViewContract = buildCanonicalNodeViewContract(projectNode, data, node.id)
  const isBusinessLevel = networkStackLength === 0
  const localizedSystemText = localizeNodeText(
    getNodeDisplayId(data),
    { label: data.label, description: data.description },
    language,
  )
  const prefersCustomLabel =
    projectNode?.ui?.preferCustomLabel === true || shouldPreserveNodeAuthoredText(data)
  const localizedNodeText = prefersCustomLabel
    ? { label: data.label, description: data.description }
    : localizedSystemText
  const businessLabel = prefersCustomLabel
    ? localizedNodeText.label
    : businessNodeName({
        label: localizedNodeText.label,
        kind: nodeViewContract.identity.kind as WorkflowNodeKind,
        capability: nodeViewContract.identity.capability as WorkflowCapability,
        params: configurationNode?.params ?? canonical?.params,
        language,
      })
  const parameterInterfaceView = buildParameterInterfaceView({
    node: configurationNode,
    adapter: projectAdapter,
    nodes,
    allowedParamIds: implementationNode
      ? undefined
      : [
          ...nodeViewContract.params.map((param) => param.id),
          ...(configurationNode?.parameterInterface?.fields.map((field) => field.id) ?? []),
        ],
    runtimeCapability: data.runtimeCapability,
  })
  const nodeInternals = nodeViewContract.internals
  const nodeContract = nodeViewContract.staticContract
  const promptCapable =
    canonical?.kind === "agent" ||
    typeof data.primitiveId === "string" && (data.primitiveId.includes("prompt") || data.primitiveId.includes("model"))
  const promptParameter = (id: string) =>
    projectNode?.params[id] ?? canonical?.params?.[id] ?? data.fields?.find((field) => field.id === id)?.value
  const promptConfiguration: Array<{ key: string; value: string }> = [
    { key: "preset", value: promptParameter("style") },
    { key: "version", value: promptParameter("promptVersion") ?? promptParameter("version") },
    { key: "model", value: promptParameter("model") },
  ].flatMap(({ key, value }) => {
    const displayValue = nodeParameterDisplayValue(value)
    return displayValue ? [{ key, value: displayValue }] : []
  })
  const configuredPrompt = nodeParameterDisplayValue(promptParameter("prompt") ?? promptParameter("systemPrompt"))
  const testInput = nodeParameterDisplayValue(promptParameter("input"))
  const expectedOutput = nodeParameterDisplayValue(promptParameter("expected"))

  const update = (patch: Partial<WorkflowNodeData>) => updateNodeData(node.id, patch)

  const updateField = (fieldId: string, value: string) => {
    const fields = (data.fields ?? []).map((f: FieldConfig) =>
      f.id === fieldId ? { ...f, value } : f,
    )
    update({ fields })
  }

  const updateParameterField = (field: ParameterInterfaceViewField, value: unknown) => {
    if (field.readonly) return
    if (field.binding.source === "params" && field.binding.fieldId === "operatorId") {
      const configFields = parameterInterfaceView?.fields.filter(
        (candidate) => candidate.binding.source === "params" && candidate.binding.fieldId.startsWith("config."),
      ) ?? []
      for (const configField of configFields) {
        setJsonDrafts((drafts) => clearParameterDraftEntry(drafts, configurationNodeId, configField.id))
        setJsonErrors((errors) => clearParameterDraftEntry(errors, configurationNodeId, configField.id))
      }
    }
    if (parameterInterfaceView?.mode === "template") {
      if (field.binding.source === "adapter") {
        if (field.binding.fieldId === "mode") {
          updateWorkflowNodeParams(configurationNodeId, {}, { mode: value as never })
          return
        }
        updateWorkflowNodeParams(configurationNodeId, {}, { config: { [field.binding.fieldId]: value } })
        return
      }
      if (field.binding.source === "data") {
        update({ [field.binding.fieldId]: value } as Partial<WorkflowNodeData>)
        return
      }
      updateWorkflowNodeParams(configurationNodeId, { [field.binding.fieldId]: value })
      return
    }
    updateParameterInterfaceField(configurationNodeId, field.id, value)
  }

  const renderParameterField = (field: ParameterInterfaceViewField) => {
    const raw = field.value
    const fieldId = `parameter-${field.id}`
    const fieldText = localizeNodeParameterText(
      field.binding.fieldId,
      { label: field.label, description: field.description },
      language,
    )
    const label = (
      <div className="min-w-0 pt-1 text-right">
        <Label
          htmlFor={fieldId}
          title={fieldText.description}
          className="block truncate font-mono text-[10px] uppercase tracking-[0.04em] text-muted-foreground"
        >
          {fieldText.label}
        </Label>
      </div>
    )
    const readonlyTone = field.readonly ? "opacity-70" : ""
    const row = (control: React.ReactNode, align = "items-start") => (
      <div
        key={field.id}
        className={cn(
          "grid grid-cols-[118px_minmax(0,1fr)] gap-3 border-b border-[#24282f] px-1 py-2 last:border-b-0",
          align,
          readonlyTone,
        )}
      >
        {label}
        <div className="min-w-0">{control}</div>
      </div>
    )
    const variableSelector = !field.readonly && upstreamVariableOptions.length > 0 ? (
      <Select onValueChange={(value) => value && updateParameterField(field, value)}>
        <SelectTrigger
          data-testid="parameter-variable-selector"
          aria-label={`${copy.insertVariable}: ${fieldText.label}`}
          className={cn(houdiniSelectTriggerClass, "w-full")}
        >
          <SelectValue>
            {(value: string | null) =>
              value ? (upstreamVariableOptions.find((option) => option.value === value)?.label ?? value) : copy.insertVariable
            }
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="rounded-[2px] border border-[#2c3036] bg-[#0d0f12] font-mono text-[11px]">
          {upstreamVariableOptions.map((option) => (
            <SelectItem key={option.value} value={option.value} className="rounded-[2px] text-[11px]">
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    ) : null

    if (field.type === "json") {
      const draftKey = `${configurationNodeId}:${field.id}`
      const value = jsonDrafts[draftKey] ?? formatJsonParameterValue(raw)
      const error = jsonErrors[draftKey]
      return row(
        <div className="space-y-1">
          <Textarea
            id={fieldId}
            rows={5}
            readOnly={field.readonly}
            className={cn(houdiniTextareaClass, error && "border-[#f87171]/70")}
            value={value}
            onChange={(event) => {
              const next = event.target.value
              setJsonDrafts((drafts) => ({ ...drafts, [draftKey]: next }))
              const parsed = parseJsonParameterValue(next)
              if (!parsed.ok) {
                setJsonErrors((errors) => ({ ...errors, [draftKey]: parsed.error }))
                return
              }
              setJsonErrors((errors) => {
                const updated = { ...errors }
                delete updated[draftKey]
                return updated
              })
              updateParameterField(field, parsed.value)
            }}
            onBlur={() => {
              if (jsonErrors[draftKey]) return
              setJsonDrafts((drafts) => {
                const updated = { ...drafts }
                delete updated[draftKey]
                return updated
              })
            }}
          />
          {error ? <p role="alert" className="font-mono text-[10px] text-[#f87171]">{error}</p> : null}
        </div>,
      )
    }

    if (field.type === "boolean") {
      const checked = raw === true || raw === "true"
      return row(
        <div className="flex h-7 items-center">
          <input
            id={fieldId}
            type="checkbox"
            checked={checked}
            disabled={field.readonly}
            onChange={(event) => updateParameterField(field, event.target.checked)}
            className="houdini-checkbox"
          />
        </div>,
        "items-center",
      )
    }

    if (field.type === "select") {
      const value = typeof raw === "string" ? raw : field.options?.[0]?.value
      const selectedLabel = field.options?.find((option) => option.value === value)?.label ?? value ?? ""
      return row(
        field.readonly ? (
          <Input
            id={fieldId}
            readOnly
            value={selectedLabel}
            className={houdiniInputClass}
          />
        ) : (
          <Select value={value} onValueChange={(next) => updateParameterField(field, next)}>
            <SelectTrigger id={fieldId} className={houdiniSelectTriggerClass}>
              <span className="min-w-0 flex-1 truncate text-left">{selectedLabel}</span>
            </SelectTrigger>
            <SelectContent className="rounded-[2px] border border-[#2c3036] bg-[#0d0f12] font-mono text-[11px]">
              {(field.options ?? []).map((option) => (
                <SelectItem key={option.value} value={option.value} className="rounded-[2px] text-[11px]">
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ),
      )
    }

    if (field.type === "textarea") {
      return row(
        <div className="space-y-1.5">
          {variableSelector}
          <Textarea
            id={fieldId}
            rows={3}
            readOnly={field.readonly}
            className={houdiniTextareaClass}
            value={typeof raw === "string" ? raw : ""}
            onChange={(e) => updateParameterField(field, e.target.value)}
          />
        </div>,
      )
    }

    if (field.type === "tokens") {
      const selectedValues = new Set(
        Array.isArray(raw)
          ? raw.filter((value): value is string => typeof value === "string")
          : typeof raw === "string" && raw
            ? raw.split(",").map((value) => value.trim()).filter(Boolean)
            : [],
      )
      const options = field.options ?? []
      const optionValues = new Set(options.map((option) => option.value))
      const visibleOptions = [
        ...options,
        ...Array.from(selectedValues)
          .filter((value) => !optionValues.has(value))
          .map((value) => ({ value, label: value })),
      ]
      return row(
        <div className="flex flex-wrap items-center gap-1">
          {visibleOptions.map((option) => {
            const selectedToken = selectedValues.has(option.value)
            return (
              <button
                key={option.value}
                type="button"
                disabled={field.readonly}
                onClick={() => {
                  const next = new Set(selectedValues)
                  if (next.has(option.value)) next.delete(option.value)
                  else next.add(option.value)
                  updateParameterField(field, Array.from(next))
                }}
                className={cn(
                  "h-6 rounded-[2px] border px-2 font-mono text-[10px] transition-colors disabled:pointer-events-none disabled:opacity-60",
                  selectedToken
                    ? "border-[#8694a5] bg-[#2b3138] text-foreground"
                    : "border-[#2c3036] bg-[#07080a] text-muted-foreground hover:border-[#4a515c] hover:text-foreground",
                )}
              >
                {option.label}
              </button>
            )
          })}
          {field.allowCustom ? (
            <Input
              disabled={field.readonly}
              placeholder="Add value"
              className={cn(houdiniInputClass, "h-6 w-28 px-2 text-[10px]")}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== ",") return
                event.preventDefault()
                const value = event.currentTarget.value.trim().replace(/,$/, "")
                if (!value) return
                updateParameterField(field, [...selectedValues, value])
                event.currentTarget.value = ""
              }}
            />
          ) : null}
        </div>,
      )
    }

    if (field.type === "slider") {
      const value = typeof raw === "number" ? raw : Number(raw ?? field.min ?? 0)
      const safeValue = Number.isFinite(value) ? value : field.min ?? 0
      return row(
        <div className="flex h-7 items-center gap-2">
          <div className="min-w-0 flex-1">
            <input
              id={fieldId}
              type="range"
              min={field.min ?? 0}
              max={field.max ?? 1}
              step={field.step ?? 0.01}
              value={safeValue}
              disabled={field.readonly}
              onChange={(e) => updateParameterField(field, Number(e.target.value))}
              className="houdini-range w-full disabled:opacity-60"
            />
          </div>
          <Input
            type="number"
            min={field.min ?? 0}
            max={field.max ?? 1}
            step={field.step ?? 0.01}
            value={safeValue}
            readOnly={field.readonly}
            onChange={(e) => updateParameterField(field, Number(e.target.value))}
            className={cn(houdiniInputClass, "h-7 w-[4.25rem] px-1.5 text-right")}
            aria-label={`${fieldText.label} numeric value`}
          />
        </div>,
        "items-center",
      )
    }

    if (field.type === "number") {
      const value =
        typeof raw === "number"
          ? raw
          : typeof raw === "string" && raw.trim()
            ? Number(raw)
            : undefined
      return row(
          <Input
            id={fieldId}
            type="number"
            min={field.min}
            max={field.max}
            step={field.step}
            readOnly={field.readonly}
            value={typeof value === "number" && Number.isFinite(value) ? value : field.optional ? "" : 0}
            onChange={(e) => {
              if (field.optional && !e.target.value) {
                updateParameterField(field, undefined)
                return
              }
              updateParameterField(field, Number(e.target.value))
            }}
            className={houdiniInputClass}
          />,
      )
    }

    return row(
      <div className="space-y-1.5">
        {variableSelector}
        <Input
          id={fieldId}
          value={typeof raw === "string" || typeof raw === "number" ? String(raw) : ""}
          placeholder={field.placeholder}
          readOnly={field.readonly}
          onChange={(e) => updateParameterField(field, e.target.value)}
          className={houdiniInputClass}
        />
      </div>,
    )
  }

  const isCondition = data.nodeType === "condition"
  const ports = nodeViewContract.ports.map((port) => ({
    name: port.id,
    dir: port.direction,
    type: port.type,
    description: port.description,
  }))
  const inputPorts = ports.filter((port) => port.dir === "input")
  const incomingEdgesForPort = (portName: string) => edges.filter((edge) =>
    edge.target === node.id &&
    (
      edge.targetHandle === portName ||
      (edge.targetHandle == null && inputPorts.length === 1)
    ),
  )
  const inputOptions = (targetPort: (typeof inputPorts)[number], currentEdge?: WorkflowEdge) =>
    nodes.flatMap((candidate) => {
      if (candidate.id === node.id) return []
      const candidateProjectNode = hydrateProjectNodeIdentity(
        findWorkflowProjectNodeByCanvasId(workflowProject, candidate.id),
        candidate.data,
      )
      const candidateContract = buildCanonicalNodeViewContract(
        candidateProjectNode,
        candidate.data,
        candidate.id,
      )
      const graphWithoutCurrent = currentEdge
        ? edges.filter((edge) => edge.id !== currentEdge.id)
        : edges
      if (wouldCreateCycle(graphWithoutCurrent, candidate.id, node.id)) return []
      const localized = localizeNodeText(
        getNodeDisplayId(candidate.data),
        { label: candidate.data.label, description: candidate.data.description },
        language,
      )
      return candidateContract.ports
        .filter((port) =>
          port.direction === "output" &&
          portTypesCompatible(port.type, targetPort.type),
        )
        .map((port) => ({
          value: inputBindingValue(candidate.id, port.id),
          label: `${localized.label} · ${port.id} (${port.type})`,
          nodeId: candidate.id,
          portId: port.id,
        }))
    })
  const upstreamNodeIds = workflowDirectUpstreamNodeIds(node.id, edges)
  const upstreamVariableOptions = Array.from(new Map(nodes.flatMap((candidate) => {
    if (!upstreamNodeIds.has(candidate.id)) return []
    const candidateProjectNode = hydrateProjectNodeIdentity(
      findWorkflowProjectNodeByCanvasId(workflowProject, candidate.id),
      candidate.data,
    )
    const candidateContract = buildCanonicalNodeViewContract(
      candidateProjectNode,
      candidate.data,
      candidate.id,
    )
    const localized = localizeNodeText(
      getNodeDisplayId(candidate.data),
      { label: candidate.data.label, description: candidate.data.description },
      language,
    )
    return candidateContract.ports
      .filter((port) => port.direction === "output")
      .flatMap((port) => {
        const value = workflowInputReferenceForPort(port.id)
        return value ? [{
          value,
          label: `${localized.label} · ${port.id} (${port.type})`,
        }] : []
      })
  }).map((option) => [option.value, option])).values())
  const parameterGroups = parameterInterfaceView?.groups ?? []
  const activeParameterGroupId = parameterGroups.some((group) => group.id === parameterGroupTab)
    ? parameterGroupTab
    : parameterGroups[0]?.id
  const activeParameterFields = parameterInterfaceView?.fields.filter((field) => field.groupId === activeParameterGroupId) ?? []
  const regularParameterFields = activeParameterFields.filter((field) => field.type !== "json")
  const advancedParameterFields = activeParameterFields.filter((field) => field.type === "json")
  const blockedAction = blockedActionViewForRuntime(data)
  const locateNode = () => {
    const internalNode = getInternalNode(node.id)
    const position = internalNode?.internals.positionAbsolute ?? node.position
    const width = internalNode?.measured.width ?? node.measured?.width ?? 0
    const height = internalNode?.measured.height ?? node.measured?.height ?? 0
    void setCenter(position.x + width / 2, position.y + height / 2, { zoom: 1, duration: 300 })
  }
  const openCLISources = isOpenCLISourceSlotArray(configurationNode?.params.sources)
    ? configurationNode.params.sources
    : undefined
  const collectorKind = collectorKindForCatalogId(configurationNode?.ui?.catalogId)
  const collectorParams = collectorKind && isCollectorNodeParams(
    configurationNode?.params,
    collectorKind,
  )
    ? configurationNode.params as CollectorNodeParams
    : undefined
  const publicParameterPanel = parameterInterfaceView ? (
    <section className="space-y-2 rounded-[3px] border border-[#35404b] bg-[#101216]/84 p-3">
      <div>
        <SectionCaption>{language === "zh-CN" ? "公开参数 / PARAMETERS" : "PUBLIC PARAMETERS"}</SectionCaption>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          {parameterInterfaceView.summary}
        </p>
      </div>
      <div className="overflow-hidden rounded-[3px] border border-[#20242a] bg-[#101216]/84">
        <div className="flex flex-wrap gap-0 border-b border-[#24282f] bg-[#1d2025] p-0 font-mono text-[10px] uppercase">
          {parameterGroups.map((group) => (
            <button
              key={group.id}
              type="button"
              onClick={() => setParameterGroupTab(group.id)}
              className={cn(
                "border-r border-[#2b3037] px-3 py-1.5 transition-colors",
                activeParameterGroupId === group.id
                  ? "bg-[#07080a] text-foreground"
                  : "text-muted-foreground hover:bg-[#252a31] hover:text-foreground",
              )}
            >
              {PARAMETER_GROUP_TEXT[group.label]?.[language] ?? group.label}
            </button>
          ))}
        </div>
        <div className="px-2 py-1">{regularParameterFields.map((field) => renderParameterField(field))}</div>
        {activeParameterFields.length === 0 ? (
          <p className="px-3 py-4 text-[11px] text-muted-foreground">{copy.noPublicParameters}</p>
        ) : null}
      </div>
    </section>
  ) : null
  return (
    <PanelShell
      compact={compact}
      title={isBusinessLevel ? businessLabel : data.label}
      typeLine={`${nodeViewContract.identity.kind}::${nodeViewContract.identity.capability}`.toUpperCase() + " · V1.0"}
      status={data.status}
      onClose={onClose}
      onLocate={locateNode}
      pinned={pinnedNodeId === node.id}
      onTogglePin={() => setPinnedNodeId((current) => current === node.id ? undefined : node.id)}
    >
      <div className="space-y-4 p-4">
        <InspectorLanguageToggle
          language={language}
          onChange={(nextLanguage) => setLanguage("language", nextLanguage)}
        />
        <InspectorModeTabs
          active={nodeTab}
          copy={copy}
          hasSelection
          onChange={setNodeTab}
        />

        {nodeTab === "run" ? (
          <div className="space-y-3">
            <SectionCaption>{copy.runResult}</SectionCaption>
            <div className="rounded-md border bg-card p-3 text-[11px] leading-relaxed text-muted-foreground">
              {copy.runHelp}
            </div>
            <MonoRow k="node" v={node.id} />
            {canonical?.capability ? <MonoRow k="capability" v={canonical.capability} /> : null}
            {canonical?.adapter ? <MonoRow k="adapter" v={canonical.adapter} /> : null}
            <MonoRow k="artifacts" v={nodeViewContract.outputs.artifacts.join(", ") || "none"} />
            <MonoRow k="batches" v={nodeViewContract.outputs.evidenceBatchCount} />
          </div>
        ) : nodeTab === "trace" ? (
          <div className="space-y-3">
            <SectionCaption>{copy.trace}</SectionCaption>
            <div className="rounded-md border bg-card p-3 text-[11px] leading-relaxed text-muted-foreground">
              {copy.traceHelp}
            </div>
            <MonoRow k="profile" v={workflowProject.profile} />
            {canonical?.kind ? <MonoRow k="kind" v={canonical.kind} /> : null}
            <MonoRow k="events" v={nodeViewContract.trace.events.join(", ") || "none"} />
            {nodeViewContract.trace.runId ? <MonoRow k="run" v={nodeViewContract.trace.runId} /> : null}
            {nodeViewContract.trace.traceId ? <MonoRow k="trace" v={nodeViewContract.trace.traceId} /> : null}
          </div>
        ) : (
          <>
        {publicParameterPanel}
        {configurationNode?.ui?.catalogId === "intelligence.sink.feishu-bitable" ? (
          <FeishuBitableTargetEditor
            params={(configurationNode.params ?? {}) as FeishuBitableTargetParams}
            onChange={(patch) => updateWorkflowNodeParams(configurationNodeId, patch)}
          />
        ) : null}
        <section className="space-y-3 rounded-md border bg-card p-3">
          <div>
            <SectionCaption>{copy.businessConfig}</SectionCaption>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {copy.businessHelp}
            </p>
            <p className="mt-1 font-mono text-3xs text-muted-foreground">
              {copy.systemNode}: {localizedSystemText.label}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-label" className="font-mono text-[10px] uppercase tracking-wider">
              {copy.nodeName}
            </Label>
            <Input
              id="node-label"
              value={data.label}
              onFocus={takeSnapshot}
              onChange={(event) => update({ label: event.target.value })}
              placeholder={copy.nodeNamePlaceholder}
              className={houdiniInputClass}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-desc" className="font-mono text-[10px] uppercase tracking-wider">
              {copy.nodeDescription}
            </Label>
            <Textarea
              id="node-desc"
              rows={2}
              value={data.description ?? ""}
              onFocus={takeSnapshot}
              onChange={(event) => update({ description: event.target.value })}
              placeholder={copy.nodeDescriptionPlaceholder}
              className={houdiniTextareaClass}
            />
          </div>
        </section>
        {displayId === IMAGE_GENERATION_CATALOG_ID || displayId === IMAGE_ASSET_CATALOG_ID ? (
          <section className="space-y-3 rounded-md border bg-card p-3">
            <div>
              <SectionCaption>{language === "zh-CN" ? "图片工作区 / IMAGE WORKSPACE" : "IMAGE WORKSPACE"}</SectionCaption>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                {language === "zh-CN"
                  ? "创作、选择资产和运行指标只在工作区中管理；节点画布保持为摘要。"
                  : "Authoring, asset selection, and run metrics are managed in the workspace while the canvas stays a summary."}
              </p>
            </div>
            {displayId === IMAGE_GENERATION_CATALOG_ID ? (
              <>
                <div className="grid grid-cols-2 gap-2">
                <MonoRow k="snapshot" v={imageSnapshotId} />
                  <MonoRow k="assets" v={data.imageStudioSummary?.recentAssetIds.length ?? 0} />
                  <MonoRow k="model" v={data.imageStudioSummary?.modelFingerprint ?? "unset"} />
                  <MonoRow k="document" v={canvasDocumentId || "new"} />
                </div>
                <Link href={imageStudioHref} className="inline-flex h-8 w-full items-center justify-center rounded-sm border border-border bg-background font-mono text-[10px] uppercase tracking-wide text-foreground transition-colors hover:bg-accent">
                  {canvasDocumentId ? "Open Image Studio" : "Create Image Canvas"}
                </Link>
              </>
            ) : (
              <>
                <MonoRow k="pinned-assets" v={Array.isArray(imageStudioParams?.assetIds) ? imageStudioParams.assetIds.length : 0} />
                <Link href={imageAssetHref} className="inline-flex h-8 w-full items-center justify-center rounded-sm border border-border bg-background font-mono text-[10px] uppercase tracking-wide text-foreground transition-colors hover:bg-accent">
                  Select Workspace Assets
                </Link>
              </>
            )}
          </section>
        ) : null}

        {data.sourceAnchor || data.runArtifact || data.topicCollapse || data.miniNetwork || evidenceBatches.length || semantic || weight !== undefined ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{language === "zh-CN" ? "运行与关系详情" : "RUN & RELATION DETAILS"}</span>
              <span className="text-[10px] normal-case tracking-normal">{evidenceBatches.length} batches</span>
            </summary>
            <div className="grid grid-cols-2 gap-2 border-t p-3">
              {data.sourceAnchor ? (
                <>
                  <MonoRow k="source-anchor" v={`${data.sourceAnchor.kind} · ${data.sourceAnchor.label}`} />
                  {data.sourceAnchor.runId ? <MonoRow k="source-run" v={data.sourceAnchor.runId} /> : null}
                  {data.sourceAnchor.artifactPath ? <MonoRow k="source-artifact" v={data.sourceAnchor.artifactPath} /> : null}
                  {data.sourceAnchor.href ? <MonoRow k="source-url" v={data.sourceAnchor.href} /> : null}
                  {data.sourceAnchor.selector ? <MonoRow k="source-selector" v={data.sourceAnchor.selector} /> : null}
                </>
              ) : null}
              {data.runArtifact ? (
                <>
                  <MonoRow k="runtime-run" v={data.runArtifact.runId} />
                  <MonoRow k="runtime-artifact" v={data.runArtifact.artifactPath} />
                  {data.runArtifact.apiPath ? <MonoRow k="runtime-api" v={data.runArtifact.apiPath} /> : null}
                </>
              ) : null}
              {data.topicCollapse ? (
                <>
                  <MonoRow k="topic-package" v={data.topicCollapse.mode} />
                  <MonoRow k="topic-nodes" v={data.topicCollapse.nodeCount} />
                </>
              ) : null}
              {data.miniNetwork ? <MonoRow k="network" v={`${data.miniNetwork.nodes} nodes · ${data.miniNetwork.edges} edges · ${data.miniNetwork.mode}`} /> : null}
              {evidenceBatches.length ? (
                <>
                  <MonoRow k="evidence-batches" v={evidenceBatches.length} />
                  <MonoRow k="evidence-items" v={evidenceItemCount} />
                  <MonoRow k="evidence-records" v={evidenceRecordCount} />
                  <MonoRow k="evidence-state" v={evidenceState ?? "unknown"} />
                </>
              ) : null}
              {semantic?.relationship ? <MonoRow k="relationship" v={semantic.relationship} /> : null}
              {typeof semantic?.confidence === "number" ? <MonoRow k="confidence" v={`${Math.round(Math.max(0, Math.min(1, semantic.confidence)) * 100)}%`} /> : null}
              {weight !== undefined ? <MonoRow k="weight" v={`${Math.round(Math.max(0, Math.min(1, weight)) * 100)}%`} /> : null}
            </div>
          </details>
        ) : null}

        {promptCapable ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{copy.promptSection}</span>
              <span className="truncate text-[10px] normal-case tracking-normal">
                {promptConfiguration.find((item) => item.key === "model")?.value ?? copy.systemNode}
              </span>
            </summary>
            <div className="space-y-3 border-t p-3">
              <p className="text-[11px] leading-relaxed text-muted-foreground">{copy.promptHelp}</p>
              {promptConfiguration.map(({ key, value }) => <MonoRow key={key} k={key} v={value} />)}
              {configuredPrompt ? (
                <div className="space-y-1.5">
                  <Label className="font-mono text-[10px] uppercase tracking-wider">{copy.configuredPrompt}</Label>
                  <Textarea readOnly rows={4} className="font-mono text-xs" value={configuredPrompt} />
                </div>
              ) : null}
              {testInput || expectedOutput ? <Separator /> : null}
              {testInput ? (
                <div className="space-y-1.5">
                  <Label className="font-mono text-[10px] uppercase tracking-wider">{copy.testInput}</Label>
                  <Textarea readOnly rows={3} className="font-mono text-xs" value={testInput} />
                </div>
              ) : null}
              {expectedOutput ? (
                <div className="space-y-1.5">
                  <Label className="font-mono text-[10px] uppercase tracking-wider">{copy.expectedOutput}</Label>
                  <Textarea readOnly rows={4} className="font-mono text-xs" value={expectedOutput} />
                </div>
              ) : null}
              {!configuredPrompt && !testInput && !expectedOutput ? (
                <p className="rounded-md border border-dashed bg-card p-3 text-[11px] leading-relaxed text-muted-foreground">
                  {copy.noPrompt}
                </p>
              ) : null}
            </div>
          </details>
        ) : null}

        {blockedAction ? (
          <div className="overflow-hidden rounded-[3px] border border-[#7f1d1d]/60 bg-[#180b0b]/70">
            <div className="flex items-center justify-between gap-3 border-b border-[#7f1d1d]/50 bg-[#2a1010]/72 px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <AlertTriangle className="size-3.5 shrink-0 text-[#f87171]" />
                <SectionCaption>{copy.blockedAction}</SectionCaption>
              </div>
              <Link
                href={blockedAction.href}
                className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[2px] border border-[#f87171]/35 bg-[#3a1515] px-2 font-mono text-[10px] uppercase tracking-[0.06em] text-[#fecaca] transition-colors hover:border-[#fecaca]/50 hover:bg-[#4a1717]"
              >
                <PlugZap className="size-3" />
                <span>{blockedAction.actionLabel}</span>
              </Link>
            </div>
            <div className="space-y-2 p-3">
              <p className="line-clamp-2 text-[11px] leading-relaxed text-[#fecaca]/85">{blockedAction.message}</p>
              {blockedAction.missingLabels.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {blockedAction.missingLabels.map((label) => (
                    <span
                      key={label}
                      className="rounded-[2px] border border-[#7f1d1d]/60 bg-[#120707] px-2 py-1 font-mono text-[10px] text-[#fecaca]/75"
                    >
                      {label}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {openCLISources ? (
          <OpenCLISourceEditor
            key={configurationNodeId}
            sources={openCLISources}
            language={language}
            onChange={(sources) => updateWorkflowNodeParams(configurationNodeId, { sources })}
          />
        ) : null}

        {collectorParams && collectorKind ? (
          <CollectorSourceEditor
            key={configurationNodeId}
            kind={collectorKind}
            params={collectorParams}
            language={language}
            onChange={(params) => updateWorkflowNodeParams(configurationNodeId, params)}
          />
        ) : null}

        <details className={houdiniDetailsClass} open>
          <summary className={houdiniSummaryClass}>
            <span>{copy.inputs}</span>
            <span className="text-3xs normal-case tracking-normal">{inputPorts.length} IN</span>
          </summary>
          <div className="space-y-3 border-t border-ops-line p-3">
            <p className="text-2xs leading-relaxed text-zinc-500">{copy.inputsHelp}</p>
            {inputPorts.map((port) => {
              const currentEdges = incomingEdgesForPort(port.name)
              const currentEdge = currentEdges[0]
              const options = inputOptions(port, currentEdge)
              const currentOption = currentEdge
                ? options.find((option) =>
                    option.nodeId === currentEdge.source &&
                    (
                      option.portId === currentEdge.sourceHandle ||
                      currentEdge.sourceHandle == null
                    ),
                  )
                : undefined
              const legacyValue = currentEdge ? `__opencli_legacy__${currentEdge.id}` : undefined
              const currentValue = currentOption?.value ?? legacyValue ?? UNBOUND_INPUT_VALUE
              return (
                <div key={port.name} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <Label className="truncate font-mono text-2xs text-zinc-200">{port.name}</Label>
                    <span className="shrink-0 font-mono text-3xs uppercase text-zinc-500">{port.type}</span>
                  </div>
                  <Select
                    value={currentValue}
                    onValueChange={(value) => {
                      if (!value) return
                      if (value === UNBOUND_INPUT_VALUE) {
                        if (currentEdges.length > 0) removeEdgesByIds(currentEdges.map((edge) => edge.id))
                        return
                      }
                      const binding = parseInputBindingValue(value)
                      if (!binding) return
                      const connection = {
                        source: binding.nodeId,
                        sourceHandle: binding.portId,
                        target: node.id,
                        targetHandle: port.name,
                      }
                      if (
                        currentEdge &&
                        currentEdge.source === connection.source &&
                        currentEdge.sourceHandle === connection.sourceHandle &&
                        currentEdge.targetHandle === connection.targetHandle
                      ) return
                      if (currentEdge) onReconnect(currentEdge, connection)
                      else connectNodes(connection)
                    }}
                  >
                    <SelectTrigger
                      aria-label={`${copy.inputs}: ${port.name}`}
                      className={houdiniSelectTriggerClass}
                    >
                      <SelectValue>
                        {(value: string | null) =>
                          !value || value === UNBOUND_INPUT_VALUE
                            ? copy.inputUnbound
                            : value === legacyValue && currentEdge
                              ? `${copy.legacyMapping} · ${currentEdge.source}:${currentEdge.sourceHandle ?? "default"}`
                              : (options.find((option) => option.value === value)?.label ?? value)
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent className="rounded-xs border border-ops-line bg-ops-raised font-mono text-2xs">
                      <SelectItem value={UNBOUND_INPUT_VALUE}>{copy.inputUnbound}</SelectItem>
                      {currentEdge && !currentOption && legacyValue ? (
                        <SelectItem value={legacyValue} disabled>
                          {copy.legacyMapping} · {currentEdge.source}:{currentEdge.sourceHandle ?? "default"}
                        </SelectItem>
                      ) : null}
                      {options.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {options.length === 0 ? (
                    <p className="text-3xs text-zinc-500">{copy.noCompatibleOutputs}</p>
                  ) : null}
                  {currentEdge && !currentOption ? (
                    <p className="text-3xs leading-relaxed text-signal-warning">
                      {language === "zh-CN"
                        ? `当前旧连接 ${currentEdge.source}:${currentEdge.sourceHandle ?? "default"} 未被输入契约识别。`
                        : `The legacy connection ${currentEdge.source}:${currentEdge.sourceHandle ?? "default"} is not recognized by the input contract.`}
                    </p>
                  ) : null}
                </div>
              )
            })}
            {inputPorts.length === 0 ? (
              <p className="text-2xs leading-relaxed text-signal-danger">{copy.noContract}</p>
            ) : null}
          </div>
        </details>

        {advancedParameterFields.length > 0 ? (
          <details className={houdiniDetailsClass} data-testid="advanced-parameter-fields">
            <summary className={houdiniSummaryClass}>
              <span>{copy.advanced}</span>
              <span className="text-[10px] normal-case tracking-normal">{advancedParameterFields.length}</span>
            </summary>
            <div className="border-t px-2 py-1">
              {advancedParameterFields.map((field) => renderParameterField(field))}
            </div>
          </details>
        ) : null}

        {nodeContract || data.runtimeContract ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{copy.contract}</span>
              <span className="truncate text-[10px] normal-case tracking-normal">
                {data.runtimeContract?.bindingId ?? nodeContract?.dataModel}
              </span>
            </summary>
            <div className="space-y-3 border-t p-3">
              <div className="space-y-1">
                <h3 className="text-xs font-medium text-foreground">{nodeContract?.title ?? nodeViewContract.identity.label}</h3>
                <p className="font-mono text-[10px] text-muted-foreground">
                  {data.runtimeContract?.status ?? nodeContract?.dataModel}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <MonoRow k="ports" v={nodeViewContract.ports.length} />
                <MonoRow k="params" v={nodeViewContract.params.length} />
              </div>
              <Separator />
              <div className="space-y-1.5">
                {nodeViewContract.params.slice(0, 4).map((param) => (
                  <div key={param.id} className="flex items-center justify-between gap-2 font-mono text-[10px]">
                    <span className="truncate text-foreground">{param.id}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {param.type ?? "runtime"}{param.required ? ` · ${copy.required}` : ""}
                    </span>
                  </div>
                ))}
              </div>
              {nodeContract?.assertions.length ? (
                <>
                  <Separator />
                  <div className="space-y-1">
                    {nodeContract.assertions.slice(0, 3).map((assertion) => (
                      <p key={assertion} className="line-clamp-1 text-[11px] text-muted-foreground">
                        {assertion}
                      </p>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          </details>
        ) : null}

        {nodeInternals ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{copy.internals}</span>
              <span className="text-[10px] normal-case tracking-normal">{nodeInternals.steps.length} {copy.steps}</span>
            </summary>
            <div className="space-y-3 border-t p-3">
              <div className="space-y-1">
                <h3 className="text-xs font-medium text-foreground">{nodeInternals.title}</h3>
                <p className="text-[11px] leading-relaxed text-muted-foreground">{nodeInternals.summary}</p>
              </div>
              <div className="space-y-2">
                {nodeInternals.steps.map((step, index) => (
                  <div key={step.id} className="rounded-[3px] border border-[#252a31] bg-[#090a0c]/70 p-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[10px] text-muted-foreground">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <p className="truncate text-xs font-medium text-foreground">{step.label}</p>
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{step.description}</p>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 rounded-sm border px-1.5 py-0.5 font-mono text-[9px]",
                          internalStatusClass[step.status],
                        )}
                      >
                        {internalStatusLabel[step.status]}
                      </span>
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2 font-mono text-[10px] text-muted-foreground/80">
                      <span>{step.capability}</span>
                      <span className="truncate">{step.evidence}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </details>
        ) : null}

        {isCondition || (!nodeTemplate && data.fields && data.fields.length > 0) ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{copy.advanced}</span>
              <span className="truncate text-[10px] normal-case tracking-normal">{data.label}</span>
            </summary>
            <div className="space-y-3 border-t p-3">
            {isCondition ? (
              <div className="space-y-1.5">
                <Label htmlFor="node-cond" className="font-mono text-[10px] uppercase tracking-wider">
                  Expression
                </Label>
                <Textarea
                  id="node-cond"
                  rows={2}
                  className={houdiniTextareaClass}
                  value={data.condition ?? ""}
                  onFocus={takeSnapshot}
                  onChange={(e) => update({ condition: e.target.value })}
                />
              </div>
            ) : null}

            {!nodeTemplate && data.fields && data.fields.length > 0
              ? data.fields.map((f: FieldConfig) => (
                  <div key={f.id} className="space-y-1.5">
                    <Label
                      htmlFor={`field-${f.id}`}
                      className="font-mono text-[10px] uppercase tracking-wider"
                    >
                      {f.label}
                    </Label>
                    <Input
                      id={`field-${f.id}`}
                      value={f.value}
                      onFocus={takeSnapshot}
                      onChange={(e) => updateField(f.id, e.target.value)}
                      className={houdiniInputClass}
                    />
                  </div>
                ))
              : null}
            </div>
          </details>
        ) : null}

        {data.nodeType !== "note" && data.nodeType !== "group" ? (
          <details className={houdiniDetailsClass}>
            <summary className={houdiniSummaryClass}>
              <span>{copy.interface}</span>
              <span className="text-[10px] normal-case tracking-normal">
                {ports.filter((port) => port.dir === "input").length} IN · {ports.filter((port) => port.dir === "output").length} OUT
              </span>
            </summary>
            <div className="space-y-1.5 border-t p-3">
              {ports.map((p) => (
                <div
                  key={`${p.dir}-${p.name}`}
                  className="flex items-center justify-between font-mono text-[11px]"
                >
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "size-1.5 rounded-[2px]",
                        p.dir === "input" ? "bg-[#a0c3ec]" : "bg-[#3a3d42]",
                      )}
                      aria-hidden
                    />
                    <span className="text-foreground">{p.name}</span>
                  </span>
                  <span className="text-muted-foreground/70">
                    {p.dir.toUpperCase()} · {p.type.toUpperCase()}
                  </span>
                </div>
              ))}
              {ports.length === 0 ? (
                <p className="text-[11px] leading-relaxed text-[#f87171]">
                  {copy.noContract}
                </p>
              ) : null}
            </div>
          </details>
        ) : null}

        <details className={houdiniDetailsClass}>
          <summary className={houdiniSummaryClass}>
            <span>{copy.debug}</span>
            <span className="truncate text-[10px] normal-case tracking-normal">{node.id}</span>
          </summary>
          <div className="space-y-1.5 border-t p-3">
            <MonoRow k="id" v={node.id} />
            <MonoRow k="pos" v={`${Math.round(node.position.x)}, ${Math.round(node.position.y)}`} />
            {node.parentId ? <MonoRow k="parent" v={node.parentId} /> : null}
          </div>
        </details>
          </>
        )}
      </div>
    </PanelShell>
  )
}

function findImplementationNode(node: WorkflowProjectNode | undefined): WorkflowProjectNode | undefined {
  if (!node) return undefined
  const operator = node.params.operator
  if (!operator || typeof operator !== "object" || Array.isArray(operator)) return undefined
  const implementationNodeId = (operator as Record<string, unknown>).implementationNodeId
  if (typeof implementationNodeId !== "string") return undefined
  return (node.internals?.nodes ?? []).find((candidate): candidate is WorkflowProjectNode => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return false
    return (candidate as { id?: unknown }).id === implementationNodeId
  })
}

function CollectorSourceEditor({
  kind,
  params,
  language,
  onChange,
}: {
  kind: "web" | "api" | "rss" | "cli"
  params: CollectorNodeParams
  language: WorkflowLanguage
  onChange: (params: CollectorNodeParams) => void
}) {
  const isZh = language === "zh-CN"
  const updateSource = (index: number, patch: Partial<CollectorSourceDefinition>) => {
    onChange({
      ...params,
      sources: params.sources.map((source, sourceIndex) => (
        sourceIndex === index
          ? { ...source, ...patch } as CollectorSourceDefinition
          : source
      )),
    })
  }
  const removeSource = (index: number) => {
    onChange({
      ...params,
      sources: params.sources.filter((_, sourceIndex) => sourceIndex !== index),
    })
  }
  const moveSource = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction
    if (nextIndex < 0 || nextIndex >= params.sources.length) return
    const sources = [...params.sources]
    const [source] = sources.splice(index, 1)
    sources.splice(nextIndex, 0, source)
    onChange({ ...params, sources })
  }
  const addSource = () => {
    const source = createCollectorSource(kind, `${kind}-${params.sources.length + 1}`)
    onChange({ ...params, sources: [...params.sources, source] })
  }
  const sourceTypeLabel = {
    web: isZh ? "网页" : "Web",
    api: "API",
    rss: "RSS",
    cli: "CLI",
  }[kind]
  const previewItems = collectorPreviewItems({ items: [] })

  return (
    <section className="space-y-3 rounded-md border border-ops-line bg-ops-panel p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <SectionCaption>{isZh ? `${sourceTypeLabel} 来源` : `${sourceTypeLabel} sources`}</SectionCaption>
          <p className="mt-1 text-2xs leading-relaxed text-zinc-400">
            {isZh
              ? "来源按顺序并行执行；凭证只能引用已保存的 credential。"
              : "Sources run in order-preserving parallel fan-out; credentials are references only."}
          </p>
        </div>
        <button
          type="button"
          onClick={addSource}
          className="inline-flex h-7 items-center gap-1 rounded-xs border border-ops-line px-2 font-mono text-2xs text-primary-400 hover:border-primary-500"
        >
          <Plus className="size-3" />
          {isZh ? "添加来源" : "Add source"}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled
          className="rounded-xs border border-ops-line px-2 py-1 font-mono text-3xs text-zinc-500 disabled:opacity-60"
        >
          {isZh ? "测试全部" : "Test all"}
        </button>
        <button
          type="button"
          disabled
          className="rounded-xs border border-ops-line px-2 py-1 font-mono text-3xs text-zinc-500 disabled:opacity-60"
        >
          {isZh ? "重试失败项" : "Retry failed"}
        </button>
        <span className="font-mono text-3xs text-zinc-500">
          publishedAt / fetchedAt
        </span>
      </div>

      {params.sources.map((source, index) => (
        <div key={source.sourceId} className="space-y-2 rounded-md border border-ops-line bg-ops-raised p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-2xs text-zinc-300">{source.sourceId}</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label={isZh ? "上移来源" : "Move source up"}
                disabled={index === 0}
                onClick={() => moveSource(index, -1)}
                className="rounded-xs border border-ops-line px-1.5 py-1 font-mono text-3xs text-zinc-400 disabled:opacity-40"
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={isZh ? "下移来源" : "Move source down"}
                disabled={index === params.sources.length - 1}
                onClick={() => moveSource(index, 1)}
                className="rounded-xs border border-ops-line px-1.5 py-1 font-mono text-3xs text-zinc-400 disabled:opacity-40"
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={isZh ? "移除来源" : "Remove source"}
                onClick={() => removeSource(index)}
                className="rounded-xs border border-ops-line px-1.5 py-1 text-signal-danger"
              >
                <Trash2 className="size-3" />
              </button>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="font-mono text-3xs uppercase tracking-wider text-zinc-500">
              {isZh ? "名称" : "Name"}
            </span>
            <Input
              value={source.name ?? ""}
              className={houdiniInputClass}
              onChange={(event) => updateSource(index, { name: event.target.value })}
            />
          </label>

          {source.kind === "web" || source.kind === "api" ? (
            <label className="block space-y-1">
              <span className="font-mono text-3xs uppercase tracking-wider text-zinc-500">URL</span>
              <Input
                value={source.url}
                className={houdiniInputClass}
                onChange={(event) => updateSource(index, { url: event.target.value })}
              />
            </label>
          ) : null}

          {source.kind === "rss" ? (
            <label className="block space-y-1">
              <span className="font-mono text-3xs uppercase tracking-wider text-zinc-500">Feed URL</span>
              <Input
                value={source.feedUrl}
                className={houdiniInputClass}
                onChange={(event) => updateSource(index, { feedUrl: event.target.value })}
              />
            </label>
          ) : null}

          {source.kind === "api" ? (
            <label className="block space-y-1">
              <span className="font-mono text-3xs uppercase tracking-wider text-zinc-500">
                {isZh ? "凭证引用（可选）" : "Credential reference (optional)"}
              </span>
              <Input
                value={source.credentialRef ?? ""}
                placeholder="credential://api-example"
                className={houdiniInputClass}
                onChange={(event) => updateSource(index, {
                  credentialRef: event.target.value || undefined,
                  credentialScheme: event.target.value
                    ? source.credentialScheme ?? "bearer"
                    : undefined,
                })}
              />
            </label>
          ) : null}

          {source.kind === "cli" ? (
            <label className="block space-y-1">
              <span className="font-mono text-3xs uppercase tracking-wider text-zinc-500">
                {isZh ? "已注册 adapterNodeId" : "Registered adapterNodeId"}
              </span>
              <Input
                value={source.adapterNodeId}
                className={houdiniInputClass}
                onChange={(event) => updateSource(index, { adapterNodeId: event.target.value })}
              />
              <p className="text-3xs leading-relaxed text-zinc-500">
                {isZh
                  ? "仅允许填写目录中的只读 adapter；不接受 Shell 或 commandLine。"
                  : "Only registered read adapters are allowed; Shell and commandLine are not accepted."}
              </p>
            </label>
          ) : null}
        </div>
      ))}

      {params.sources.length === 0 ? (
        <p className="rounded-xs border border-dashed border-ops-line p-3 text-2xs text-zinc-500">
          {isZh ? "尚未配置来源。" : "No sources configured."}
        </p>
      ) : null}
      {previewItems.length > 0 ? (
        <div className="space-y-1 border-t border-ops-line pt-2">
          {previewItems.map((item, index) => (
            <div key={index} className="font-mono text-3xs text-zinc-500">{String(item)}</div>
          ))}
        </div>
      ) : null}
    </section>
  )
}

function collectorPreviewItems(output: { items: unknown[] }) {
  return output.items.slice(0, 50)
}

function OpenCLISourceEditor({
  sources,
  language,
  onChange,
}: {
  sources: OpenCLISourceSlot[]
  language: WorkflowLanguage
  onChange: (sources: OpenCLISourceSlot[]) => void
}) {
  const copy = INSPECTOR_COPY[language]
  const searchParams = useSearchParams()
  const workspaceId = searchParams.get("workspace")
  const projectId = searchParams.get("project")
  const sourceCatalog = useSources({ enabled: true, limit: 100 })
  const sourceBindings = useProjectSourceBindings(workspaceId, projectId)
  const bindings = sourceBindings.data ?? []
  const registeredSources = (sourceCatalog.data?.data ?? [])
    .map(openCLISlotFromDataSource)
    .filter((source): source is OpenCLISourceSlot => Boolean(source))
  const selectedSourceKeys = new Set(sources.map(sourceSlotKey))
  const availableSources = registeredSources.filter((source) => !selectedSourceKeys.has(sourceSlotKey(source)))
  const businessQuery = sourceBusinessQuery(sources)
  const market = sourceMarket(sources)
  const [removedSource, setRemovedSource] = useState<{ source: OpenCLISourceSlot; index: number } | null>(null)
  const [sourceSearch, setSourceSearch] = useState("")
  const [preflightStatus, setPreflightStatus] = useState<"idle" | "running" | "ready">("idle")
  const [preflightResults, setPreflightResults] = useState<Record<string, SourceFleetPreflightEntry>>({})
  const normalizedSearch = sourceSearch.trim().toLowerCase()
  const visibleSources = sources
    .map((source, index) => ({ source, index }))
    .filter(({ source }) => (
      !normalizedSearch ||
      [source.label, source.site, source.command, source.sourceGroup]
        .some((value) => value.toLowerCase().includes(normalizedSearch))
    ))
  const groupedSources = Array.from(
    visibleSources.reduce((groups, entry) => {
      const group = entry.source.sourceGroup || entry.source.site || "other"
      groups.set(group, [...(groups.get(group) ?? []), entry])
      return groups
    }, new Map<string, typeof visibleSources>()),
  )
  const runnableCount = Object.values(preflightResults).filter((result) => result.status === "ready").length
  const blockedCount = Object.values(preflightResults).filter((result) => result.status !== "ready").length

  useEffect(() => {
    setPreflightStatus("idle")
    setPreflightResults({})
  }, [sources])

  const updateSource = (index: number, patch: Partial<OpenCLISourceSlot>) => {
    onChange(sources.map((source, sourceIndex) => (
      sourceIndex === index ? { ...source, ...patch } : source
    )))
  }

  const addSource = (sourceId: string | null) => {
    const source = availableSources.find((candidate) => candidate.id === sourceId)
    if (!source) return
    onChange([...sources, source])
  }

  const addContentSources = (contentType: string | null) => {
    if (!contentType) return
    const presets = contentType === "video"
      ? OPENCLI_SITUATION_SOURCES.filter((source) => source.sourceGroup?.startsWith("video-"))
      : ASHARE_OPENCLI_SOURCES.filter((source) => source.sourceGroup === contentType)
    const selectedKeys = new Set(sources.map(sourceSlotKey))
    const additions = presets.filter((source) => !selectedKeys.has(sourceSlotKey(source)))
    if (additions.length > 0) onChange([...sources, ...additions])
  }

  const removeSource = (index: number) => {
    setRemovedSource({ source: sources[index], index })
    onChange(sources.filter((_, sourceIndex) => sourceIndex !== index))
  }

  const restoreSource = () => {
    if (!removedSource) return
    const removedKey = sourceSlotKey(removedSource.source)
    if (sources.some((source) => sourceSlotKey(source) === removedKey)) {
      setRemovedSource(null)
      return
    }
    const restored = [...sources]
    restored.splice(Math.min(removedSource.index, restored.length), 0, removedSource.source)
    onChange(restored)
    setRemovedSource(null)
  }

  const runFleetPreflight = async () => {
    setPreflightStatus("running")
    const entries = await Promise.all(sources.map(async (source): Promise<[string, SourceFleetPreflightEntry]> => {
      if (workspaceId && projectId) {
        const binding = bindings.find((candidate) => candidate.id === source.sourceBindingId)
        if (!binding) {
          return [
            source.id,
            {
              status: "blocked",
              message: language === "zh-CN"
                ? "请先选择当前项目的 Source Binding。"
                : "Select a Source Binding from this project first.",
            },
          ]
        }
        if (binding.status !== "active") {
          return [
            source.id,
            {
              status: "blocked",
              message: language === "zh-CN"
                ? `Source Binding 当前为 ${binding.status}，不能运行。`
                : `The Source Binding is ${binding.status} and cannot run.`,
            },
          ]
        }
        if (!source.sourceBindingRevisionId || !source.sourceBindingRevisionNumber) {
          return [
            source.id,
            {
              status: "blocked",
              message: language === "zh-CN"
                ? "请显式固定一个 Binding Revision。"
                : "Pin an explicit Binding Revision first.",
            },
          ]
        }
      }
      try {
        const match = await matchWorkflowFleetCapability({
          adapterNodeId: source.adapterId,
          site: source.site,
          command: source.command,
        })
        return [
          source.id,
          {
            status: match.matched ? "ready" : "blocked",
            match,
          },
        ]
      } catch (error) {
        return [
          source.id,
          {
            status: "error",
            message: error instanceof Error ? error.message : "Fleet preflight failed",
          },
        ]
      }
    }))
    setPreflightResults(Object.fromEntries(entries))
    setPreflightStatus("ready")
  }

  return (
    <section
      data-testid="source-pool-editor"
      className="overflow-hidden rounded-[3px] border border-[#20242a] bg-[#101216]/84"
    >
      <div className="space-y-3 border-b border-[#24282f] bg-[#171a1f] p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <SectionCaption>{copy.dataSources}</SectionCaption>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {copy.sourceHelp}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <SourcePoolTour language={language} />
            <Link
              href="/providers"
              className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[2px] border border-[#343a43] px-2 text-[10px] text-muted-foreground transition-colors hover:border-[#5f6976] hover:text-foreground"
            >
              {copy.manageSources}
              <ExternalLink className="size-3" />
            </Link>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 rounded-[3px] border border-[#2a2f36] bg-[#0b0d10] px-2.5 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-[2px] bg-[#ff7a17]/12 text-[#ff9a4a]">
              <Database className="size-3.5" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium text-foreground">{sources.length} {copy.sources}</p>
              <p className="truncate text-[10px] text-muted-foreground">{copy.parallel}</p>
            </div>
          </div>
          <Select onValueChange={addSource} disabled={sourceCatalog.isLoading || availableSources.length === 0}>
            <SelectTrigger
              aria-label={copy.addConnectedSource}
              className="h-7 w-auto min-w-28 rounded-[2px] border-[#343a43] bg-[#111317] px-2 text-[11px] shadow-none focus:ring-0"
            >
              <Plus className="size-3" />
              <SelectValue>
                {(value: string | null) =>
                  value
                    ? (registeredSources.find((source) => source.id === value)?.label ?? value)
                    : sourceCatalog.isLoading
                      ? copy.loading
                      : copy.addSource
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {availableSources.map((source) => (
                <SelectItem key={source.id} value={source.id}>
                  {source.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-zinc-500" />
            <Input
              data-testid="source-pool-search"
              value={sourceSearch}
              onChange={(event) => setSourceSearch(event.target.value)}
              placeholder={language === "zh-CN" ? "搜索名称、站点、命令或分组" : "Search name, site, command, or group"}
              className="h-8 rounded-xs border-ops-line bg-ops-black pl-8 text-2xs focus-visible:ring-0"
            />
          </div>
          <Ripple
            amplitude={0.2}
            speed={0.8}
            wavelength={48}
            rings={2}
            decay={2.5}
            refraction={12}
            dispersion={0}
            shine={0.2}
            trigger="click"
            className="min-h-8 w-32 rounded-xs"
          >
            <button
              type="button"
              data-testid="source-pool-preflight"
              disabled={preflightStatus === "running" || sources.length === 0}
              onClick={() => void runFleetPreflight()}
              className="inline-flex min-h-8 w-full items-center justify-center gap-1.5 whitespace-nowrap rounded-xs border border-ops-line bg-ops-raised px-3 text-2xs text-zinc-300 transition-colors hover:border-ops-line-strong hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <PlugZap className="size-3.5" />
              {preflightStatus === "running"
                ? (language === "zh-CN" ? "预检中" : "Checking")
                : (language === "zh-CN" ? "Fleet 预检" : "Fleet preflight")}
            </button>
          </Ripple>
        </div>
        <p role="status" className="font-mono text-3xs text-zinc-500">
          {preflightStatus === "ready"
            ? (language === "zh-CN"
                ? `可运行 ${runnableCount} · 受阻 ${blockedCount}`
                : `${runnableCount} runnable · ${blockedCount} blocked`)
            : (language === "zh-CN"
                ? "预检只检查 Worker、site binding 与命令能力，不执行来源。"
                : "Preflight checks Workers, site bindings, and command capability without executing sources.")}
        </p>
        <div className="space-y-1.5">
          <Label className="text-[11px] font-medium text-foreground">{copy.contentType}</Label>
          <Select onValueChange={addContentSources}>
            <SelectTrigger
              aria-label={copy.addContent}
              className="h-8 rounded-[3px] border-[#303640] bg-[#080a0c] text-xs shadow-none focus:ring-0"
            >
              <Plus className="size-3" />
              <SelectValue>
                {(value: string | null) =>
                  value && value in copy.contentTypes
                    ? copy.contentTypes[value as keyof typeof copy.contentTypes]
                    : copy.addContent
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(copy.contentTypes).map(([value, label]) => (
                <SelectItem key={value} value={value}>{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            {language === "zh-CN"
              ? "市场范围只影响行情类卡片；新闻、社区和视频按来源卡片独立配置。"
              : "Market scope affects market cards only; news, social, and video are configured per source card."}
          </p>
        </div>
        {sourceCatalog.isError ? (
          <p className="text-[10px] leading-relaxed text-[#fca5a5]">
            {copy.sourceUnavailable}
          </p>
        ) : !sourceCatalog.isLoading && registeredSources.length === 0 ? (
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            {language === "zh-CN" ? `当前节点已有 ${sources.length} 个来源；` : `This node has ${sources.length} sources; `}{copy.noOtherSources}
          </p>
        ) : !sourceCatalog.isLoading && availableSources.length === 0 ? (
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            {copy.allSourcesAdded}
          </p>
        ) : null}
      </div>

      {businessQuery !== undefined || market !== undefined ? (
        <div className="grid gap-3 border-b border-[#24282f] bg-[#0d0f12] p-3">
          {businessQuery !== undefined ? (
            <div className="space-y-1.5">
              <Label htmlFor="source-business-query" className="text-[11px] font-medium text-foreground">
                {copy.collectionTopic}
              </Label>
              <Input
                id="source-business-query"
                value={businessQuery}
                onChange={(event) => onChange(updateSourceBusinessQuery(sources, event.target.value))}
                placeholder={copy.collectionTopicPlaceholder}
                className="h-8 rounded-[3px] border-[#303640] bg-[#080a0c] text-xs focus-visible:ring-0"
              />
              <p className="text-[10px] text-muted-foreground">{copy.collectionTopicHint}</p>
            </div>
          ) : null}
          {market !== undefined ? (
            <div className="space-y-1.5">
              <Label className="text-[11px] font-medium text-foreground">{copy.market}</Label>
              <Select value={market} onValueChange={(value) => value && onChange(updateSourceMarket(sources, value))}>
                <SelectTrigger className="h-8 rounded-[3px] border-[#303640] bg-[#080a0c] text-xs focus:ring-0">
                  <SelectValue>
                    {(value: string | null) => SOURCE_MARKET_OPTIONS.find((option) => option.value === value)?.label ?? value}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {!SOURCE_MARKET_OPTIONS.some((option) => option.value === market) ? (
                    <SelectItem value={market}>{market}</SelectItem>
                  ) : null}
                  {SOURCE_MARKET_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-2 p-2">
        {removedSource ? (
          <div role="status" className="flex items-center justify-between gap-3 rounded-[3px] border border-[#3a3327] bg-[#18140e] px-2.5 py-2 text-[10px] text-[#f7c77d]">
            <span className="truncate">{copy.removedSource}: {sourceCardLabel(removedSource.source, language)}</span>
            <button
              type="button"
              onClick={restoreSource}
              className="inline-flex h-6 shrink-0 items-center gap-1 rounded-[2px] border border-[#6b5230] px-2 font-medium transition-colors hover:border-[#f7c77d] hover:text-[#ffe4b5]"
            >
              <RotateCcw className="size-3" />
              {copy.undoRemove}
            </button>
          </div>
        ) : null}
        {visibleSources.length === 0 ? (
          <p className="rounded-xs border border-dashed border-ops-line p-3 text-center text-2xs text-zinc-500">
            {language === "zh-CN" ? "没有匹配的来源。" : "No matching sources."}
          </p>
        ) : null}
        {groupedSources.map(([sourceGroup, entries]) => {
          const groupType = sourceGroup.startsWith("video-") ? "video" : sourceGroup
          const groupLabel = groupType in copy.contentTypes
            ? copy.contentTypes[groupType as keyof typeof copy.contentTypes]
            : sourceGroup
          return (
            <details
              key={sourceGroup}
              open={normalizedSearch ? true : undefined}
              className="rounded-xs border border-ops-line bg-ops-black"
            >
              <summary
                data-testid="source-pool-group"
                className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 font-mono text-3xs uppercase tracking-wider text-zinc-400"
              >
                <span>{groupLabel}</span>
                <span>{entries.length}</span>
              </summary>
              <div className="space-y-2 border-t border-ops-line p-2">
                {entries.map(({ source, index }) => {
                  const businessArguments = sourceBusinessArguments(source)
                  const optionCount = businessArguments.length + (source.positionalArgs?.length ? 1 : 0)
                  const contentType = source.sourceGroup?.startsWith("video-") ? "video" : source.sourceGroup
                  const contentLabel = contentType && contentType in copy.contentTypes
                    ? copy.contentTypes[contentType as keyof typeof copy.contentTypes]
                    : (language === "zh-CN" ? "数据采集" : "Data collection")
                  const fleetResult = preflightResults[source.id]
                  const fleetLabel = fleetResult?.status === "ready"
                    ? "READY"
                    : fleetResult?.status === "blocked"
                      ? "BLOCKED"
                      : fleetResult?.status === "error"
                        ? "ERROR"
                        : null
                  return (
                    <details
                      key={source.id}
                      data-testid="source-pool-source-card"
                      className="group rounded-[3px] border border-[#252a31] bg-[#090a0c]/70 open:border-[#3a414c]"
                    >
                      <summary className="flex cursor-pointer list-none items-center gap-2 p-2.5">
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-[3px] border border-[#343a43] bg-[#15181d] font-mono text-[11px] font-semibold uppercase text-[#ff9a4a]">
                          {source.site.slice(0, 1)}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-foreground">{sourceCardLabel(source, language)}</p>
                          <p className="truncate text-[10px] text-muted-foreground">
                            {source.site} · {contentLabel}
                          </p>
                        </div>
                        {fleetLabel ? (
                          <span
                            className={cn(
                              "shrink-0 font-mono text-3xs",
                              fleetResult?.status === "ready" ? "text-signal-success" : "text-signal-danger",
                            )}
                          >
                            {fleetLabel}
                          </span>
                        ) : null}
                        <span className="shrink-0 text-[10px] text-muted-foreground">{copy.configureSource}</span>
                        <ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
                      </summary>
                      <div className="grid gap-2 border-t border-[#20242a] p-2.5">
                        <SourceBindingRevisionControls
                          source={source}
                          bindings={bindings}
                          workspaceId={workspaceId}
                          projectId={projectId}
                          loading={sourceBindings.isLoading}
                          failed={sourceBindings.isError}
                          language={language}
                          onChange={(patch) => updateSource(index, patch)}
                        />
                        {fleetResult && fleetResult.status !== "ready" ? (
                          <p className="rounded-xs border border-signal-danger/30 bg-signal-danger/10 p-2 text-2xs leading-relaxed text-signal-danger">
                            {fleetResult.message ||
                              fleetResult.match?.missing.join(" · ") ||
                              (language === "zh-CN" ? "没有匹配的可用 Worker。" : "No available Worker matched.")}
                          </p>
                        ) : fleetResult?.match?.selected ? (
                          <p className="font-mono text-3xs text-signal-success">
                            {fleetResult.match.selected.label} · {fleetResult.match.selected.endpoint}
                          </p>
                        ) : null}
                        {optionCount > 0 ? (
                          <>
                            <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                              {copy.collectionOptions} · {optionCount} {copy.items}
                            </p>
                            {source.positionalArgs?.length ? (
                              <div className="space-y-1">
                                <Label className="text-[10px] text-muted-foreground">{copy.positionalArgument}</Label>
                                <Input
                                  value={source.positionalArgs[0] ?? ""}
                                  onChange={(event) => updateSource(index, { positionalArgs: [event.target.value] })}
                                  className={houdiniInputClass}
                                />
                              </div>
                            ) : null}
                            {businessArguments.map(([key, value]) => (
                              <SourceBusinessArgument
                                key={key}
                                argumentKey={key}
                                value={value}
                                language={language}
                                onChange={(nextValue) => updateSource(index, { args: { ...source.args, [key]: nextValue } })}
                              />
                            ))}
                          </>
                        ) : (
                          <p className="text-[10px] text-muted-foreground">
                            {language === "zh-CN" ? "此来源没有必须填写的业务参数。" : "This source has no required business parameters."}
                          </p>
                        )}
                        <div className="flex justify-end border-t border-[#20242a] pt-2">
                          <button
                            type="button"
                            aria-label={`${copy.removeSource} ${source.label}`}
                            disabled={sources.length <= 1}
                            onClick={() => removeSource(index)}
                            className="inline-flex h-7 items-center gap-1.5 rounded-[2px] border border-[#4a2525] px-2 text-[10px] text-[#f87171] transition-colors hover:border-[#f87171] disabled:cursor-not-allowed disabled:opacity-30"
                          >
                            <Trash2 className="size-3" />
                            {copy.removeSource}
                          </button>
                        </div>
                      </div>
                    </details>
                  )
                })}
              </div>
            </details>
          )
        })}
      </div>

      <details className="border-t border-[#24282f] bg-[#111317]/74">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:text-foreground">
          <span>{copy.advanced}</span>
          <span className="normal-case tracking-normal">{copy.opencliMapping}</span>
        </summary>
        <div className="space-y-2 border-t border-[#24282f] p-2">
          {sources.map((source, index) => (
            <div key={source.id} className="space-y-2 rounded-[3px] border border-[#252a31] bg-[#090a0c]/70 p-2.5">
              <Input
                aria-label={`${language === "zh-CN" ? "来源" : "Source"} ${index + 1} ${copy.sourceName}`}
                value={source.label}
                onChange={(event) => updateSource(index, { label: event.target.value })}
                className={houdiniInputClass}
              />
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">{copy.site}</Label>
                  <Input
                    aria-label={`${language === "zh-CN" ? "来源" : "Source"} ${index + 1} ${copy.site}`}
                    value={source.site}
                    onChange={(event) => updateSource(index, { site: event.target.value })}
                    className={houdiniInputClass}
                  />
                </div>
                <div className="space-y-1">
                  <Label className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">{copy.command}</Label>
                  <Input
                    aria-label={`${language === "zh-CN" ? "来源" : "Source"} ${index + 1} ${copy.command}`}
                    value={source.command}
                    onChange={(event) => updateSource(index, { command: event.target.value })}
                    className={houdiniInputClass}
                  />
                </div>
              </div>
              <SourceArgsEditor
                sourceId={source.id}
                value={source.args}
                language={language}
                onCommit={(args) => updateSource(index, { args })}
              />
            </div>
          ))}
        </div>
      </details>
      <div className="border-t border-[#24282f] bg-[#0d0f12] px-3 py-2">
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          {copy.autoSaveHint}
        </p>
      </div>
    </section>
  )
}

function SourceBindingRevisionControls({
  source,
  bindings,
  workspaceId,
  projectId,
  loading,
  failed,
  language,
  onChange,
}: {
  source: OpenCLISourceSlot
  bindings: SourceBinding[]
  workspaceId: string | null
  projectId: string | null
  loading: boolean
  failed: boolean
  language: WorkflowLanguage
  onChange: (patch: Partial<OpenCLISourceSlot>) => void
}) {
  const selectedBinding = bindings.find((binding) => binding.id === source.sourceBindingId)
  const revisionsQuery = useProjectSourceBindingRevisions(
    workspaceId,
    projectId,
    source.sourceBindingId ?? null,
  )
  const revisions = revisionsQuery.data ?? EMPTY_SOURCE_BINDING_REVISIONS

  useEffect(() => {
    if (!selectedBinding || source.sourceBindingRevisionId || revisions.length === 0) return
    const currentRevision = revisions.find(
      (revision) => revision.revision_number === selectedBinding.current_revision_number,
    )
    if (!currentRevision) return
    onChange({
      sourceBindingRevisionId: currentRevision.id,
      sourceBindingRevisionNumber: currentRevision.revision_number,
    })
  }, [onChange, revisions, selectedBinding, source.sourceBindingRevisionId])

  if (!workspaceId || !projectId) {
    return (
      <p className="rounded-xs border border-dashed border-ops-line p-2 text-2xs text-zinc-500">
        {language === "zh-CN"
          ? "独立画布没有项目上下文，当前来源保持为未绑定草稿槽位。"
          : "Standalone canvas has no project context; this source remains an unbound draft slot."}
      </p>
    )
  }

  const selectedRevision = revisions.find((revision) => revision.id === source.sourceBindingRevisionId)
  const bindingState = selectedBinding
    ? `${selectedBinding.status.toUpperCase()} · R${source.sourceBindingRevisionNumber ?? selectedBinding.current_revision_number}`
    : (language === "zh-CN" ? "未绑定草稿" : "Unbound draft")

  return (
    <div className="space-y-2 rounded-xs border border-ops-line bg-ops-panel p-2">
      <div className="flex items-center justify-between gap-3">
        <p className="font-mono text-3xs uppercase tracking-wider text-zinc-500">Binding Revision</p>
        <span className={cn(
          "font-mono text-3xs",
          selectedBinding?.status === "active" ? "text-signal-success" : "text-zinc-500",
        )}>
          {bindingState}
        </span>
      </div>
      {failed ? (
        <p className="text-2xs text-signal-danger">
          {language === "zh-CN" ? "Project Source Bindings 加载失败。" : "Project Source Bindings failed to load."}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Select
            value={source.sourceBindingId ?? UNBOUND_SOURCE_BINDING_VALUE}
            disabled={loading}
            onValueChange={(value) => {
              if (!value || value === UNBOUND_SOURCE_BINDING_VALUE) {
                onChange({
                  sourceBindingId: undefined,
                  sourceBindingRevisionId: undefined,
                  sourceBindingRevisionNumber: undefined,
                })
                return
              }
              onChange({
                sourceBindingId: value,
                sourceBindingRevisionId: undefined,
                sourceBindingRevisionNumber: undefined,
              })
            }}
          >
            <SelectTrigger
              aria-label={language === "zh-CN" ? "Project Source Binding" : "Project Source Binding"}
              className="h-8 rounded-xs border-ops-line bg-ops-black text-2xs shadow-none focus:ring-0"
            >
              <SelectValue>
                {selectedBinding?.name ??
                  (source.sourceBindingId
                    ? (language === "zh-CN" ? "Binding 不可用" : "Binding unavailable")
                    : (language === "zh-CN" ? "未绑定草稿" : "Unbound draft"))}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNBOUND_SOURCE_BINDING_VALUE}>
                {language === "zh-CN" ? "未绑定草稿" : "Unbound draft"}
              </SelectItem>
              {bindings.map((binding) => (
                <SelectItem key={binding.id} value={binding.id}>
                  {binding.name} · {binding.status}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={source.sourceBindingRevisionId ?? UNBOUND_SOURCE_BINDING_REVISION_VALUE}
            disabled={!selectedBinding || revisionsQuery.isLoading || revisions.length === 0}
            onValueChange={(value) => {
              const revision = revisions.find((candidate) => candidate.id === value)
              if (!revision) return
              onChange({
                sourceBindingRevisionId: revision.id,
                sourceBindingRevisionNumber: revision.revision_number,
              })
            }}
          >
            <SelectTrigger
              aria-label="Source Binding Revision"
              className="h-8 rounded-xs border-ops-line bg-ops-black text-2xs shadow-none focus:ring-0"
            >
              <SelectValue>
                {source.sourceBindingRevisionNumber
                  ? `R${source.sourceBindingRevisionNumber}`
                  : (language === "zh-CN" ? "选择 Revision" : "Select revision")}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {!selectedRevision && source.sourceBindingRevisionId ? (
                <SelectItem value={source.sourceBindingRevisionId}>
                  {language === "zh-CN" ? "已固定的 Revision 不可用" : "Pinned revision unavailable"}
                </SelectItem>
              ) : null}
              <SelectItem value={UNBOUND_SOURCE_BINDING_REVISION_VALUE} disabled>
                {language === "zh-CN" ? "选择 Revision" : "Select revision"}
              </SelectItem>
              {revisions.map((revision) => (
                <SelectItem key={revision.id} value={revision.id}>
                  R{revision.revision_number}
                  {revision.revision_number === selectedBinding?.current_revision_number
                    ? (language === "zh-CN" ? " · 当前" : " · current")
                    : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}

function SourceBusinessArgument({
  argumentKey,
  value,
  language,
  onChange,
}: {
  argumentKey: string
  value: string | number | boolean
  language: WorkflowLanguage
  onChange: (value: string | number | boolean) => void
}) {
  const label = language === "zh-CN"
    ? SOURCE_ARGUMENT_LABELS[argumentKey] ?? argumentKey
    : argumentKey.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase())
  if (typeof value === "boolean") {
    return (
      <div className="flex items-center justify-between gap-3 rounded-[3px] border border-[#252a31] bg-[#0d0f12] px-2.5 py-2">
        <Label className="text-[11px] text-foreground">{label}</Label>
        <Switch checked={value} onCheckedChange={onChange} aria-label={label} />
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <Label className="text-[10px] text-muted-foreground">{label}</Label>
      <Input
        type={typeof value === "number" ? "number" : "text"}
        value={value}
        onChange={(event) => onChange(typeof value === "number" ? Number(event.target.value) : event.target.value)}
        className="h-7 rounded-[2px] border-[#2c3036] bg-[#07080a] px-2 text-[11px] focus-visible:ring-0"
      />
    </div>
  )
}

function SourceArgsEditor({
  sourceId,
  value,
  language,
  onCommit,
}: {
  sourceId: string
  value: Record<string, unknown>
  language: WorkflowLanguage
  onCommit: (value: Record<string, unknown>) => void
}) {
  const copy = INSPECTOR_COPY[language]
  const serialized = JSON.stringify(value, null, 2)
  const [draft, setDraft] = useState(serialized)
  const [error, setError] = useState("")

  useEffect(() => {
    setDraft(serialized)
    setError("")
  }, [serialized])

  const commit = () => {
    try {
      const parsed = JSON.parse(draft) as unknown
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setError(copy.jsonObjectRequired)
        return
      }
      setError("")
      onCommit(parsed as Record<string, unknown>)
    } catch {
      setError(copy.invalidJson)
    }
  }

  return (
    <div className="space-y-1">
      <Label htmlFor={`source-args-${sourceId}`} className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
        {copy.parameters}
      </Label>
      <Textarea
        id={`source-args-${sourceId}`}
        rows={3}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        className={cn(houdiniTextareaClass, error && "border-[#7f1d1d]")}
      />
      {error ? <p className="text-[10px] text-[#f87171]">{error}</p> : null}
    </div>
  )
}

function formatJsonParameterValue(value: unknown): string {
  if (!value || typeof value !== "object") return "{}"
  return JSON.stringify(value, null, 2)
}
