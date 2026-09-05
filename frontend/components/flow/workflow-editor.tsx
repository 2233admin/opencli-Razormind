"use client"

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react"
import {
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type NodeMouseHandler,
  type OnConnectEnd,
  type OnConnectStart,
  type ReactFlowProps,
} from "@xyflow/react"
import { useShallow } from "zustand/react/shallow"
import "@xyflow/react/dist/style.css"

import { useFlowStore } from "@/lib/flow/store"
import { nodeHandleDescriptor, nodeHandleIds } from "@/lib/flow/graph"
import { useSettingsStore } from "@/lib/flow/settings-store"
import type { WorkflowNode, WorkflowEdge, ToolMode } from "@/lib/flow/types"
import { CommandStrip } from "./command-strip"
import { CommandPalette, type CompatibleConnectionPort } from "./command-palette"
import { NODE_PALETTE } from "@/lib/flow/palette"
import {
  getWorkflowNodeCatalog,
  nativeIntelligenceCatalogItems,
} from "@/lib/workflow/node-catalog"
import { mergeWorkflowNodeCatalog } from "@/lib/workflow/opencli-adapter-catalog"
import { getWorkflowPrimitives } from "@/lib/workflow/node-primitives"
import { groupPrimitivesForNodeMenu } from "@/lib/workflow/node-menu"
import { useWorkflowCapabilities } from "@/lib/workflow/use-workflow-capabilities"
import { useOpenCLIAdapterCatalog } from "@/lib/workflow/use-opencli-adapter-catalog"
import { useWorkflowToolCapabilities } from "@/lib/workflow/use-workflow-tool-capabilities"
import { useWorkflowKeyboardShortcuts } from "./workflow-keyboard-shortcuts"
import {
  useCanvasViewportCompaction,
  useConnectionGuards,
  usePaletteDrop,
  useScissorCanvasHandlers,
  useWorkflowNodeDragHandlers,
  type ShakeState,
} from "./workflow-canvas-interactions"
import { useWorkflowNodeMenuActions, type NodeMenuState } from "./workflow-node-menu-actions"
import { selectEditorCanvasState } from "./workflow-editor-selectors"
import { WorkflowCanvasSurface } from "./workflow-canvas-surface"
import type { WorkflowWorkbenchMode } from "./workflow-workbench-panel"
import type { RunPanelScope } from "@/lib/workflow/run-panel-extensions"
import {
  isNetworkLocked,
  useApplyWorkflowCapabilities,
  useAutoDismissToast,
  useCompactViewportEffect,
  useCompactViewportMedia,
  useDismissNodeMenu,
  useExitCurrentNetwork,
  useSharedWorkflowImport,
} from "./workflow-editor-effects"

type PendingConnection = {
  handleId: string | null
  handleType: "source" | "target"
  nodeId: string
  type: string
}

