"use client"

import { useEffect, useMemo, type DragEvent, type MouseEvent as ReactMouseEvent, type RefObject } from "react"
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  SelectionMode,
  useStore,
  type IsValidConnection,
  type NodeMouseHandler,
  type OnBeforeDelete,
  type OnConnect,
  type OnConnectEnd,
  type OnConnectStart,
  type OnEdgesChange,
  type OnNodeDrag,
  type OnNodesChange,
  type OnReconnect,
  type ReactFlowProps,
} from "@xyflow/react"

import type { CanvasSettings } from "@/lib/flow/settings-store"
import type { FlowState } from "@/lib/flow/store"
import { workflowNodeDensity } from "@/lib/flow/node-geometry"
import type { ToolMode, WorkflowEdge, WorkflowNode } from "@/lib/flow/types"
import type { WorkflowCapabilitiesResponse } from "@/lib/workflow/capabilities"
import type { WorkflowNodeCatalogItem } from "@/lib/workflow/node-catalog"
import type { WorkflowPrimitive } from "@/lib/workflow/node-primitives"
import { cn } from "@/lib/utils"
import { Collaboration } from "./collaboration"
import { DrawingLayer } from "./drawing-layer"
import EditableEdge from "./edges/editable-edge"
import RoutedEdge from "./edges/routed-edge"
import WorkflowEdge_ from "./edges/workflow-edge"
import { HelperLinesRenderer } from "./helper-lines-renderer"
import { Inspector } from "./inspector"
import { NodeContextMenu } from "./node-context-menu"
import GroupNode from "./nodes/group-node"
import MathNode from "./nodes/math-node"
import NoteNode from "./nodes/note-node"
import ShapeNode from "./nodes/shape-node"
import WorkflowNodeComp from "./nodes/workflow-node"
import type { NodeMenuState } from "./workflow-node-menu-actions"
import {
  NetworkBreadcrumb,
  ScissorTrailOverlay,
  WorkflowFloatingPanels,
  WorkflowToast,
} from "./workflow-editor-overlays"
import { WorkflowMotionRuntime } from "./workflow-motion-runtime"
import type { CanvasPoint } from "./workflow-canvas-geometry"
import { WorkflowWorkbenchPanel, type WorkflowWorkbenchMode } from "./workflow-workbench-panel"

const nodeTypes = {
  workflow: WorkflowNodeComp,
  note: NoteNode,
  group: GroupNode,
  shape: ShapeNode,
  math: MathNode,
}

const edgeTypes = {
  workflow: WorkflowEdge_,
  editable: EditableEdge,
  routed: RoutedEdge,
}

const defaultEdgeOptions = { type: "workflow", animated: false } as const
const proOptions = { hideAttribution: true } as const

type PrimitiveMenuGroup = {
  category: string
  label: string
  items: WorkflowPrimitive[]
}

type WorkflowCanvasSurfaceProps = {
  addDopNodeFromMenu: (item: WorkflowNodeCatalogItem) => void
  addPrimitiveFromMenu: (item: WorkflowPrimitive, itemIndex: number) => void
  capabilities: WorkflowCapabilitiesResponse | null | undefined
  compactViewport: boolean
  diveIntoNetwork: (nodeId: string) => void
  dopNodeMenuItems: WorkflowNodeCatalogItem[]
  edges: WorkflowEdge[]
  exitCurrentNetwork: () => void
  helperLines: FlowState["helperLines"]
  inspectorOpen: boolean
  isDraw: boolean
  isScissors: boolean
  isValidConnection: IsValidConnection<WorkflowEdge>
  lockInternals: (nodeId: string) => void
  networkLocked: boolean
  networkStack: FlowState["networkStack"]
  nodeManagementOpen: boolean
  nodeMenu: NodeMenuState | null
  nodes: WorkflowNode[]
  onBeforeDelete: OnBeforeDelete<WorkflowNode, WorkflowEdge>
  onAddNodeFromMenu: () => void
  onAddNoteFromMenu: () => void
  onConnectPortFromMenu: () => void
  onImportApp: () => void
  onTestRun: () => void
  onCanvasMouseDownCapture: (event: ReactMouseEvent<HTMLDivElement>) => void
  onCanvasMouseMoveCapture: (event: ReactMouseEvent<HTMLDivElement>) => void
  onCanvasMouseUpCapture: (event: ReactMouseEvent<HTMLDivElement>) => void
  onConnect: OnConnect
  onConnectEnd: OnConnectEnd
  onConnectStart: OnConnectStart
  onClickConnectEnd: OnConnectEnd
  onClickConnectStart: OnConnectStart
  onDragOver: (event: DragEvent) => void
  onDrop: (event: DragEvent) => void
  onEdgesChange: OnEdgesChange<WorkflowEdge>
  onCloseInspector: () => void
  onMouseMove: (event: ReactMouseEvent<HTMLDivElement>) => void
  onNodeClick: NodeMouseHandler<WorkflowNode>
  onNodeContextMenu: NodeMouseHandler<WorkflowNode>
  onNodeDoubleClick: NodeMouseHandler<WorkflowNode>
  onPaneContextMenu: (event: ReactMouseEvent<Element> | MouseEvent) => void
  onNodeDrag: OnNodeDrag<WorkflowNode>
  onNodeDragStop: OnNodeDrag<WorkflowNode>
  onNodesChange: OnNodesChange<WorkflowNode>
  onReconnect: OnReconnect<WorkflowEdge>
  onReconnectEnd: NonNullable<ReactFlowProps<WorkflowNode, WorkflowEdge>["onReconnectEnd"]>
  onReconnectStart: NonNullable<ReactFlowProps<WorkflowNode, WorkflowEdge>["onReconnectStart"]>
  onProfileChange: FlowState["updateWorkflowProfile"]
  primitiveMenuGroups: PrimitiveMenuGroup[]
  projectSettingsOpen: boolean
  runTraceOpen: boolean
  runRequestId: number
  scissorTrail: CanvasPoint[]
  setNodeManagementOpen: (open: boolean) => void
  settings: CanvasSettings
  settingsOpen: boolean
  takeSnapshot: () => void
  toast: string | null
  toolMode: ToolMode
  wiringState: "idle" | "wiring" | "picker" | "reconnecting"
  unlockInternals: (nodeId: string) => void
  workflowProfile: FlowState["workflowProject"]["profile"]
  workbenchMode: WorkflowWorkbenchMode | null
  onChangeWorkbenchMode: (mode: WorkflowWorkbenchMode | null) => void
  wrapperRef: RefObject<HTMLDivElement | null>
  zoom: number
  setZoom: (zoom: number) => void
}

