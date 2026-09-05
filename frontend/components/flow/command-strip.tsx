"use client"

import { useCallback, useEffect, useRef, useState, type RefObject } from "react"
import { useReactFlow, getNodesBounds, getViewportForBounds } from "@xyflow/react"
import { toPng } from "html-to-image"
import {
  Undo2,
  Redo2,
  Network,
  Save,
  FolderOpen,
  RotateCcw,
  ImageDown,
  ServerCog,
  Download,
  FileCode2,
  Upload,
  Link2,
  Eraser,
  Users,
  Settings,
  SlidersHorizontal,
  Play,
  BrainCircuit,
  ListTree,
  Magnet,
  Scissors,
  MoreHorizontal,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useFlowStore } from "@/lib/flow/store"
import { useSettingsStore } from "@/lib/flow/settings-store"
import { buildShareUrl } from "@/lib/flow/share-state"
import {
  exportReactFlowToWorkflowJson,
  exportReactFlowToWorkflowCanvas,
  exportReactFlowToWorkflowMermaid,
  exportReactFlowToWorkflowMarkdown,
  exportReactFlowToWorkflowOpml,
  importWorkflowJsonToReactFlowManaged,
  importWorkflowMermaidToReactFlow,
} from "@/lib/workflow/io"
import { cn } from "@/lib/utils"
import { MAX_WORKFLOW_NODE_DEPTH, workflowNodeDepthFromNetworkStack, workflowNodeLayerAtDepth } from "@/lib/workflow/node-hierarchy"
import { COLLECTION_WORKFLOW_PROJECT } from "@/lib/workflow/collection-pipeline"
import type { WorkflowWorkbenchMode } from "./workflow-workbench-panel"

