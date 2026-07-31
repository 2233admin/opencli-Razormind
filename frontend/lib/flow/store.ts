"use client"

import { create } from "zustand"
import { nanoid } from "nanoid"
import {
  type Connection,
  type EdgeChange,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react"
import type {
  WorkflowNode,
  WorkflowEdge,
  WorkflowNodeData,
  WorkflowEdgeData,
  FlowSnapshot,
  PaletteItem,
  FreehandStroke,
  ToolMode,
  GeneratedWorkflowSpec,
  ParameterInterface,
} from "./types"
import type { HelperLines } from "./helper-lines"
import { resolveCollisions, findFreePosition, nodeRect } from "./collision"
import type { LayoutDirection, LayoutEngine } from "./layout"
import { NODE_PALETTE } from "./palette"
import { createLayoutActions } from "./store-layout-actions"
import {
  createCanvasChangeActions,
  createEdgeActions,
  createHistoryActions,
  createSelectionActions,
  createWhiteboardActions,
} from "./store-slices"
import { snapshot, workflowNodeId } from "./store-utils"
import { PACKAGED_WORKFLOW_PROJECT } from "../workflow/collection-pipeline"
import type { WorkflowProject } from "../workflow/schema"
import { parseWorkflowProject, type AdapterBinding, type WorkflowProfile, type WorkflowProjectNode } from "../workflow/schema"
import { workflowNodeToReactFlow, workflowProjectToReactFlow } from "../workflow/to-react-flow"
import {
  addCatalogNodeToWorkflowProject,
  buildOpenCLIMultiSourceHDAInternals,
  isOpenCLISourceSlotArray,
  opencliAdaptersForSourceSlots,
  type WorkflowNodeCatalogItem,
} from "../workflow/node-catalog"
import { getNodeInternals, type NodeInternals, type NodeInternalStep } from "../workflow/node-internals"
import { getPrimitiveByStepCapability, primitiveToNodeData, type WorkflowPrimitive } from "../workflow/node-primitives"
import {
  createBackendParameterInterface,
  createDataOperatorParameterInterface,
  createParameterInterfaceFromInternals,
  parseDataOperatorSelectionValue,
  setParameterInterfaceFieldValue,
} from "../workflow/parameter-interface"
import {
  catalogRuntimeCapability,
  projectedCatalogRuntimeCapability,
  runtimeContractForCapability,
  type WorkflowCapabilitiesResponse,
  type WorkflowRuntimeCapability,
} from "../workflow/capabilities"
import type { AgentProposal } from "../workflow/proposal"
import type {
  WorkflowEvidenceBatchProjection,
  WorkflowEvidenceBatchSummary,
  WorkflowNodeRunEvent,
  WorkflowRunNodeState,
  WorkflowRunProjection,
  WorkflowRunStatus,
} from "../workflow/backend-runs"
import { applyEvidenceBatchRuntimePatches } from "../workflow/runtime-bridge"
import { MAX_WORKFLOW_NODE_DEPTH, NODE_NETWORK_DEPTH_LIMIT_REACHED } from "../workflow/node-hierarchy"
import {
  findWorkflowProjectNodeByCanvasId as findProjectNodeByCanvasId,
  mapWorkflowProjectNodeTree,
  normalizeWorkflowRuntimeNodePath,
  workflowProjectNodeChildren,
  workflowRuntimeCanvasNodeIds,
} from "../workflow/node-path"
import {
  appendCanonicalNetworkNode,
  canonicalPositionFromNetworkCanvas,
  updateCanonicalProjectNodeByCanvasId,
} from "./store-canonical-actions"
import type { FlowClipboard, FlowNetworkStackEntry, FlowStoreSnapshot } from "./store-utils"

export type { GeneratedWorkflowSpec } from "./types"

const STORAGE_KEY = "workflow-editor-state"
const initialWorkflowProject = PACKAGED_WORKFLOW_PROJECT
const initialWorkflowFlow = workflowProjectToReactFlow(initialWorkflowProject)

export function clearParameterDraftEntry<T>(
  values: Record<string, T>,
  nodeId: string,
  fieldId: string,
): Record<string, T> {
  const key = `${nodeId}:${fieldId}`
  if (!(key in values)) return values
  const updated = { ...values }
  delete updated[key]
  return updated
}

export type FlowState = {
  workflowProject: WorkflowProject
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  networkStack: FlowNetworkStackEntry[]
  helperLines: HelperLines
  past: FlowStoreSnapshot[]
  future: FlowStoreSnapshot[]
  clipboard: FlowClipboard | null
  selectedIds: string[]
  pendingAgentProposal: AgentProposal | null

  // freehand whiteboard
  drawings: FreehandStroke[]
  toolMode: ToolMode
  penColor: string
  penSize: number
  setToolMode: (mode: ToolMode) => void
  setPenColor: (color: string) => void
  setPenSize: (size: number) => void
  addStroke: (stroke: FreehandStroke) => void
  clearDrawings: () => void

  onNodesChange: (changes: NodeChange<WorkflowNode>[]) => void
  onEdgesChange: (changes: EdgeChange<WorkflowEdge>[]) => void
  onConnect: (connection: Connection) => void
  connectNodes: (connection: Connection, options?: { suppressSnapshot?: boolean }) => void
  onReconnect: (oldEdge: WorkflowEdge, connection: Connection) => void

  takeSnapshot: () => void
  undo: () => void
  redo: () => void
  canUndo: () => boolean
  canRedo: () => boolean

  addNodeFromPalette: (item: PaletteItem, position: XYPosition) => void
  addPrimitiveNode: (
    item: WorkflowPrimitive,
    position: XYPosition,
    runtimeCapability?: WorkflowRuntimeCapability,
    options?: { suppressSnapshot?: boolean },
  ) => void
  addPrimitiveToNodeNetwork: (
    nodeId: string,
    item: WorkflowPrimitive,
    position: XYPosition,
    runtimeCapability?: WorkflowRuntimeCapability,
  ) => number
  addWorkflowNodeFromCatalog: (item: WorkflowNodeCatalogItem, position: XYPosition) => void
  updateWorkflowNodeParams: (
    nodeId: string,
    paramsPatch: Record<string, unknown>,
    adapterPatch?: Partial<Pick<AdapterBinding, "mode" | "config">>,
  ) => void
  updateParameterInterfaceField: (nodeId: string, fieldId: string, value: unknown) => void
  updateNodeData: (id: string, data: Partial<WorkflowNodeData>) => void
  deleteSelected: () => void
  disconnectSelectedConnections: () => number
  disconnectNodeConnections: (nodeId: string, options?: { suppressSnapshot?: boolean }) => number
  removeEdgesByIds: (edgeIds: string[], options?: { suppressSnapshot?: boolean }) => number
  selectConnectedComponent: (nodeId: string) => { nodeIds: string[]; edgeIds: string[] }
  duplicateSelected: () => void

  copy: () => void
  cut: () => void
  paste: (position?: XYPosition) => void

  autoLayout: (direction: LayoutDirection, engine?: LayoutEngine, animated?: boolean) => Promise<void>
  toggleGroupCollapse: (id: string) => void

  // grouping / parent-child
  groupSelection: () => void
  ungroupSelection: () => void
  attachToParent: (childId: string, parentId: string) => void
  detachFromParent: (childId: string) => void

  // editable edges
  updateEdgeWaypoints: (edgeId: string, waypoints: XYPosition[]) => void
  updateEdgeData: (edgeId: string, data: Partial<WorkflowEdgeData>) => void
  updateEdgeType: (edgeId: string, type: string) => void
  toggleEdgeAnimated: (edgeId: string) => void

  // dynamic layouting helpers
  addChildNode: (parentId: string) => void
  insertNodeOnEdge: (edgeId: string) => void
  enterNodeNetwork: (nodeId: string) => number
  enterNodeNetworkForEdit: (nodeId: string, options?: { suppressSnapshot?: boolean }) => number
  exitNodeNetwork: () => boolean
  unlockNodeInternals: (nodeId: string) => number
  lockNodeInternals: (nodeId: string) => number

  // collision & group maintenance
  resolveNodeCollisions: (movedId: string) => void
  resizeGroupToFit: (groupId: string) => void

  setNodes: (updater: (nodes: WorkflowNode[]) => WorkflowNode[]) => void
  setSelectedIds: (ids: string[]) => void
  clearHelperLines: () => void

  save: () => void
  load: () => boolean
  reset: () => void
  importFlow: (snapshot: FlowSnapshot) => void
  importWorkflowProject: (project: WorkflowProject) => void
  applyWorkflowCapabilities: (capabilities: WorkflowCapabilitiesResponse) => void
  applyWorkflowNodeRunEvent: (event: WorkflowNodeRunEvent) => void
  applyWorkflowRunProjection: (projection: WorkflowRunProjection) => void
  applyWorkflowEvidenceBatchProjection: (
    projection: WorkflowEvidenceBatchProjection,
    batches: WorkflowEvidenceBatchSummary[],
  ) => void
  updateWorkflowProfile: (profile: WorkflowProfile) => void
  queueAgentProposal: (proposal: AgentProposal) => void
  clearPendingAgentProposal: () => void
  focusProposalTargets: (nodeIds: string[], edgeIds?: string[]) => void
  clearProposalFocus: () => void
  applyGeneratedWorkflow: (spec: GeneratedWorkflowSpec) => void
}

function uniqueWorkflowNodeId(prefix: string, nodes: WorkflowProject["nodes"]): string {
  const ids = new Set(nodes.map((node) => node.id))
  let candidate = prefix
  let i = 2
  while (ids.has(candidate)) {
    candidate = `${prefix}-${i}`
    i += 1
  }
  return candidate
}

function canonicalNodeFromPrimitive(
  item: WorkflowPrimitive,
  id: string,
  canvasPosition: XYPosition,
  runtimeCapability?: WorkflowRuntimeCapability,
): WorkflowProjectNode {
  const primitiveData = primitiveToNodeData(item, runtimeCapability)
  return {
    id,
    kind: item.canonical.kind,
    capability: item.canonical.capability,
    params: {
      ...Object.fromEntries(item.fields.map((field) => [field.id, field.value])),
      io: {
        inputs: item.canonical.inputPorts,
        outputs: item.canonical.outputPorts,
      },
    },
    sourceAnchor: primitiveData.sourceAnchor,
    runArtifact: primitiveData.runArtifact,
    miniNetwork: primitiveData.miniNetwork,
    topicCollapse: primitiveData.topicCollapse,
    proposalState: primitiveData.proposalState,
    ui: {
      label: item.label,
      description: item.description,
      icon: item.icon,
      color: item.color,
      position: canonicalPositionFromNetworkCanvas(canvasPosition),
      catalogId: item.id,
      primitiveId: item.id,
      primitiveCategory: item.category,
      primitivePorts: item.ports,
      runtimeCapability: primitiveData.runtimeCapability,
    },
  }
}

function canonicalInternalsFromFallback(internals: NodeInternals): NonNullable<WorkflowProjectNode["internals"]> {
  const nodes = internals.steps.map((stepItem, index) => {
    const primitiveItem = getPrimitiveByStepCapability(stepItem.capability)
    const node = canonicalNodeFromPrimitive(
      primitiveItem,
      stepItem.id,
      { x: 520, y: 80 + index * 140 },
    )
    return {
      ...node,
      params: {
        ...node.params,
        stepCapability: stepItem.capability,
        evidence: stepItem.evidence,
        ...Object.fromEntries(
          (stepItem.exposedParams ?? []).map((param) => [param.binding?.fieldId ?? param.id, param.value]),
        ),
      },
      ui: {
        ...node.ui,
        label: stepItem.label,
        description: stepItem.description,
        internalStatus: stepItem.status,
      },
    }
  })
  const edges = nodes.slice(0, -1).map((node, index) => {
    const target = nodes[index + 1]
    const sourcePrimitive = getPrimitiveByStepCapability(internals.steps[index].capability)
    const targetPrimitive = getPrimitiveByStepCapability(internals.steps[index + 1].capability)
    return {
      id: `fallback-${node.id}-${target.id}`,
      source: node.id,
      target: target.id,
      sourcePort: sourcePrimitive.canonical.outputPorts[0]?.id,
      targetPort: targetPrimitive.canonical.inputPorts[0]?.id,
    }
  })
  return { locked: false, nodes, edges }
}

function canonicalParameterInterface(
  projectNode: WorkflowProjectNode,
  fallback: NodeInternals | undefined,
): ParameterInterface | undefined {
  const parameterInterface =
    projectNode.parameterInterface ?? createParameterInterfaceFromInternals(projectNode.id, fallback)
  if (!parameterInterface) return undefined
  const childIds = new Set(
    (projectNode.internals?.nodes ?? []).filter(isWorkflowProjectNode).map((node) => node.id),
  )
  if (fallback) {
    for (const step of fallback.steps) childIds.add(step.id)
  }
  return {
    ...parameterInterface,
    groups: [...parameterInterface.groups],
    fields: parameterInterface.fields.map((field) => {
      const localChildId = Array.from(childIds).find(
        (childId) => field.binding.nodeId === childId || field.binding.nodeId.endsWith(`__${childId}`),
      )
      return localChildId
        ? { ...field, binding: { ...field.binding, nodeId: localChildId } }
        : field
    }),
  }
}

function ensureCanonicalNodeInternals(
  project: WorkflowProject,
  canvasNodeId: string,
  projectNode: WorkflowProjectNode,
): { project: WorkflowProject; projectNode: WorkflowProjectNode } {
  const fallback = getNodeInternals(projectNode)
  const internals =
    projectNode.internals ??
    (fallback ? canonicalInternalsFromFallback(fallback) : { locked: false, nodes: [], edges: [] })
  const parameterInterface = canonicalParameterInterface({ ...projectNode, internals }, fallback)
  const nextProject = parseWorkflowProject(
    updateCanonicalProjectNodeByCanvasId(project, canvasNodeId, (node) => ({
      ...node,
      parameterInterface,
      internals,
    })),
  )
  return {
    project: nextProject,
    projectNode: findProjectNodeByCanvasId(nextProject, canvasNodeId) ?? projectNode,
  }
}

function isWorkflowRuntimeCapability(value: unknown): value is WorkflowRuntimeCapability {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return typeof record.id === "string" && typeof record.status === "string"
}

function workflowNodeStatusFromRun(status: WorkflowRunStatus): WorkflowNodeData["status"] {
  switch (status) {
    case "queued":
      return "idle"
    case "running":
    case "waiting":
    case "partial":
      return status === "waiting" ? "waiting" : "running"
    case "completed":
      return "success"
    case "partial_success":
      return "partial_success"
    case "blocked":
    case "failed":
      return "error"
    default:
      return "idle"
  }
}

function workflowNodeStatusFromEvent(eventType: WorkflowNodeRunEvent["eventType"]): WorkflowNodeData["status"] {
  switch (eventType) {
    case "queued":
      return "idle"
    case "started":
    case "waiting":
    case "partial":
    case "batch_ready":
      return eventType === "waiting" ? "waiting" : "running"
    case "completed":
      return "success"
    case "blocked":
    case "failed":
      return "error"
    default:
      return "idle"
  }
}

function runStateForEvent(event: WorkflowNodeRunEvent): WorkflowRunNodeState {
  return {
    nodeId: event.nodeId,
    nodePath: event.nodePath,
    status:
      event.eventType === "queued"
        ? "queued"
        : event.eventType === "waiting"
          ? "waiting"
        : event.eventType === "completed"
          ? "completed"
          : event.eventType === "blocked"
            ? "blocked"
            : event.eventType === "failed"
              ? "failed"
              : event.eventType === "batch_ready" || event.eventType === "partial"
                ? "partial"
                : "running",
    packageNodeId: event.packageNodeId,
    internalNodeId: event.internalNodeId,
    sourceGroups: event.sourceGroup ? [event.sourceGroup] : [],
    latestEventId: event.id,
    eventCount: 1,
    blockReasons: event.blockReason ? [event.blockReason] : [],
    batches: event.batch ? [event.batch] : [],
  }
}

function runtimeNodeIdCandidates(
  nodeId: string,
  packageNodeId?: string | null,
  internalNodeId?: string | null,
  nodePath?: readonly string[] | null,
): string[] {
  return workflowRuntimeCanvasNodeIds({ nodeId, nodePath, packageNodeId, internalNodeId })
}

function runtimeStateByCanvasNodeId(projection: WorkflowRunProjection): Map<string, WorkflowRunNodeState> {
  const byCanvasNodeId = new Map<string, WorkflowRunNodeState>()
  const exactNodeIds = new Set<string>()
  for (const nodeState of projection.nodeStates) {
    const exactNodeId = normalizeWorkflowRuntimeNodePath({
      nodeId: nodeState.nodeId,
      nodePath: nodeState.nodePath,
      packageNodeId: nodeState.packageNodeId,
      internalNodeId: nodeState.internalNodeId,
    }).join("__")
    exactNodeIds.add(exactNodeId)
    byCanvasNodeId.set(exactNodeId, nodeState)
  }
  for (const nodeState of projection.nodeStates) {
    if (nodeState.status !== "blocked" && nodeState.status !== "failed") continue
    const exactNodeId = normalizeWorkflowRuntimeNodePath({
      nodeId: nodeState.nodeId,
      nodePath: nodeState.nodePath,
      packageNodeId: nodeState.packageNodeId,
      internalNodeId: nodeState.internalNodeId,
    }).join("__")
    for (const candidate of runtimeNodeIdCandidates(
      nodeState.nodeId,
      nodeState.packageNodeId,
      nodeState.internalNodeId,
      nodeState.nodePath,
    )) {
      if (candidate === exactNodeId || exactNodeIds.has(candidate)) continue
      const current = byCanvasNodeId.get(candidate)
      if (!current || runtimeFailureSeverity(nodeState.status) > runtimeFailureSeverity(current.status)) {
        byCanvasNodeId.set(candidate, nodeState)
      }
    }
  }
  return byCanvasNodeId
}

function runtimeFailureSeverity(status: WorkflowRunStatus): number {
  return status === "failed" ? 2 : status === "blocked" ? 1 : 0
}

function patchProjectNodeRunEvent(
  node: WorkflowProjectNode,
  event: WorkflowNodeRunEvent,
  runtimeRunState: WorkflowRunNodeState,
): WorkflowProjectNode {
  const targets = new Set(
    runtimeNodeIdCandidates(event.nodeId, event.packageNodeId, event.internalNodeId, event.nodePath),
  )
  return patchProjectNodeRunEventAtPath(node, "", targets, event, runtimeRunState)
}

function patchProjectNodeRunEventAtPath(
  node: WorkflowProjectNode,
  canvasParentId: string,
  targets: ReadonlySet<string>,
  event: WorkflowNodeRunEvent,
  runtimeRunState: WorkflowRunNodeState,
): WorkflowProjectNode {
  const canvasNodeId = canvasParentId ? scopedInternalId(canvasParentId, node.id) : node.id
  const patchedChildren = node.internals?.nodes.map((child) =>
    isWorkflowProjectNode(child)
      ? patchProjectNodeRunEventAtPath(child, canvasNodeId, targets, event, runtimeRunState)
      : child,
  )
  const childrenChanged = Boolean(
    patchedChildren?.some((child, index) => child !== node.internals?.nodes[index]),
  )
  const matchesNode = targets.has(canvasNodeId)
  if (!matchesNode && !childrenChanged) return node

  return {
    ...node,
    ...(matchesNode
      ? {
          ui: {
            ...(node.ui ?? {}),
            runtimeRunState,
            runtimeLatestEvent: event,
          },
        }
      : {}),
    ...(node.internals && patchedChildren
      ? {
          internals: {
            ...node.internals,
            nodes: patchedChildren,
          },
        }
      : {}),
  }
}

function patchProjectNodeRunProjection(
  node: WorkflowProjectNode,
  stateByCanvasNodeId: Map<string, WorkflowRunNodeState>,
): WorkflowProjectNode {
  return patchProjectNodeRunProjectionAtPath(node, "", stateByCanvasNodeId)
}

function patchProjectNodeRunProjectionAtPath(
  node: WorkflowProjectNode,
  canvasParentId: string,
  stateByCanvasNodeId: Map<string, WorkflowRunNodeState>,
): WorkflowProjectNode {
  const canvasNodeId = canvasParentId ? scopedInternalId(canvasParentId, node.id) : node.id
  const runtimeRunState = stateByCanvasNodeId.get(canvasNodeId)
  const patchedChildren = node.internals?.nodes.map((child) =>
    isWorkflowProjectNode(child)
      ? patchProjectNodeRunProjectionAtPath(child, canvasNodeId, stateByCanvasNodeId)
      : child,
  )
  const childrenChanged = Boolean(
    patchedChildren?.some((child, index) => child !== node.internals?.nodes[index]),
  )
  if (!runtimeRunState && !childrenChanged) return node

  return {
    ...node,
    ...(runtimeRunState
      ? {
          ui: {
            ...(node.ui ?? {}),
            runtimeRunState,
          },
        }
      : {}),
    ...(node.internals && patchedChildren
      ? {
          internals: {
            ...node.internals,
            nodes: patchedChildren,
          },
        }
      : {}),
  }
}

function fieldsForInternalStep(stepItem: NodeInternalStep, parentNodeId?: string, parameterInterface?: ParameterInterface) {
  return [
    { id: "capability", label: "capability", value: stepItem.capability },
    { id: "evidence", label: "evidence", value: stepItem.evidence },
    ...(stepItem.exposedParams ?? []).map((param) => {
      const fieldId = param.binding?.fieldId ?? param.id
      const boundValue = parameterInterface?.fields.find(
        (field) =>
          (field.binding.nodeId === stepItem.id || field.binding.nodeId === `${parentNodeId}__${stepItem.id}`) &&
          field.binding.fieldId === fieldId,
      )?.value
      return {
        id: fieldId,
        label: param.label,
        value: String(boundValue ?? param.value ?? ""),
      }
    }),
  ]
}

type InternalWorkflowEdge = {
  id: string
  source: string
  target: string
  sourcePort?: string
  targetPort?: string
  label?: string
  ui?: Record<string, unknown>
}

function layoutOpenCLISourcePool(rawNodes: WorkflowProjectNode[]): Map<string, XYPosition> {
  const sourcePool = rawNodes.find((node) => node.id === "source-pool")
  const sources = rawNodes.filter((node) => typeof node.params?.sourceGroup === "string")
  if (!sourcePool || sources.length < 2) return new Map()

  const columns = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(sources.length))))
  const positions = new Map<string, XYPosition>()
  let slot = 0
  let previousGroup: string | undefined

  for (const source of sources) {
    const group = source.params.sourceGroup as string
    if (previousGroup && group !== previousGroup) slot = Math.ceil(slot / columns) * columns
    positions.set(source.id, {
      x: 320 + (slot % columns) * 300,
      y: Math.floor(slot / columns) * 150,
    })
    previousGroup = group
    slot += 1
  }

  const sourcePositions = [...positions.values()]
  const minY = Math.min(...sourcePositions.map((position) => position.y))
  const maxY = Math.max(...sourcePositions.map((position) => position.y))
  positions.set(sourcePool.id, { x: 0, y: (minY + maxY) / 2 })
  return positions
}