function minimapNodeColor(node: { selected?: boolean }) {
  return node.selected ? "#e8e8e6" : "#3a3d42"
}


function panOnDragValue(settings: CanvasSettings, interactionLocked: boolean) {
  return settings.panOnDrag && !interactionLocked ? [1, 2] : false
}

function flowInteractionProps(settings: CanvasSettings, interactionLocked: boolean) {
  if (settings.touchMode) {
    return { panOnDrag: [1, 2] as number[], panOnScroll: true, selectionOnDrag: false }
  }
  return {
    panOnDrag: panOnDragValue(settings, interactionLocked),
    panOnScroll: settings.panOnScroll && !interactionLocked,
    selectionOnDrag: settings.selectionOnDrag && !interactionLocked,
  }
}

/** Live zoom bridge so nodes can render at different detail levels. */
function ZoomProvider({ onZoom }: { onZoom: (z: number) => void }) {
  const zoom = useStore((s) => s.transform[2])
  useEffect(() => {
    onZoom(zoom)
  }, [zoom, onZoom])
  return null
}

function OptionalBackground({ visible }: { visible: boolean }) {
  if (!visible) return null
  return <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#26282c" />
}

function OptionalControls({ visible }: { visible: boolean }) {
  if (!visible) return null
  return <Controls className="!rounded-md !border !border-border !bg-card [&_button]:!border-border [&_button]:!bg-card [&_button:hover]:!bg-accent [&_button]:!fill-muted-foreground" />
}

function OptionalMiniMap({ visible }: { visible: boolean }) {
  if (!visible) return null
  return (
    <MiniMap
      pannable
      zoomable
      nodeColor={minimapNodeColor}
      className="!rounded-md !border !border-border !bg-card"
      maskColor="rgb(10 10 10 / 0.75)"
    />
  )
}

function NodeMenuOverlay({
  menu,
  onAddNode,
  onAddNote,
  onConnectPort,
  onImportApp,
  onTestRun,
  wrapperElement,
}: {
  menu: NodeMenuState | null
  onAddNode: () => void
  onAddNote: () => void
  onConnectPort: () => void
  onImportApp: () => void
  onTestRun: () => void
  wrapperElement: HTMLElement | null
}) {
  if (!menu) return null
  return (
    <NodeContextMenu
      menu={menu}
      onAddNode={onAddNode}
      onAddNote={onAddNote}
      onConnectPort={onConnectPort}
      onImportApp={onImportApp}
      onTestRun={onTestRun}
      wrapperElement={wrapperElement}
    />
  )
}

function CanvasLayers({
  helperLines,
  settings,
  setZoom,
}: {
  helperLines: FlowState["helperLines"]
  settings: CanvasSettings
  setZoom: (zoom: number) => void
}) {
  return (
    <>
      <ZoomProvider onZoom={setZoom} />
      <OptionalBackground visible={settings.showBackground} />
      <OptionalControls visible={settings.showControls} />
      <OptionalMiniMap visible={settings.showMiniMap} />
      <HelperLinesRenderer lines={helperLines} />
      <WorkflowMotionRuntime interaction={helperLines.interaction} />
      <DrawingLayer />
      <Collaboration />
    </>
  )
}