function EditorCanvas({
  documentState,
  onSaveWorkflow,
  workspaceId,
  runPanelScope,
}: {
  documentState?: "loading" | "saving" | "saved" | "error" | "conflict"
  onSaveWorkflow?: () => Promise<void>
  workspaceId?: string | null
  runPanelScope?: RunPanelScope | null
}) {
  const {
    addNodeFromPalette,
    addPrimitiveToNodeNetwork,
    addWorkflowNodeFromCatalog,
    applyWorkflowCapabilities,
    attachToParent,
    autoLayout,
    clearHelperLines,
    connectNodes,
    copy,
    cut,
    deleteSelected,
    detachFromParent,
    disconnectNodeConnections,
    duplicate,
    edges,
    enterNodeNetwork,
    exitNodeNetwork,
    groupSelection,
    helperLines,
    insertNodeOnEdge,
    lockNodeInternals,
    networkStack,
    nodes,
    onConnect,
    onReconnect,
    onEdgesChange,
    onNodesChange,
    paste,
    redo,
    removeEdgesByIds,
    resizeGroupToFit,
    resolveNodeCollisions,
    save,
    selectConnectedComponent,
    setToolMode,
    takeSnapshot,
    toolMode,
    undo,
    unlockNodeInternals,
    updateWorkflowProfile,
    workflowProject,
  } = useFlowStore(useShallow(selectEditorCanvasState))

  const settings = useSettingsStore()

  const { screenToFlowPosition, getInternalNode, setViewport, fitView } = useReactFlow<WorkflowNode, WorkflowEdge>()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const importInputRef = useRef<HTMLInputElement>(null)
  const mousePos = useRef({ x: 0, y: 0 })
  const shakeRef = useRef<Map<string, ShakeState>>(new Map())
  const scissorDraggingRef = useRef(false)
  const scissorCutRef = useRef<Set<string>>(new Set())
  const yMomentaryModeRef = useRef<ToolMode | null>(null)
  const pendingConnectionRef = useRef<PendingConnection | null>(null)
  const reconnectingEdgeIdRef = useRef<string | null>(null)
  const [scissorTrail, setScissorTrail] = useState<{ x: number; y: number }[]>([])
  const [toast, setToast] = useState<string | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteAnchor, setPaletteAnchor] = useState<{ x: number; y: number } | null>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false)
  const [runTraceOpen, setRunTraceOpen] = useState(false)
  const [nodeManagementOpen, setNodeManagementOpen] = useState(false)
  const [workbenchMode, setWorkbenchMode] = useState<WorkflowWorkbenchMode | null>(null)
  const [zoom, setZoom] = useState(1)
  const [compactViewport, setCompactViewport] = useState(false)
  const [nodeMenu, setNodeMenu] = useState<NodeMenuState | null>(null)
  const [wiringState, setWiringState] = useState<"idle" | "wiring" | "picker" | "reconnecting">("idle")
  const [compatiblePort, setCompatiblePort] = useState<CompatibleConnectionPort | undefined>()
  const { capabilities } = useWorkflowCapabilities(true, workspaceId)
  const {
    items: openCLIAdapterCatalogItems,
    response: openCLIAdapterCatalogResponse,
    error: openCLIAdapterCatalogError,
    loading: openCLIAdapterCatalogLoading,
  } = useOpenCLIAdapterCatalog(true)
  const {
    tools: nativeIntelligenceTools,
    error: nativeIntelligenceToolsError,
    loading: nativeIntelligenceToolsLoading,
  } = useWorkflowToolCapabilities(true)
  const dopNodeMenuItems = useMemo(
    () =>
      mergeWorkflowNodeCatalog(
        [
          ...getWorkflowNodeCatalog(workflowProject.profile, capabilities),
          ...(workflowProject.profile === "intelligence"
            ? nativeIntelligenceCatalogItems(nativeIntelligenceTools)
            : []),
        ],
        openCLIAdapterCatalogItems,
      ),
    [
      workflowProject.profile,
      capabilities,
      openCLIAdapterCatalogItems,
      nativeIntelligenceTools,
    ],
  )
  const primitiveMenuGroups = useMemo(
    () => groupPrimitivesForNodeMenu(getWorkflowPrimitives(), settings.language),
    [settings.language],
  )

  const showToast = useCallback((msg: string) => setToast(msg), [])
  const saveWorkflow = useCallback(() => {
    if (onSaveWorkflow) {
      void onSaveWorkflow()
      return
    }
    try {
      save()
      showToast("工作流已保存到本地")
    } catch {
      showToast("本地保存失败，请重试或导出备份")
    }
  }, [onSaveWorkflow, save, showToast])
  const setMiniMapVisible = useCallback((visible: boolean) => settings.set("showMiniMap", visible), [settings])

  useApplyWorkflowCapabilities({ applyWorkflowCapabilities, capabilities, workflowProjectId: workflowProject.id })
  useSharedWorkflowImport({ fitView, showToast })
  useAutoDismissToast(toast, setToast)
  useDismissNodeMenu(nodeMenu, setNodeMenu)
  useCompactViewportMedia(setCompactViewport)
  const applyCompactViewport = useCanvasViewportCompaction({ compactViewport, nodes, setViewport })
  useCompactViewportEffect(applyCompactViewport)

  useWorkflowKeyboardShortcuts({
    autoLayout,
    copy,
    cut,
    deleteSelected,
    duplicate,
    exitNodeNetwork,
    fitView,
    groupSelection,
    inspectorOpen,
    mousePosRef: mousePos,
    paste,
    projectSettingsOpen,
    redo,
    save: saveWorkflow,
    screenToFlowPosition,
    scissorCutRef,
    scissorDraggingRef,
    setInspectorOpen,
    setPaletteOpen,
    setProjectSettingsOpen,
    setScissorTrail,
    setSettingsOpen,
    setToolMode,
    setMiniMapVisible,
    settingsOpen,
    showToast,
    undo,
    yMomentaryModeRef,
  })

  const { onDragOver, onDrop } = usePaletteDrop({ addNodeFromPalette, screenToFlowPosition })
  const { onCanvasMouseDownCapture, onCanvasMouseMoveCapture, onCanvasMouseUpCapture } = useScissorCanvasHandlers({
    cutRef: scissorCutRef,
    draggingRef: scissorDraggingRef,
    removeEdgesByIds,
    setTrail: setScissorTrail,
    showToast,
    takeSnapshot,
    toolMode,
    wrapperRef,
  })
  const { onNodeDrag, onNodeDragStop } = useWorkflowNodeDragHandlers({
    attachToParent,
    clearHelperLines,
    detachFromParent,
    disconnectNodeConnections,
    getInternalNode,
    insertNodeOnEdge,
    resizeGroupToFit,
    resolveNodeCollisions,
    shakeRef,
    showToast,
  })
  const {
    addDopNodeFromMenu,
    addPrimitiveFromMenu,
    diveIntoNetwork,
    lockInternals,
    unlockInternals,
  } = useWorkflowNodeMenuActions({
    addPrimitiveToNodeNetwork,
    addWorkflowNodeFromCatalog,
    capabilities,
    enterNodeNetwork,
    fitView,
    language: settings.language,
    lockNodeInternals,
    nodeMenu,
    screenToFlowPosition,
    selectConnectedComponent,
    setInspectorOpen,
    setNodeMenu,
    showToast,
    unlockNodeInternals,
  })

  const onNodeDoubleClick: NodeMouseHandler<WorkflowNode> = useCallback(
    (_event: unknown, node: { id: string }) => {
      diveIntoNetwork(node.id)
    },
    [diveIntoNetwork],
  )

  const onNodeClick: NodeMouseHandler<WorkflowNode> = useCallback(() => {
    if (!workbenchMode) {
      setSettingsOpen(false)
      setProjectSettingsOpen(false)
      setInspectorOpen(true)
    }
  }, [workbenchMode])

  const onNodeContextMenu: NodeMouseHandler<WorkflowNode> = useCallback((event, node) => {
    event.preventDefault()
    event.stopPropagation()
    setPaletteOpen(false)
    setNodeMenu({ nodeId: node.id, x: event.clientX, y: event.clientY })
  }, [])

  useEffect(() => {
    const openPortMenu = (event: Event) => {
      const detail = (event as CustomEvent<NodeMenuState["port"] & { x: number; y: number }>).detail
      if (!detail) return
      setPaletteOpen(false)
      setNodeMenu({
        nodeId: detail.nodeId,
        port: detail,
        x: detail.x,
        y: detail.y,
      })
    }
    window.addEventListener("opencli:workflow-port-menu", openPortMenu)
    return () => window.removeEventListener("opencli:workflow-port-menu", openPortMenu)
  }, [])

  const onPaneContextMenu = useCallback((event: ReactMouseEvent<Element> | MouseEvent) => {
    event.preventDefault()
    setPaletteOpen(false)
    setNodeMenu({ x: event.clientX, y: event.clientY })
  }, [])

  const openNodePicker = useCallback(() => {
    const bounds = wrapperRef.current?.getBoundingClientRect()
    setPaletteAnchor(
      bounds
        ? { x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 }
        : { x: window.innerWidth / 2, y: window.innerHeight / 2 },
    )
    setPaletteOpen(true)
  }, [])

  const openNodePickerFromMenu = useCallback(() => {
    if (!nodeMenu) return
    setPaletteAnchor({ x: nodeMenu.x, y: nodeMenu.y })
    setNodeMenu(null)
    setPaletteOpen(true)
  }, [nodeMenu])

  const addNoteFromMenu = useCallback(() => {
    if (!nodeMenu) return
    const note = NODE_PALETTE.find((item) => item.nodeType === "note")
    if (!note) {
      showToast("未找到注释节点定义")
      setNodeMenu(null)
      return
    }
    addNodeFromPalette(note, screenToFlowPosition({ x: nodeMenu.x, y: nodeMenu.y }))
    setNodeMenu(null)
    showToast("已添加注释")
  }, [addNodeFromPalette, nodeMenu, screenToFlowPosition, showToast])

  const runWorkflow = useCallback(() => {
    setNodeMenu(null)
    setRunTraceOpen(true)
    showToast("请在运行面板检查输入，再启动运行")
  }, [showToast])

  const importAppFromMenu = useCallback(() => {
    setNodeMenu(null)
    importInputRef.current?.click()
  }, [])

  const { getConnectionValidation, isValidConnection, onBeforeDelete } = useConnectionGuards({
    ignoredEdgeIdRef: reconnectingEdgeIdRef,
    settings,
  })
  const clearWiringSession = useCallback(() => {
    pendingConnectionRef.current = null
    reconnectingEdgeIdRef.current = null
    setWiringState("idle")
    setCompatiblePort(undefined)
  }, [])
  const onConnectStart: OnConnectStart = useCallback((_event, params) => {
    if (!params.nodeId || !params.handleType) return
    const node = useFlowStore.getState().nodes.find((candidate) => candidate.id === params.nodeId)
    const port = nodeHandleDescriptor(node, params.handleId, params.handleType)
    pendingConnectionRef.current = {
      nodeId: params.nodeId,
      handleId: params.handleId,
      handleType: params.handleType,
      type: port?.type ?? "unknown",
    }
    setCompatiblePort({ handleType: params.handleType, type: port?.type ?? "unknown" })
    setWiringState("wiring")
  }, [])
  const onConnectEnd: OnConnectEnd = useCallback((_event, connectionState) => {
    if (connectionState.isValid) {
      clearWiringSession()
      return
    }
    const pending = pendingConnectionRef.current
    if (!pending) {
      clearWiringSession()
      return
    }
    if (!connectionState.toNode && connectionState.pointer) {
      setPaletteAnchor(connectionState.pointer)
      setPaletteOpen(true)
      setWiringState("picker")
      return
    }
    if (connectionState.toHandle) {
      const connection: Connection = pending.handleType === "source"
        ? {
            source: pending.nodeId,
            sourceHandle: pending.handleId,
            target: connectionState.toHandle.nodeId,
            targetHandle: connectionState.toHandle.id ?? null,
          }
        : {
            source: connectionState.toHandle.nodeId,
            sourceHandle: connectionState.toHandle.id ?? null,
            target: pending.nodeId,
            targetHandle: pending.handleId,
          }
      const result = getConnectionValidation(connection)
      if (!result.ok) showToast(result.reason)
    }
    clearWiringSession()
  }, [clearWiringSession, getConnectionValidation, showToast])
  const onReconnectStart: NonNullable<ReactFlowProps<WorkflowNode, WorkflowEdge>["onReconnectStart"]> = useCallback((_event, edge) => {
    reconnectingEdgeIdRef.current = edge.id
    setWiringState("reconnecting")
  }, [])
  const onReconnectEnd: NonNullable<ReactFlowProps<WorkflowNode, WorkflowEdge>["onReconnectEnd"]> = useCallback(
    (_event, _edge, _handleType, connectionState) => {
      if (!connectionState.isValid && connectionState.toHandle) {
        const from = connectionState.fromHandle
        const to = connectionState.toHandle
        const connection: Connection = from.type === "source"
          ? { source: from.nodeId, sourceHandle: from.id ?? null, target: to.nodeId, targetHandle: to.id ?? null }
          : { source: to.nodeId, sourceHandle: to.id ?? null, target: from.nodeId, targetHandle: from.id ?? null }
        const result = getConnectionValidation(connection)
        if (!result.ok) showToast(result.reason)
      }
      clearWiringSession()
    },
    [clearWiringSession, getConnectionValidation, showToast],
  )
  const connectPortFromMenu = useCallback(() => {
    const port = nodeMenu?.port
    if (!port) return
    pendingConnectionRef.current = {
      nodeId: port.nodeId,
      handleId: port.handleId,
      handleType: port.handleType,
      type: port.type,
    }
    setCompatiblePort({ handleType: port.handleType, type: port.type })
    setPaletteAnchor({ x: nodeMenu.x, y: nodeMenu.y })
    setPaletteOpen(true)
    setNodeMenu(null)
    setWiringState("picker")
  }, [nodeMenu])
  const onPaletteNodeCreated = useCallback(() => {
    const pending = pendingConnectionRef.current
    if (!pending) {
      setInspectorOpen(true)
      return
    }
    const newNode = useFlowStore.getState().nodes.find((node) => node.selected)
    if (!newNode) {
      clearWiringSession()
      return
    }
    const targetDirection = pending.handleType === "source" ? "target" : "source"
    const connection = nodeHandleIds(newNode, targetDirection)
      .map<Connection>((handleId) => pending.handleType === "source"
        ? {
            source: pending.nodeId,
            sourceHandle: pending.handleId,
            target: newNode.id,
            targetHandle: handleId,
          }
        : {
            source: newNode.id,
            sourceHandle: handleId,
            target: pending.nodeId,
            targetHandle: pending.handleId,
          })
      .find((candidate) => getConnectionValidation(candidate).ok)
    if (connection) connectNodes(connection, { suppressSnapshot: true })
    else showToast("新节点没有兼容端口，已保留节点")
    clearWiringSession()
  }, [clearWiringSession, connectNodes, getConnectionValidation, showToast])

  const isDraw = toolMode === "draw"
  const isScissors = toolMode === "scissors"
  const networkLocked = isNetworkLocked(networkStack, nodes)
  const exitCurrentNetwork = useExitCurrentNetwork({ exitNodeNetwork, fitView, showToast })
  const onCanvasMouseMove = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
    mousePos.current = { x: event.clientX, y: event.clientY }
  }, [])

  const toggleCollabProvider = useCallback(() => {
    settings.set("collabProvider", settings.collabProvider === "off" ? "yjs" : "off")
  }, [settings])

  const changeWorkbenchMode = useCallback((mode: WorkflowWorkbenchMode | null) => {
    setWorkbenchMode(mode)
    if (!mode) return
    setInspectorOpen(false)
    setSettingsOpen(false)
    setProjectSettingsOpen(false)
  }, [])

  return (
    <div data-health="workflow-editor" className="flex h-full min-h-0 flex-1 flex-col">
      <CommandStrip
        importInputRef={importInputRef}
        documentState={documentState}
        onSaveWorkflow={saveWorkflow}
        onOpenPalette={openNodePicker}
        onExported={showToast}
        collab={settings.collabProvider !== "off"}
        onToggleCollab={toggleCollabProvider}
        settingsOpen={settingsOpen}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
        projectSettingsOpen={projectSettingsOpen}
        onToggleProjectSettings={() => setProjectSettingsOpen((v) => !v)}
        onRunWorkflow={runWorkflow}
        runTraceOpen={runTraceOpen}
        onToggleRunTrace={() => setRunTraceOpen((v) => !v)}
        nodeManagementOpen={nodeManagementOpen}
        onToggleNodeManagement={() => setNodeManagementOpen((v) => !v)}
        workbenchMode={workbenchMode}
        onChangeWorkbenchMode={changeWorkbenchMode}
      />
      <div className="flex min-h-0 flex-1">
        <WorkflowCanvasSurface
          addDopNodeFromMenu={addDopNodeFromMenu}
          addPrimitiveFromMenu={addPrimitiveFromMenu}
          capabilities={capabilities}
          compactViewport={compactViewport}
          diveIntoNetwork={diveIntoNetwork}
          dopNodeMenuItems={dopNodeMenuItems}
          edges={edges}
          exitCurrentNetwork={exitCurrentNetwork}
          helperLines={helperLines}
          inspectorOpen={inspectorOpen}
          onCloseInspector={() => setInspectorOpen(false)}
          isDraw={isDraw}
          isScissors={isScissors}
          isValidConnection={isValidConnection}
          lockInternals={lockInternals}
          networkLocked={networkLocked}
          networkStack={networkStack}
          nodeManagementOpen={nodeManagementOpen}
          nodeMenu={nodeMenu}
          nodes={nodes}
          onBeforeDelete={onBeforeDelete}
          onAddNodeFromMenu={openNodePickerFromMenu}
          onAddNoteFromMenu={addNoteFromMenu}
          onConnectPortFromMenu={connectPortFromMenu}
          onImportApp={importAppFromMenu}
          onTestRun={runWorkflow}
          onCanvasMouseDownCapture={onCanvasMouseDownCapture}
          onCanvasMouseMoveCapture={onCanvasMouseMoveCapture}
          onCanvasMouseUpCapture={onCanvasMouseUpCapture}
          onConnect={onConnect}
          onConnectEnd={onConnectEnd}
          onConnectStart={onConnectStart}
          onClickConnectEnd={onConnectEnd}
          onClickConnectStart={onConnectStart}
          onDragOver={onDragOver}
          onDrop={onDrop}
          onEdgesChange={onEdgesChange}
          onMouseMove={onCanvasMouseMove}
          onReconnect={onReconnect}
          onReconnectEnd={onReconnectEnd}
          onReconnectStart={onReconnectStart}
          onNodeContextMenu={onNodeContextMenu}
          onPaneContextMenu={onPaneContextMenu}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onNodeDrag={onNodeDrag}
          onNodeDragStop={onNodeDragStop}
          onNodesChange={onNodesChange}
          onProfileChange={updateWorkflowProfile}
          runPanelScope={runPanelScope}
          primitiveMenuGroups={primitiveMenuGroups}
          projectSettingsOpen={projectSettingsOpen}
          runTraceOpen={runTraceOpen}
          scissorTrail={scissorTrail}
          setNodeManagementOpen={setNodeManagementOpen}
          settings={settings}
          settingsOpen={settingsOpen}
          takeSnapshot={takeSnapshot}
          toast={toast}
          toolMode={toolMode}
          wiringState={wiringState}
          unlockInternals={unlockInternals}
          workflowProfile={workflowProject.profile}
          workbenchMode={workbenchMode}
          onChangeWorkbenchMode={changeWorkbenchMode}
          wrapperRef={wrapperRef}
          zoom={zoom}
          setZoom={setZoom}
        />
      </div>

      <CommandPalette
        adapterCatalogError={openCLIAdapterCatalogError ?? nativeIntelligenceToolsError}
        adapterCatalogLoading={openCLIAdapterCatalogLoading || nativeIntelligenceToolsLoading}
        adapterCatalogResponse={openCLIAdapterCatalogResponse}
        catalogItems={dopNodeMenuItems}
        compatiblePort={wiringState === "picker" ? compatiblePort : undefined}
        open={paletteOpen}
        onImportApp={() => importInputRef.current?.click()}
        onClose={() => {
          setPaletteOpen(false)
          setPaletteAnchor(null)
          clearWiringSession()
        }}
        onMessage={showToast}
        onSaveWorkflow={saveWorkflow}
        projectPersistence={Boolean(onSaveWorkflow)}
        onNodeCreated={onPaletteNodeCreated}
        getAnchor={() => screenToFlowPosition(paletteAnchor ?? mousePos.current)}
      />
    </div>
  )
}

export function WorkflowEditor({
  documentState,
  onSaveWorkflow,
  workspaceId,
  runPanelScope,
}: {
  documentState?: "loading" | "saving" | "saved" | "error" | "conflict"
  onSaveWorkflow?: () => Promise<void>
  workspaceId?: string | null
  runPanelScope?: RunPanelScope | null
} = {}) {
  return (
    <ReactFlowProvider>
      <EditorCanvas documentState={documentState} onSaveWorkflow={onSaveWorkflow} workspaceId={workspaceId} runPanelScope={runPanelScope} />
    </ReactFlowProvider>
  )
}