function materializeProjectInternals(
  projectNode: WorkflowProjectNode | undefined,
  parentNode: WorkflowNode,
  canvasParentId: string,
  mode: "network" | "unlock",
): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } | undefined {
  const rawNodes = projectNode?.internals?.nodes.filter(isWorkflowProjectNode) ?? []
  if (!projectNode || rawNodes.length === 0) return undefined

  const explicitEdges = projectNode.internals?.edges.filter(isInternalWorkflowEdge) ?? []
  const rawEdges = explicitEdges.length > 0 ? explicitEdges : inferNormalizeFanoutEdges(rawNodes)
  const parentId = canvasParentId
  const networkLocked = projectNode.internals?.locked === true
  const parentRect = nodeRect(parentNode)
  const origin =
    mode === "network"
      ? { x: 520, y: 80 }
      : { x: parentRect.x, y: parentRect.y + parentRect.height + 110 }
  const title = typeof projectNode.ui?.label === "string" ? projectNode.ui.label : parentId
  const sourcePoolPositions = layoutOpenCLISourcePool(rawNodes)

  const internalNodes = rawNodes.map((internalNode, index) => {
    const normalizedNode: WorkflowProjectNode = { ...internalNode, params: internalNode.params ?? {} }
    const reactNode = workflowNodeToReactFlow(normalizedNode, index)
    const relativePosition = sourcePoolPositions.get(normalizedNode.id) ?? readInternalPosition(normalizedNode, index)
    const catalogId = typeof normalizedNode.ui?.catalogId === "string" ? normalizedNode.ui.catalogId : undefined
    const primitivePorts = Array.isArray(normalizedNode.ui?.primitivePorts)
      ? normalizedNode.ui.primitivePorts
      : undefined
    return {
      ...reactNode,
      id: scopedInternalId(parentId, normalizedNode.id),
      type: "workflow" as const,
      draggable: mode === "network" ? !networkLocked : reactNode.draggable,
      connectable: mode === "network" ? !networkLocked : reactNode.connectable,
      position: {
        x: origin.x + relativePosition.x,
        y: origin.y + relativePosition.y,
      },
      data: {
        ...reactNode.data,
        status: reactNode.data.status,
        internalOf: parentId,
        internalStepId: normalizedNode.id,
        ...(catalogId?.startsWith("primitive.")
          ? {
              primitiveId: catalogId,
              primitiveCategory: normalizedNode.ui?.primitiveCategory,
              primitivePorts,
            }
          : {}),
        internalStatus: "ready",
        internalLocked: mode === "network" && networkLocked,
        ...(mode === "network"
          ? { networkTitle: title }
          : { internalDraft: true, packageDraft: true }),
      },
    }
  })

  const nodeIds = new Set(rawNodes.map((node) => node.id))
  const internalEdges = rawEdges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => ({
      id: `e-${parentId}__${edge.id}`,
      source: scopedInternalId(parentId, edge.source),
      target: scopedInternalId(parentId, edge.target),
      sourceHandle: edge.sourcePort,
      targetHandle: edge.targetPort,
      label: edge.label,
      type: "workflow" as const,
      animated: true,
      data: {
        ...(edge.ui ?? {}),
        label: edge.label,
        internalOf: parentId,
        internalEdgeId: edge.id,
        sourcePort: edge.sourcePort,
        targetPort: edge.targetPort,
      },
    }))

  return { nodes: internalNodes, edges: internalEdges }
}