export function WorkflowCanvasSurface(props: WorkflowCanvasSurfaceProps) {
  const interactionLocked = props.isDraw || props.isScissors
  const flowInteraction = useMemo(
    () => flowInteractionProps(props.settings, interactionLocked),
    [props.settings, interactionLocked],
  )
  const fitViewOptions = useMemo(
    () => ({
      padding: props.compactViewport ? 0.24 : 0.15,
      minZoom: props.compactViewport ? 0.62 : 0.2,
    }),
    [props.compactViewport],
  )
  const inspectorVisible = props.inspectorOpen && !props.projectSettingsOpen && !props.settingsOpen
  return (
    <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
      <div
        ref={props.wrapperRef}
        className="relative min-w-0 flex-1"
        onMouseDownCapture={props.onCanvasMouseDownCapture}
        onMouseMoveCapture={props.onCanvasMouseMoveCapture}
        onMouseUpCapture={props.onCanvasMouseUpCapture}
        onMouseMove={props.onMouseMove}
        data-zoom-bucket={workflowNodeDensity(props.zoom, props.settings.contextualZoom)}
        data-wiring-state={props.wiringState}
      >
      <ReactFlow<WorkflowNode, WorkflowEdge>
        nodes={props.nodes}
        edges={props.edges}
        onNodesChange={props.onNodesChange}
        onEdgesChange={props.onEdgesChange}
        onConnect={props.onConnect}
        onConnectEnd={props.onConnectEnd}
        onConnectStart={props.onConnectStart}
        onClickConnectEnd={props.onClickConnectEnd}
        onClickConnectStart={props.onClickConnectStart}
        onReconnect={props.onReconnect}
        onReconnectEnd={props.onReconnectEnd}
        onReconnectStart={props.onReconnectStart}
        onNodeDragStart={props.takeSnapshot}
        onNodeDrag={props.onNodeDrag}
        onNodeDragStop={props.onNodeDragStop}
        onNodeClick={props.onNodeClick}
        onNodeDoubleClick={props.onNodeDoubleClick}
        onNodeContextMenu={props.onNodeContextMenu}
        onPaneContextMenu={props.onPaneContextMenu}
        onDrop={props.onDrop}
        onDragOver={props.onDragOver}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        fitView
        fitViewOptions={fitViewOptions}
        isValidConnection={props.isValidConnection}
        connectionDragThreshold={2}
        connectionRadius={32}
        reconnectRadius={24}
        edgesReconnectable
        autoPanOnConnect
        connectOnClick
        onBeforeDelete={props.onBeforeDelete}
        nodesDraggable={props.settings.nodesDraggable && !interactionLocked}
        nodesConnectable={props.settings.nodesConnectable && !props.isScissors}
        elementsSelectable={props.settings.elementsSelectable}
        zoomOnScroll={props.settings.zoomOnScroll}
        zoomOnPinch={props.settings.zoomOnPinch}
        zoomOnDoubleClick={props.settings.zoomOnDoubleClick}
        panOnScroll={flowInteraction.panOnScroll}
        panOnDrag={flowInteraction.panOnDrag}
        selectionOnDrag={flowInteraction.selectionOnDrag}
        selectionMode={SelectionMode.Partial}
        proOptions={proOptions}
        minZoom={0.2}
        maxZoom={2}
        className={cn("bg-background", props.isScissors && "cursor-crosshair")}
        data-tool-mode={props.toolMode}
      >
        <CanvasLayers helperLines={props.helperLines} settings={props.settings} setZoom={props.setZoom} />
      </ReactFlow>

      <ScissorTrailOverlay active={props.isScissors} points={props.scissorTrail} />
      <NetworkBreadcrumb locked={props.networkLocked} networkStack={props.networkStack} onExit={props.exitCurrentNetwork} />

      <NodeMenuOverlay
        menu={props.nodeMenu}
        onAddNode={props.onAddNodeFromMenu}
        onAddNote={props.onAddNoteFromMenu}
        onConnectPort={props.onConnectPortFromMenu}
        onImportApp={props.onImportApp}
        onTestRun={props.onTestRun}
        wrapperElement={props.wrapperRef.current}
      />

      <WorkflowFloatingPanels
        nodeManagementOpen={props.nodeManagementOpen}
        onCloseNodeManagement={() => props.setNodeManagementOpen(false)}
        onProfileChange={props.onProfileChange}
        projectSettingsOpen={props.projectSettingsOpen}
        runTraceOpen={props.runTraceOpen}
        runRequestId={props.runRequestId}
        settingsOpen={props.settingsOpen}
        workflowProfile={props.workflowProfile}
      />

      {props.workbenchMode ? (
        <WorkflowWorkbenchPanel
          nodes={props.nodes}
          edges={props.edges}
          onClose={() => props.onChangeWorkbenchMode(null)}
        />
      ) : null}

      <WorkflowToast message={props.toast} />
      </div>
      <div className={cn("h-full min-h-0 overflow-hidden", !inspectorVisible && "hidden")} aria-hidden={!inspectorVisible}>
        <Inspector compact={props.compactViewport} onClose={props.onCloseInspector} />
      </div>
    </div>
  )
}