function downloadText(filename: string, data: string, type: string) {
  const blob = new Blob([data], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.download = filename
  a.href = url
  a.click()
  URL.revokeObjectURL(url)
}

function IconAction({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string
  onClick?: () => void
  disabled?: boolean
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "size-11 text-muted-foreground hover:text-foreground",
              active && "bg-accent text-foreground",
            )}
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export function CommandStrip({
  onOpenPalette,
  onExported,
  collab,
  onToggleCollab,
  settingsOpen,
  onToggleSettings,
  projectSettingsOpen,
  onToggleProjectSettings,
  onRunWorkflow,
  runTraceOpen,
  onToggleRunTrace,
  nodeManagementOpen,
  onToggleNodeManagement,
  workbenchMode,
  onChangeWorkbenchMode,
  importInputRef,
  documentState,
  onSaveWorkflow,
}: {
  onOpenPalette: () => void
  onExported?: (msg: string) => void
  collab?: boolean
  onToggleCollab?: () => void
  settingsOpen?: boolean
  onToggleSettings?: () => void
  projectSettingsOpen?: boolean
  onToggleProjectSettings?: () => void
  onRunWorkflow: () => void
  runTraceOpen?: boolean
  onToggleRunTrace?: () => void
  nodeManagementOpen?: boolean
  onToggleNodeManagement?: () => void
  workbenchMode?: WorkflowWorkbenchMode | null
  onChangeWorkbenchMode?: (mode: WorkflowWorkbenchMode | null) => void
  importInputRef?: RefObject<HTMLInputElement | null>
  documentState?: "loading" | "saving" | "saved" | "error" | "conflict"
  onSaveWorkflow: () => void
}) {
  const localFileInputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = importInputRef ?? localFileInputRef
  const { getNodes } = useReactFlow()

  const undo = useFlowStore((s) => s.undo)
  const redo = useFlowStore((s) => s.redo)
  const canUndo = useFlowStore((s) => s.past.length > 0)
  const canRedo = useFlowStore((s) => s.future.length > 0)
  const nodeCount = useFlowStore((s) => s.nodes.length)
  const edgeCount = useFlowStore((s) => s.edges.length)
  const workflowProjectName = useFlowStore((s) => s.workflowProject.name)
  const networkStackLength = useFlowStore((s) => s.networkStack.length)
  const networkDepth = workflowNodeDepthFromNetworkStack(networkStackLength)
  const networkLayer = workflowNodeLayerAtDepth(networkDepth)
  const selectedNodeId = useFlowStore((s) => s.nodes.find((node) => node.selected)?.id)
  const selectedNodeCount = useFlowStore((s) => s.nodes.reduce((count, node) => count + (node.selected ? 1 : 0), 0))
  const selectedEdgeCount = useFlowStore((s) => s.edges.reduce((count, edge) => count + (edge.selected ? 1 : 0), 0))
  const autoLayout = useFlowStore((s) => s.autoLayout)
  const selectConnectedComponent = useFlowStore((s) => s.selectConnectedComponent)
  const load = useFlowStore((s) => s.load)
  const reset = useFlowStore((s) => s.reset)
  const importFlow = useFlowStore((s) => s.importFlow)
  const importWorkflowProject = useFlowStore((s) => s.importWorkflowProject)
  const snapToHelperLines = useSettingsStore((s) => s.snapToHelperLines)
  const setCanvasSetting = useSettingsStore((s) => s.set)

  const toolMode = useFlowStore((s) => s.toolMode)
  const setToolMode = useFlowStore((s) => s.setToolMode)
  const clearDrawings = useFlowStore((s) => s.clearDrawings)

  const isDirty = canUndo
  const persistenceLabel = documentState
    ? {
        loading: "加载中",
        saving: "保存中",
        saved: "已保存",
        error: "保存失败",
        conflict: "保存冲突",
      }[documentState]
    : isDirty
      ? "未保存"
      : "已保存"
  const persistenceNeedsAttention = documentState
    ? documentState === "saving" || documentState === "error" || documentState === "conflict"
    : isDirty
  const [shareUrlLoaded, setShareUrlLoaded] = useState(false)

  useEffect(() => {
    if (typeof window === "undefined") return
    setShareUrlLoaded(new URLSearchParams(window.location.search).has("flow"))
  }, [])

  const exportImage = useCallback(() => {
    const nodes = getNodes()
    if (nodes.length === 0) return
    const bounds = getNodesBounds(nodes)
    const width = 1600
    const height = 1000
    const viewport = getViewportForBounds(bounds, width, height, 0.5, 2, 0.15)
    const el = document.querySelector<HTMLElement>(".react-flow__viewport")
    if (!el) return
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--background").trim()
    toPng(el, {
      backgroundColor: bg || "#0a0a0a",
      width,
      height,
      style: {
        width: `${width}px`,
        height: `${height}px`,
        transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
      },
    }).then((dataUrl) => {
      const a = document.createElement("a")
      a.download = `workflow-${Date.now()}.png`
      a.href = dataUrl
      a.click()
      onExported?.("已导出为 PNG 图片")
    })
  }, [getNodes, onExported])

  const exportServerImage = useCallback(async () => {
    const { nodes, edges } = useFlowStore.getState()
    if (nodes.length === 0) return
    try {
      const res = await fetch("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nodes, edges }),
      })
      if (!res.ok) throw new Error("failed")
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.download = `workflow-server-${Date.now()}.svg`
      a.href = url
      a.click()
      URL.revokeObjectURL(url)
      onExported?.("已由服务端生成 SVG 图像")
    } catch {
      onExported?.("服务端成图失败")
    }
  }, [onExported])

  const exportJson = useCallback(() => {
    const { workflowProject, nodes, edges } = useFlowStore.getState()
    const data = exportReactFlowToWorkflowJson(workflowProject, { nodes, edges })
    downloadText(`workflow-${Date.now()}.json`, data, "application/json")
    onExported?.("已导出为 JSON 文件")
  }, [onExported])

  const exportMermaid = useCallback(() => {
    const { workflowProject, nodes, edges } = useFlowStore.getState()
    const data = exportReactFlowToWorkflowMermaid(workflowProject, { nodes, edges })
    downloadText(`workflow-${Date.now()}.mmd`, data, "text/plain")
    onExported?.("已导出为 Mermaid 文件")
  }, [onExported])

  const exportCanvas = useCallback(() => {
    const { workflowProject, nodes, edges } = useFlowStore.getState()
    const data = exportReactFlowToWorkflowCanvas(workflowProject, { nodes, edges })
    downloadText(`workflow-${Date.now()}.canvas`, data, "application/json")
    onExported?.("已导出为 Obsidian Canvas")
  }, [onExported])

  const exportOpml = useCallback(() => {
    const { workflowProject, nodes, edges } = useFlowStore.getState()
    const data = exportReactFlowToWorkflowOpml(workflowProject, { nodes, edges })
    downloadText(`workflow-${Date.now()}.opml`, data, "text/xml")
    onExported?.("已导出为 OPML")
  }, [onExported])

  const exportMarkdown = useCallback(() => {
    const { workflowProject, nodes, edges } = useFlowStore.getState()
    const data = exportReactFlowToWorkflowMarkdown(workflowProject, { nodes, edges })
    downloadText(`workflow-${Date.now()}.md`, data, "text/markdown")
    onExported?.("已导出为 Markdown")
  }, [onExported])

  const copyShareUrl = useCallback(async () => {
    if (typeof window === "undefined") return
    const { workflowProject, nodes, edges, drawings } = useFlowStore.getState()
    const url = buildShareUrl({ workflowProject, nodes, edges, drawings }, window.location.href)
    try {
      await navigator.clipboard.writeText(url)
      window.history.replaceState(null, "", url)
      setShareUrlLoaded(true)
      onExported?.("已复制压缩分享 URL")
    } catch {
      window.history.replaceState(null, "", url)
      setShareUrlLoaded(true)
      onExported?.("已写入地址栏分享 URL")
    }
  }, [onExported])

  const selectActiveComponent = useCallback(() => {
    const anchorId = selectedNodeId
    if (!anchorId) {
      onExported?.("先选中一个节点，再选择连通组件")
      return
    }
    const result = selectConnectedComponent(anchorId)
    onExported?.(`已选中连通组件：${result.nodeIds.length} 节点 / ${result.edgeIds.length} 连线`)
  }, [onExported, selectConnectedComponent, selectedNodeId])

  const onImportFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = async () => {
        const raw = reader.result as string
        const workflow = await importWorkflowJsonToReactFlowManaged(raw)
        if (workflow.ok) {
          importWorkflowProject(workflow.project)
          const difyReport = workflow.report?.source === "dify" ? workflow.report : undefined
          onExported?.(
            workflow.format === "n8n"
              ? `已翻译 n8n workflow：${workflow.report?.nodeCount ?? workflow.project.nodes.length} 节点 / ${workflow.report?.edgeCount ?? workflow.project.edges.length} 连线`
              : workflow.format === "dify"
                ? difyReport?.runtimeSource === "backend"
                  ? `已导入 Dify workflow：${difyReport.nodeCount} 节点，${difyReport.executable ? "可执行" : `${difyReport.blockers.length} 个阻塞项`}`
                  : "已导入 Dify 结构预览；Graphon 检查不可用，当前不可执行"
              : "已导入 canonical workflow",
          )
          return
        }

        const mermaid = importWorkflowMermaidToReactFlow(raw)
        if (mermaid.ok) {
          importWorkflowProject(mermaid.project)
          onExported?.("已导入 Mermaid workflow draft")
          return
        }

        try {
          importFlow(JSON.parse(raw))
          onExported?.("已导入旧版画布 JSON")
        } catch {
          onExported?.(workflow.error)
        }
      }
      reader.readAsText(file)
      e.target.value = ""
    },
    [importFlow, importWorkflowProject, onExported],
  )

  return (
    <header
      data-health="command-strip"
      className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-3"
    >
      <nav aria-label="工作流" className="flex min-w-0 flex-1 items-center gap-2.5">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg border border-border bg-card font-mono text-[11px] font-semibold text-foreground">
          K
        </span>
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-foreground">{workflowProjectName}</div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{nodeCount} 个节点</span>
            <span>·</span>
            <span className={cn(persistenceNeedsAttention && "text-[#ff7a17]")}>{persistenceLabel}</span>
          </div>
        </div>
      </nav>

      <div className="hidden items-center gap-2 rounded-full border border-border/80 bg-card/70 px-3 py-1.5 text-[11px] text-muted-foreground lg:flex">
        <Network className="size-3.5" />
        <span className="font-medium text-foreground">L{networkDepth} · {networkLayer.label}</span>
        <span>·</span>
        <span>{networkDepth === 1 ? networkLayer.description : networkDepth < MAX_WORKFLOW_NODE_DEPTH ? "双击节点继续展开内部网络" : networkLayer.description}</span>
      </div>

      <div className="hidden shrink-0 items-center rounded-lg border bg-card/60 p-1 xl:flex" aria-label="画布工作视图">
        <button type="button" aria-pressed={!workbenchMode} onClick={() => onChangeWorkbenchMode?.(null)} className={cn("min-h-8 rounded-md px-2.5 text-[11px] transition-colors", !workbenchMode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>编排</button>
        <button type="button" aria-pressed={workbenchMode === "evidence"} onClick={() => onChangeWorkbenchMode?.("evidence")} className={cn("flex min-h-8 items-center gap-1.5 rounded-md px-2.5 text-[11px] transition-colors", workbenchMode === "evidence" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}><BrainCircuit className="size-3.5" />证据</button>
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <Button variant="outline" size="sm" className="min-h-11 gap-1.5 rounded-lg" onClick={onOpenPalette}>
          <span className="text-base leading-none">＋</span>
          <span className="hidden sm:inline">添加节点</span>
        </Button>

        <div className="hidden items-center sm:flex">
          <IconAction label="撤销 (Ctrl+Z)" onClick={undo} disabled={!canUndo}>
            <Undo2 className="size-3.5" />
          </IconAction>
          <IconAction label="重做 (Ctrl+Shift+Z)" onClick={redo} disabled={!canRedo}>
            <Redo2 className="size-3.5" />
          </IconAction>
        </div>

        <Button
          variant="outline"
          size="sm"
          className="min-h-11 gap-1.5 rounded-lg"
          onClick={onSaveWorkflow}
          disabled={documentState === "loading" || documentState === "saving" || documentState === "conflict"}
          aria-label="保存工作流"
        >
          <Save className="size-3.5" />
          保存
        </Button>

        <Button
          data-testid="workflow-run"
          size="sm"
          className="min-h-11 gap-1.5 rounded-lg"
          onClick={onRunWorkflow}
        >
          <Play className="size-3.5" />
          运行
        </Button>

        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger
              render={
                <DropdownMenuTrigger
                  render={
                    <Button variant="ghost" size="icon" className="size-11 rounded-lg" aria-label="更多工具" />
                  }
                />
              }
            >
              <MoreHorizontal className="size-4" />
            </TooltipTrigger>
            <TooltipContent>更多工具</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="end" className="max-h-[76vh] w-64 overflow-y-auto">
            <DropdownMenuGroup>
              <DropdownMenuLabel>工作流</DropdownMenuLabel>
              <DropdownMenuItem
                onClick={() => {
                  reset()
                  onExported?.("已恢复默认封包网络")
                }}
              >
                <RotateCcw className="size-3.5" />
                恢复默认封包网络
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  importWorkflowProject(COLLECTION_WORKFLOW_PROJECT)
                  onExported?.("已载入完整采集示例")
                }}
              >
                <ListTree className="size-3.5" />
                载入完整采集示例
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  const ok = load()
                  onExported?.(ok ? "已恢复上次保存" : "没有找到保存记录")
                }}
              >
                <FolderOpen className="size-3.5" />
                恢复上次保存
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />

            <DropdownMenuGroup>
              <DropdownMenuLabel>编辑与视图</DropdownMenuLabel>
              <DropdownMenuItem onClick={() => setToolMode("select")}>
                <Magnet className="size-3.5" />
                选择工具 {toolMode === "select" ? "· 当前" : ""}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setToolMode(toolMode === "scissors" ? "select" : "scissors")}>
                <Scissors className="size-3.5" />
                剪断连线 {toolMode === "scissors" ? "· 当前" : ""}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setToolMode(toolMode === "draw" ? "select" : "draw")}>
                <Eraser className="size-3.5" />
                画布标注 {toolMode === "draw" ? "· 当前" : ""}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={clearDrawings}>
                <Eraser className="size-3.5" />
                清除画布标注
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void autoLayout("TB", "elk", true)}>
                <Network className="size-3.5" />
                自动整理节点
              </DropdownMenuItem>
              <DropdownMenuItem disabled={selectedNodeCount === 0} onClick={selectActiveComponent}>
                <Network className="size-3.5" />
                选择当前流程分支
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setCanvasSetting("snapToHelperLines", !snapToHelperLines)}>
                <Magnet className="size-3.5" />
                {snapToHelperLines ? "关闭节点吸附" : "开启节点吸附"}
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />

            <DropdownMenuGroup>
              <DropdownMenuLabel>运行与设置</DropdownMenuLabel>
              <DropdownMenuItem onClick={onToggleRunTrace}>
                <Play className="size-3.5" />
                {runTraceOpen ? "关闭运行面板" : "运行面板"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onChangeWorkbenchMode?.(workbenchMode === "evidence" ? null : "evidence")}>
                <BrainCircuit className="size-3.5" />
                {workbenchMode === "evidence" ? "关闭逻辑证据工作台" : "逻辑证据工作台"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onToggleProjectSettings}>
                <SlidersHorizontal className="size-3.5" />
                {projectSettingsOpen ? "关闭工作流设置" : "工作流设置"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onToggleNodeManagement}>
                <ListTree className="size-3.5" />
                {nodeManagementOpen ? "关闭节点状态" : "节点状态"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onToggleCollab}>
                <Users className="size-3.5" />
                {collab ? "关闭多人协作" : "开启多人协作"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onToggleSettings}>
                <Settings className="size-3.5" />
                {settingsOpen ? "关闭画布设置" : "画布设置"}
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />

            <DropdownMenuGroup>
              <DropdownMenuLabel>导入与导出</DropdownMenuLabel>
              <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                <Upload className="size-3.5" />
                导入 Dify / n8n / JSON / Mermaid
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportJson}>
                <Download className="size-3.5" />
                导出 JSON
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportImage}>
                <ImageDown className="size-3.5" />
                导出 PNG
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void exportServerImage()}>
                <ServerCog className="size-3.5" />
                导出 SVG
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportMermaid}>
                <FileCode2 className="size-3.5" />
                导出 Mermaid
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportCanvas}>
                <FileCode2 className="size-3.5" />
                导出 Obsidian Canvas
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportOpml}>
                <FileCode2 className="size-3.5" />
                导出 OPML
              </DropdownMenuItem>
              <DropdownMenuItem onClick={exportMarkdown}>
                <FileCode2 className="size-3.5" />
                导出 Markdown
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void copyShareUrl()}>
                <Link2 className="size-3.5" />
                {shareUrlLoaded ? "重新复制分享链接" : "复制分享链接"}
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel className="text-[10px] font-normal text-muted-foreground">
                {nodeCount} 个节点 · {edgeCount} 条连线 · {selectedNodeCount + selectedEdgeCount} 个已选
              </DropdownMenuLabel>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="application/json,application/yaml,text/yaml,.json,.yaml,.yml,.mmd,text/plain"
        className="hidden"
        onChange={onImportFile}
      />
    </header>
  )
}