function scopedInternalId(parentId: string, internalId: string): string {
  return `${parentId}__${internalId}`
}

function readInternalPosition(node: WorkflowProjectNode, index: number): XYPosition {
  const value = node.ui?.position
  if (!value || typeof value !== "object" || Array.isArray(value)) return { x: 0, y: index * 140 }
  const position = value as { x?: unknown; y?: unknown }
  if (typeof position.x !== "number" || typeof position.y !== "number") return { x: 0, y: index * 140 }
  return { x: position.x, y: position.y }
}

function isWorkflowProjectNode(value: unknown): value is WorkflowProjectNode {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const node = value as Partial<WorkflowProjectNode>
  return (
    typeof node.id === "string" &&
    typeof node.kind === "string" &&
    typeof node.capability === "string" &&
    (!("params" in node) || Boolean(node.params && typeof node.params === "object" && !Array.isArray(node.params)))
  )
}

function projectNodeFromCanvasNode(node: WorkflowNode): WorkflowProjectNode | undefined {
  const canonical = node.data.canonical
  if (!canonical) return undefined
  return {
    id: node.data.internalStepId ?? node.id,
    kind: canonical.kind,
    capability: canonical.capability,
    adapter: canonical.adapter,
    params: canonical.params ?? {},
    parameterInterface: node.data.parameterInterface,
    ui: {
      label: node.data.label,
      description: node.data.description,
      catalogId: canonical.catalogId,
    },
  }
}

function isInternalWorkflowEdge(value: unknown): value is InternalWorkflowEdge {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const edge = value as Partial<InternalWorkflowEdge>
  return typeof edge.id === "string" && typeof edge.source === "string" && typeof edge.target === "string"
}

function inferNormalizeFanoutEdges(nodes: WorkflowProjectNode[]): InternalWorkflowEdge[] {
  const normalize = nodes.find((node) => node.id === "internal-normalize")
  if (!normalize) return []
  return nodes
    .filter((node) => node.id !== normalize.id)
    .map((node) => ({
      id: `${node.id}-normalize`,
      source: node.id,
      target: normalize.id,
    }))
}

function scheduleNetworkInternalEdges(
  getState: () => FlowState,
  setState: (state: Partial<FlowState>) => void,
  parentId: string,
  nodeIds: string[],
  edges: WorkflowEdge[],
) {
  if (edges.length === 0 || typeof window === "undefined") return
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      const state = getState()
      const stillInNetwork = nodeIds.every((id) => state.nodes.some((node) => node.id === id && node.data.internalOf === parentId))
      if (!stillInNetwork) return
      setState({ edges })
    })
  })
}

function scheduleUnlockedInternalEdges(
  getState: () => FlowState,
  setState: (updater: Partial<FlowState>) => void,
  parentId: string,
  nodeIds: string[],
  edges: WorkflowEdge[],
) {
  if (edges.length === 0 || typeof window === "undefined") return
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      const state = getState()
      const stillUnlocked = nodeIds.every((id) => state.nodes.some((node) => node.id === id && node.data.internalOf === parentId))
      if (!stillUnlocked) return
      const existingEdgeIds = new Set(state.edges.map((edge) => edge.id))
      const missingEdges = edges.filter((edge) => !existingEdgeIds.has(edge.id))
      if (missingEdges.length === 0) return
      setState({ edges: [...state.edges, ...missingEdges] })
    })
  })
}

function writeBoundValueToNode(
  node: WorkflowNode,
  source: "params" | "adapter" | "data",
  fieldId: string,
  value: unknown,
): WorkflowNode {
  if (source === "params" || source === "adapter") {
    const nextValue =
      value && typeof value === "object"
        ? JSON.stringify(value)
        : String(value ?? "")
    const fields = node.data.fields ?? []
    const hasField = fields.some((field) => field.id === fieldId)
    return {
      ...node,
      data: {
        ...node.data,
        fields: hasField
          ? fields.map((field) => (field.id === fieldId ? { ...field, value: nextValue } : field))
          : [...fields, { id: fieldId, label: fieldId, value: nextValue }],
      },
    }
  }
  if (source === "data") return { ...node, data: { ...node.data, [fieldId]: value } }
  return node
}

function configKeyFromBinding(fieldId: string): string | undefined {
  return fieldId.startsWith("config.") ? fieldId.slice("config.".length) || undefined : undefined
}

function configRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export const useFlowStore = create<FlowState>((set, get) => ({
  workflowProject: initialWorkflowProject,
  nodes: initialWorkflowFlow.nodes,
  edges: initialWorkflowFlow.edges,
  networkStack: [],
  helperLines: { snapPosition: {} },
  past: [],
  future: [],
  clipboard: null,
  selectedIds: [],
  pendingAgentProposal: null,
  drawings: [],
  toolMode: "select",
  penColor: "var(--chart-1)",
  penSize: 4,

  ...createWhiteboardActions(set, get),
  ...createCanvasChangeActions(set, get),
  ...createHistoryActions(set, get),

  addNodeFromPalette: (item, position) => {
    get().takeSnapshot()
    const id = nanoid(8)
    const isGroup = item.nodeType === "group"
    const isShape = item.nodeType === "shape"
    const rfType =
      item.nodeType === "group"
        ? "group"
        : item.nodeType === "note"
          ? "note"
          : item.nodeType === "shape"
            ? "shape"
            : "workflow"
    const size = isGroup ? { width: 320, height: 220 } : isShape ? { width: 140, height: 100 } : { width: 240, height: 96 }
    const freePos = isGroup ? position : findFreePosition(get().nodes, position, size)
    const newNode: WorkflowNode = {
      id,
      type: rfType,
      position: freePos,
      data: {
        label: item.label,
        nodeType: item.nodeType,
        category: item.category,
        icon: item.icon,
        color: item.color,
        status: "idle",
        ...(item.shape ? { shape: item.shape } : {}),
        ...item.defaultData,
      },
      ...(isGroup ? { width: 320, height: 220, style: { width: 320, height: 220 } } : {}),
      ...(isShape ? { width: 140, height: 100, style: { width: 140, height: 100 } } : {}),
    }
    // 分组容器必须排在数组最前，保证 React Flow 的 parent-before-child 顺序
    set((state) => ({
      nodes: isGroup
        ? [{ ...newNode, selected: true }, ...state.nodes.map((node) => ({ ...node, selected: false }))]
        : [...state.nodes.map((node) => ({ ...node, selected: false })), { ...newNode, selected: true }],
    }))
  },

  addPrimitiveNode: (item, position, runtimeCapability, options) => {
    if (!options?.suppressSnapshot) get().takeSnapshot()
    const { workflowProject, nodes, networkStack } = get()
    const localId = `${item.idPrefix}-${workflowNodeId(6)}`
    const freePos = findFreePosition(nodes, position, { width: 196, height: 78 })
    const parentNetwork = networkStack.at(-1)
    const canonicalNode = canonicalNodeFromPrimitive(item, localId, freePos, runtimeCapability)
    const id = parentNetwork ? scopedInternalId(parentNetwork.nodeId, localId) : localId
    const newNode: WorkflowNode = {
      id,
      type: "workflow",
      position: freePos,
      data: {
        ...primitiveToNodeData(item, runtimeCapability),
        ...(parentNetwork
          ? {
              canonical: {
                kind: canonicalNode.kind,
                capability: canonicalNode.capability,
                params: canonicalNode.params,
                catalogId: item.id,
              },
              internalOf: parentNetwork.nodeId,
              internalStepId: localId,
              packageDraft: true,
            }
          : { packageDraft: true }),
      },
    }
    let projectForEdit = workflowProject
    if (parentNetwork) {
      const parentProjectNode = findProjectNodeByCanvasId(projectForEdit, parentNetwork.nodeId)
      if (parentProjectNode && !parentProjectNode.internals) {
        projectForEdit = ensureCanonicalNodeInternals(
          projectForEdit,
          parentNetwork.nodeId,
          parentProjectNode,
        ).project
      }
    }
    const nextProject = parentNetwork
      ? parseWorkflowProject(appendCanonicalNetworkNode(projectForEdit, parentNetwork.nodeId, canonicalNode))
      : workflowProject
    set({
      workflowProject: nextProject,
      nodes: [...nodes.map((node) => ({ ...node, selected: false })), { ...newNode, selected: true }],
    })
  },

  addPrimitiveToNodeNetwork: (nodeId, item, position, runtimeCapability) => {
    const state = get()
    if (state.networkStack.length >= MAX_WORKFLOW_NODE_DEPTH - 1) {
      return NODE_NETWORK_DEPTH_LIMIT_REACHED
    }
    if (
      !state.nodes.some((candidate) => candidate.id === nodeId) ||
      !findProjectNodeByCanvasId(state.workflowProject, nodeId)
    ) {
      return 0
    }

    get().takeSnapshot()
    const count = get().enterNodeNetworkForEdit(nodeId, { suppressSnapshot: true })
    if (count <= 0) return count
    get().addPrimitiveNode(item, position, runtimeCapability, { suppressSnapshot: true })
    return count
  },

  addWorkflowNodeFromCatalog: (item, position) => {
    get().takeSnapshot()
    const { workflowProject, nodes } = get()
    const id = uniqueWorkflowNodeId(item.idPrefix, workflowProject.nodes)
    const project = addCatalogNodeToWorkflowProject(workflowProject, item, id, position)
    const node = project.nodes.find((candidate) => candidate.id === id)
    if (!node) return
    set({
      workflowProject: project,
      nodes: [...nodes.map((candidate) => ({ ...candidate, selected: false })), { ...workflowNodeToReactFlow(node, nodes.length), selected: true }],
    })
  },

  updateWorkflowNodeParams: (nodeId, paramsPatch, adapterPatch) => {
    get().takeSnapshot()
    set((state) => {
      const target = findProjectNodeByCanvasId(state.workflowProject, nodeId)
      if (!target) return {}
      const nextParams = { ...target.params, ...paramsPatch }
      const nextSources =
        nextParams.template === "opencli-multi-source" && isOpenCLISourceSlotArray(nextParams.sources)
          ? nextParams.sources
          : undefined
      const sourceAdapters = nextSources ? opencliAdaptersForSourceSlots(nextSources) : []
      const sourceAdapterIds = new Set(sourceAdapters.map((adapter) => adapter.id))

      const nextProject = parseWorkflowProject({
        ...state.workflowProject,
        adapters: [
          ...state.workflowProject.adapters.map((adapter) => {
            if (!target.adapter || adapter.id !== target.adapter || !adapterPatch) return adapter
            return {
              ...adapter,
              ...(adapterPatch.mode ? { mode: adapterPatch.mode } : {}),
              ...(adapterPatch.config ? { config: { ...adapter.config, ...adapterPatch.config } } : {}),
            }
          }),
          ...sourceAdapters.filter(
            (adapter) =>
              sourceAdapterIds.has(adapter.id) &&
              !state.workflowProject.adapters.some((existing) => existing.id === adapter.id),
          ),
        ],
        nodes: updateCanonicalProjectNodeByCanvasId(state.workflowProject, nodeId, (node) => ({
          ...node,
          params: { ...node.params, ...paramsPatch },
          ...(nextSources ? { internals: buildOpenCLIMultiSourceHDAInternals(nextSources) } : {}),
        })).nodes,
      })
      const nextNode = findProjectNodeByCanvasId(nextProject, nodeId)
      if (!nextNode) return { workflowProject: nextProject }
      const projected = workflowNodeToReactFlow(nextNode, state.nodes.findIndex((node) => node.id === nodeId))
      return {
        workflowProject: nextProject,
        nodes: state.nodes.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...node.data,
                  fields: projected.data.fields,
                  condition:
                    nextNode.capability === "route" && typeof nextNode.params.expression === "string"
                      ? nextNode.params.expression
                      : node.data.condition,
                  canonical: projected.data.canonical,
                },
              }
            : node,
        ),
      }
    })
  },

  updateParameterInterfaceField: (nodeId, fieldId, value) => {
    get().takeSnapshot()
    set((state) => {
      const parentNode = state.nodes.find((node) => node.id === nodeId)
      const parentProjectNode = findProjectNodeByCanvasId(state.workflowProject, nodeId)
      const inferredParameterInterface = parentProjectNode
        ? canonicalParameterInterface(parentProjectNode, getNodeInternals(parentProjectNode))
        : undefined
      const parameterInterface =
        parentProjectNode?.parameterInterface ??
        parentNode?.data.parameterInterface ??
        inferredParameterInterface
      const targetField = parameterInterface?.fields.find((field) => field.id === fieldId)
      if (!parentProjectNode || !parameterInterface || !targetField || targetField.readonly) return {}

      const binding = targetField.binding
      const configKey = binding.source === "params"
        ? configKeyFromBinding(binding.fieldId)
        : undefined
      const parentCatalogId = typeof parentProjectNode.ui?.catalogId === "string"
        ? parentProjectNode.ui.catalogId
        : ""
      const resetOperatorConfig =
        parentCatalogId.startsWith("intelligence.data.") &&
        binding.source === "params" &&
        binding.fieldId === "operatorId" &&
        parameterInterface.fields.some(
          (field) =>
            field.binding.source === "params" &&
            (field.binding.fieldId === "config" || configKeyFromBinding(field.binding.fieldId) !== undefined),
        )
      const operatorSelection = resetOperatorConfig
        ? parseDataOperatorSelectionValue(value)
        : undefined
      if (resetOperatorConfig && !operatorSelection) return {}
      const updatedParameterInterface = setParameterInterfaceFieldValue(parameterInterface, fieldId, value)
      const nextParameterInterface = resetOperatorConfig
        ? createDataOperatorParameterInterface(
            parentProjectNode.id,
            parentCatalogId,
            {
              operatorId: operatorSelection?.operatorId,
              packVersion: operatorSelection?.packVersion,
              config: {},
            },
            parentNode?.data.runtimeCapability,
          ) ?? updatedParameterInterface
        : updatedParameterInterface
      const bindsParent = binding.nodeId === parentProjectNode.id || binding.nodeId === nodeId
      const directBindingNode = bindsParent
        ? parentProjectNode
        : findProjectNodeByCanvasId(state.workflowProject, binding.nodeId)
      const localBindingNodeId = scopedInternalId(nodeId, binding.nodeId)
      const localBindingNode = findProjectNodeByCanvasId(state.workflowProject, localBindingNodeId)
      const legacyPrefix = `${parentProjectNode.id}__`
      const legacyBindingNodeId = binding.nodeId.startsWith(legacyPrefix)
        ? scopedInternalId(nodeId, binding.nodeId.slice(legacyPrefix.length))
        : binding.nodeId
      const legacyBindingNode = findProjectNodeByCanvasId(state.workflowProject, legacyBindingNodeId)
      const boundProjectNode = bindsParent
        ? parentProjectNode
        : localBindingNode ?? legacyBindingNode ?? directBindingNode
      const backingNodeId = bindsParent
        ? nodeId
        : localBindingNode
          ? localBindingNodeId
          : legacyBindingNode
            ? legacyBindingNodeId
            : directBindingNode
              ? binding.nodeId
            : nodeId
      const backingProjectNode = boundProjectNode ?? parentProjectNode
      const nextConfig = configKey
        ? { ...configRecord(backingProjectNode.params.config), [configKey]: value }
        : undefined

      let projectNodes = updateCanonicalProjectNodeByCanvasId(state.workflowProject, nodeId, (node) => ({
        ...node,
        parameterInterface: nextParameterInterface,
      }))
      if (binding.source === "params") {
        projectNodes = updateCanonicalProjectNodeByCanvasId(projectNodes, backingNodeId, (node) => ({
          ...node,
          params: resetOperatorConfig
            ? {
                operatorId: operatorSelection?.operatorId,
                packVersion: operatorSelection?.packVersion,
                config: {},
              }
            : configKey
              ? { ...node.params, config: nextConfig }
              : { ...node.params, [binding.fieldId]: value },
        }))
      } else if (binding.source === "data") {
        projectNodes = updateCanonicalProjectNodeByCanvasId(projectNodes, backingNodeId, (node) => ({
          ...node,
          ui: { ...node.ui, [binding.fieldId]: value },
        }))
      }

      const nextProject = parseWorkflowProject({
        ...projectNodes,
        adapters: state.workflowProject.adapters.map((adapter) => {
          if (binding.source !== "adapter" || backingProjectNode?.adapter !== adapter.id) return adapter
          if (binding.fieldId === "mode") return { ...adapter, mode: value }
          return { ...adapter, config: { ...adapter.config, [binding.fieldId]: value } }
        }),
      })

      return {
        workflowProject: nextProject,
        nodes: state.nodes.map((node) => {
          const withParentInterface =
            node.id === nodeId
              ? { ...node, data: { ...node.data, parameterInterface: nextParameterInterface } }
              : node
          if (node.id === backingNodeId) {
            const withValue = writeBoundValueToNode(
              withParentInterface,
              binding.source,
              configKey ? "config" : binding.fieldId,
              configKey ? nextConfig : value,
            )
            return resetOperatorConfig
              ? writeBoundValueToNode(
                  writeBoundValueToNode(
                    writeBoundValueToNode(
                      withValue,
                      "params",
                      "operatorId",
                      operatorSelection?.operatorId,
                    ),
                    "params",
                    "packVersion",
                    operatorSelection?.packVersion,
                  ),
                  "params",
                  "config",
                  {},
                )
              : withValue
          }
          return withParentInterface
        }),
      }
    })
  },

  updateNodeData: (id, data) => {
    set((state) => ({
      nodes: state.nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...data } } : n)),
    }))
  },

  ...createSelectionActions(set, get),
  ...createLayoutActions(set, get),

  ...createEdgeActions(set, get),

  enterNodeNetwork: (nodeId) => {
    const { workflowProject, nodes, edges, drawings, networkStack } = get()
    if (networkStack.length >= MAX_WORKFLOW_NODE_DEPTH - 1) return NODE_NETWORK_DEPTH_LIMIT_REACHED
    const node = nodes.find((candidate) => candidate.id === nodeId)
    if (!node) return 0
    const projectNode = findProjectNodeByCanvasId(workflowProject, nodeId) ?? projectNodeFromCanvasNode(node)

    const projectInternals = materializeProjectInternals(projectNode, node, nodeId, "network")
    if (projectNode?.internals) {
      const materialized = projectInternals ?? { nodes: [], edges: [] }
      const internalNodeIds = materialized.nodes.map((internalNode) => internalNode.id)
      set({
        networkStack: [
          ...networkStack,
          {
            nodeId,
            label: String(node.data.label ?? nodeId),
            snapshot: snapshot({ nodes, edges, drawings }),
          },
        ],
        nodes: materialized.nodes,
        edges: [],
        drawings: [],
        helperLines: { snapPosition: {} },
      })
      scheduleNetworkInternalEdges(get, set, nodeId, internalNodeIds, materialized.edges)
      return materialized.nodes.length
    }

    const internals = getNodeInternals(projectNode)
    if (!internals || internals.steps.length === 0) {
      return 0
    }

    const internalNodes: WorkflowNode[] = internals.steps.map((stepItem, index) => ({
      ...(() => {
        const primitiveItem = getPrimitiveByStepCapability(stepItem.capability)
        return {
          data: {
            ...primitiveToNodeData(primitiveItem),
            label: stepItem.label,
            description: stepItem.description,
            status: stepItem.status === "future" ? "idle" : "success",
            fields: fieldsForInternalStep(stepItem, nodeId, node.data.parameterInterface ?? projectNode?.parameterInterface),
            internalOf: nodeId,
            internalStepId: stepItem.id,
            internalStatus: stepItem.status,
            internalLocked: true,
            networkTitle: internals.title,
          },
        }
      })(),
      id: `${nodeId}__${stepItem.id}`,
      type: "workflow",
      draggable: false,
      connectable: false,
      position: { x: 520, y: 80 + index * 140 },
    }))

    const internalEdges: WorkflowEdge[] = internalNodes.slice(0, -1).map((internalNode, index) => ({
      id: `e-${nodeId}__${internals.steps[index].id}-${internals.steps[index + 1].id}`,
      source: internalNode.id,
      target: internalNodes[index + 1].id,
      type: "workflow",
      animated: true,
      data: { internalOf: nodeId },
    }))

    set({
      networkStack: [
        ...networkStack,
        {
          nodeId,
          label: String(node.data.label ?? nodeId),
          snapshot: snapshot({ nodes, edges, drawings }),
        },
      ],
      nodes: internalNodes,
      edges: internalEdges,
      drawings: [],
      helperLines: { snapPosition: {} },
    })
    return internalNodes.length
  },

  enterNodeNetworkForEdit: (nodeId, options) => {
    const state = get()
    if (state.networkStack.length >= MAX_WORKFLOW_NODE_DEPTH - 1) {
      return NODE_NETWORK_DEPTH_LIMIT_REACHED
    }
    const canvasNode = state.nodes.find((candidate) => candidate.id === nodeId)
    const projectNode = findProjectNodeByCanvasId(state.workflowProject, nodeId)
    if (!canvasNode || !projectNode) return 0

    if (!projectNode.internals) {
      if (!options?.suppressSnapshot) get().takeSnapshot()
      const ensured = ensureCanonicalNodeInternals(state.workflowProject, nodeId, projectNode)
      set({ workflowProject: ensured.project })
    }

    const stackDepth = get().networkStack.length
    const count = get().enterNodeNetwork(nodeId)
    return get().networkStack.length > stackDepth ? Math.max(1, count) : count
  },

  exitNodeNetwork: () => {
    const { networkStack } = get()
    const previous = networkStack[networkStack.length - 1]
    if (!previous) return false
    set({
      networkStack: networkStack.slice(0, -1),
      nodes: previous.snapshot.nodes,
      edges: previous.snapshot.edges,
      drawings: previous.snapshot.drawings ?? [],
      helperLines: { snapPosition: {} },
    })
    return true
  },

  unlockNodeInternals: (nodeId) => {
    const { workflowProject, nodes, edges, networkStack } = get()
    if (networkStack.length >= MAX_WORKFLOW_NODE_DEPTH - 1) return NODE_NETWORK_DEPTH_LIMIT_REACHED
    const node = nodes.find((candidate) => candidate.id === nodeId)
    if (!node) return 0
    const projectNode = findProjectNodeByCanvasId(workflowProject, nodeId) ?? projectNodeFromCanvasNode(node)
    if (!projectNode?.internals) return 0

    const existingInternalIds = new Set(
      nodes.filter((candidate) => candidate.data.internalOf === nodeId).map((candidate) => candidate.id),
    )
    if (existingInternalIds.size > 0) return 0

    const projectInternals = materializeProjectInternals(projectNode, node, nodeId, "unlock")
    if (projectInternals) {
      get().takeSnapshot()
      const internalNodeIds = projectInternals.nodes.map((internalNode) => internalNode.id)
      const nextNodes = nodes.map((candidate) =>
        candidate.id === nodeId
          ? { ...candidate, data: { ...candidate.data, internalsUnlocked: true } }
          : candidate,
      )
      set({
        nodes: resolveCollisions([...nextNodes, ...projectInternals.nodes], projectInternals.nodes[0]?.id ?? nodeId),
        edges,
      })
      scheduleUnlockedInternalEdges(get, set, nodeId, internalNodeIds, projectInternals.edges)
      return projectInternals.nodes.length
    }

    return 0
  },

  lockNodeInternals: (nodeId) => {
    const { nodes, edges } = get()
    const internalIds = new Set(nodes.filter((node) => node.data.internalOf === nodeId).map((node) => node.id))
    if (internalIds.size === 0) return 0
    get().takeSnapshot()
    set({
      nodes: nodes
        .filter((node) => !internalIds.has(node.id))
        .map((node) =>
          node.id === nodeId
            ? { ...node, data: { ...node.data, internalsUnlocked: false } }
            : node,
        ),
      edges: edges.filter(
        (edge) =>
          !internalIds.has(edge.source) &&
          !internalIds.has(edge.target) &&
          edge.data?.internalOf !== nodeId,
      ),
    })
    return internalIds.size
  },

  save: () => {
    const { nodes, edges, drawings } = get()
    if (typeof window === "undefined") return
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ nodes, edges, drawings }))
  },

  load: () => {
    if (typeof window === "undefined") return false
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return false
    try {
      const parsed = JSON.parse(raw) as FlowSnapshot
      get().takeSnapshot()
      set({ nodes: parsed.nodes, edges: parsed.edges, drawings: parsed.drawings ?? [] })
      return true
    } catch {
      return false
    }
  },

  reset: () => {
    get().takeSnapshot()
    set({
      workflowProject: initialWorkflowProject,
      nodes: initialWorkflowFlow.nodes,
      edges: initialWorkflowFlow.edges,
      drawings: [],
      networkStack: [],
    })
  },

  importFlow: (snapshotData) => {
    get().takeSnapshot()
    set({ nodes: snapshotData.nodes, edges: snapshotData.edges, drawings: snapshotData.drawings ?? [], networkStack: [] })
  },

  importWorkflowProject: (project) => {
    const flow = workflowProjectToReactFlow(project)
    get().takeSnapshot()
    set({ workflowProject: project, nodes: flow.nodes, edges: flow.edges, drawings: [], networkStack: [] })
  },

  applyWorkflowCapabilities: (capabilities) => {
    set((state) => {
      const projectRuntimeCapability = (node: WorkflowProjectNode): WorkflowProjectNode => {
        const catalogId = typeof node.ui?.catalogId === "string" ? node.ui.catalogId : null
        if (!catalogId) return node
        const runtimeCapability = projectedCatalogRuntimeCapability(
          catalogRuntimeCapability(capabilities, catalogId),
          {
            id: catalogId,
            label: typeof node.ui?.label === "string" ? node.ui.label : node.id,
            kind: node.kind,
            capability: node.capability,
          },
          true,
        )
        if (!runtimeCapability) return node
        const parameterInterface = createDataOperatorParameterInterface(
          node.id,
          catalogId,
          node.params,
          runtimeCapability,
        ) ?? createBackendParameterInterface(node.id, runtimeCapability.manifest) ?? node.parameterInterface
        const selectedOperator = parameterInterface?.fields
          .find((field) => field.id === "operator.operatorId")
        const defaultSelection = parseDataOperatorSelectionValue(selectedOperator?.value)
        const shouldPinDefaultVersion =
          catalogId.startsWith("intelligence.data.") &&
          typeof node.params.packVersion !== "string" &&
          Boolean(defaultSelection)
        return {
          ...node,
          params: shouldPinDefaultVersion
            ? {
                ...node.params,
                operatorId: defaultSelection?.operatorId,
                packVersion: defaultSelection?.packVersion,
              }
            : node.params,
          parameterInterface,
          ui: {
            ...(node.ui ?? {}),
            runtimeCapability,
            runtimeContract: runtimeContractForCapability(runtimeCapability),
          },
        }
      }
      const projectNodes = state.workflowProject.nodes.map((node) =>
        mapWorkflowProjectNodeTree(node, projectRuntimeCapability),
      )
      const workflowProject = parseWorkflowProject({
        ...state.workflowProject,
        nodes: projectNodes,
      })
      const runtimeByNodeId = new Map<string, { capability: WorkflowRuntimeCapability; contract: WorkflowNodeData["runtimeContract"] }>()
      const collectRuntime = (node: WorkflowProjectNode, canvasNodeId: string) => {
        const runtimeCapability = node.ui?.runtimeCapability
        if (isWorkflowRuntimeCapability(runtimeCapability)) {
          runtimeByNodeId.set(canvasNodeId, {
            capability: runtimeCapability,
            contract: runtimeContractForCapability(runtimeCapability),
          })
        }
        for (const child of workflowProjectNodeChildren(node)) {
          collectRuntime(child, scopedInternalId(canvasNodeId, child.id))
        }
      }
      for (const node of workflowProject.nodes) {
        collectRuntime(node, node.id)
      }
      return {
        workflowProject,
        nodes: state.nodes.map((node) => {
          const runtime = runtimeByNodeId.get(node.id)
          if (!runtime) return node
          return {
            ...node,
            data: {
              ...node.data,
              runtimeCapability: runtime.capability,
              runtimeContract: runtime.contract,
              parameterInterface: workflowProject.nodes.find((projectNode) => projectNode.id === node.id)?.parameterInterface,
            },
          }
        }),
      }
    })
  },

  applyWorkflowNodeRunEvent: (event) => {
    set((state) => {
      const runtimeRunState = runStateForEvent(event)
      const canvasNodeIds = new Set(
        runtimeNodeIdCandidates(event.nodeId, event.packageNodeId, event.internalNodeId, event.nodePath),
      )
      const nextProject = parseWorkflowProject({
        ...state.workflowProject,
        nodes: state.workflowProject.nodes.map((node) =>
          patchProjectNodeRunEvent(node, event, runtimeRunState),
        ),
      })
      return {
        workflowProject: nextProject,
        nodes: state.nodes.map((node) =>
          canvasNodeIds.has(node.id)
            ? {
                ...node,
                data: {
                  ...node.data,
                  status: workflowNodeStatusFromEvent(event.eventType),
                  runtimeRunState,
                  runtimeLatestEvent: event,
                  runtimePreview: {
                    ...(node.data.runtimePreview ?? {}),
                    status: event.eventType,
                    runId: event.workflowRunId,
                    traceId: event.traceId,
                    sourceGroups: event.sourceGroup ? [event.sourceGroup] : node.data.runtimePreview?.sourceGroups,
                    diagnostic: event.message ?? event.blockReason?.message ?? node.data.runtimePreview?.diagnostic,
                  },
                },
              }
            : node,
        ),
      }
    })
  },

  applyWorkflowRunProjection: (projection) => {
    set((state) => {
      const stateByCanvasNodeId = runtimeStateByCanvasNodeId(projection)
      const nextProject = parseWorkflowProject({
        ...state.workflowProject,
        nodes: state.workflowProject.nodes.map((node) =>
          patchProjectNodeRunProjection(node, stateByCanvasNodeId),
        ),
      })
      return {
        workflowProject: nextProject,
        nodes: state.nodes.map((node) => {
          const runtimeRunState = stateByCanvasNodeId.get(node.id)
          if (!runtimeRunState) return node
          const latestBlock = runtimeRunState.blockReasons.at(-1)
          return {
            ...node,
            data: {
              ...node.data,
              status: workflowNodeStatusFromRun(runtimeRunState.status),
              runtimeRunState,
              runtimePreview: {
                ...(node.data.runtimePreview ?? {}),
                status: runtimeRunState.status,
                runId: projection.runId,
                traceId: projection.traceId,
                sourceGroups: runtimeRunState.sourceGroups,
                diagnostic: latestBlock?.message ?? node.data.runtimePreview?.diagnostic,
              },
            },
          }
        }),
      }
    })
  },

  applyWorkflowEvidenceBatchProjection: (projection, batches) => {
    set((state) => applyEvidenceBatchRuntimePatches(state.nodes, state.edges, projection, batches))
  },

  updateWorkflowProfile: (profile) => {
    get().takeSnapshot()
    set((state) => ({ workflowProject: parseWorkflowProject({ ...state.workflowProject, profile }) }))
  },

  queueAgentProposal: (proposal) => set({ pendingAgentProposal: proposal }),
  clearPendingAgentProposal: () => set({ pendingAgentProposal: null }),

  focusProposalTargets: (nodeIds, edgeIds = []) => {
    const focusedNodes = new Set(nodeIds)
    const focusedEdges = new Set(edgeIds)
    set((state) => ({
      nodes: state.nodes.map((node) => ({
        ...node,
        selected: focusedNodes.has(node.id),
        data: {
          ...node.data,
          proposalFocused: focusedNodes.has(node.id),
        },
      })),
      edges: state.edges.map((edge) => ({
        ...edge,
        selected: focusedEdges.has(edge.id),
        data: {
          ...edge.data,
          proposalFocused: focusedEdges.has(edge.id),
        },
      })),
    }))
  },

  clearProposalFocus: () => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.data.proposalFocused ? { ...node, data: { ...node.data, proposalFocused: false } } : node,
      ),
      edges: state.edges.map((edge) =>
        edge.data?.proposalFocused ? { ...edge, data: { ...edge.data, proposalFocused: false } } : edge,
      ),
    }))
  },

  applyGeneratedWorkflow: (spec) => {
    get().takeSnapshot()

    const nodes: WorkflowNode[] = spec.nodes.map((n, i) => {
      const item = NODE_PALETTE.find((p) => p.nodeType === n.type && p.nodeType !== "shape") ?? NODE_PALETTE[0]
      const rfType = n.type === "note" ? "note" : "workflow"
      const data: WorkflowNodeData = {
        label: n.label,
        description: n.description,
        nodeType: item.nodeType,
        category: item.category,
        icon: item.icon,
        color: item.color,
        status: "idle",
      }
      if (n.type === "condition") {
        data.condition = n.config || "value > 0"
      } else if (n.config) {
        const base = item.defaultData?.fields
        data.fields =
          base && base.length > 0
            ? [{ ...base[0], value: n.config }, ...base.slice(1)]
            : [{ id: "value", label: "参数", value: n.config }]
      } else if (item.defaultData) {
        Object.assign(data, JSON.parse(JSON.stringify(item.defaultData)))
      }
      return {
        id: n.id,
        type: rfType,
        position: { x: (i % 3) * 260, y: Math.floor(i / 3) * 160 },
        data,
      }
    })

    const validIds = new Set(nodes.map((n) => n.id))
    const edges: WorkflowEdge[] = spec.edges
      .filter((e) => validIds.has(e.source) && validIds.has(e.target))
      .map((e) => ({
        id: `e-${nanoid(6)}`,
        source: e.source,
        target: e.target,
        type: "workflow" as const,
        animated: true,
        ...(e.label ? { label: e.label, data: { label: e.label } } : {}),
      }))

    set({ nodes, edges, drawings: [], networkStack: [] })
    // 端口在上下两侧 → 纵向 TB 布局
    void get().autoLayout("TB", "elk", true)
  },
}))
