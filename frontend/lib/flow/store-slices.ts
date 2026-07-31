import { nanoid } from "nanoid"
import { addEdge, applyEdgeChanges, applyNodeChanges, reconnectEdge } from "@xyflow/react"
import type { StoreApi } from "zustand"
import { applyHelperLines } from "./helper-lines"
import { useSettingsStore } from "./settings-store"
import { connectedComponentEdges, findConnectedComponentForNode } from "./graph-components"
import type { FlowState } from "./store"
import {
  appendCanonicalNetworkEdge,
  canonicalPositionFromNetworkCanvas,
  canonicalLocalEdgeId,
  canonicalLocalNodeId,
  readCanonicalNetworkScope,
  removeCanonicalNetworkItems,
  syncCanonicalNetworkNodePositions,
  updateCanonicalNetworkEdge,
  updateCanonicalNetworkScope,
  type CanonicalScopeId,
} from "./store-canonical-actions"
import { HISTORY_LIMIT, snapshot, workflowNodeId } from "./store-utils"
import { MAX_WORKFLOW_NODE_DEPTH } from "../workflow/node-hierarchy"
import { parseWorkflowProject, type WorkflowProject, type WorkflowProjectNode } from "../workflow/schema"
import type { WorkflowEdge, WorkflowNode } from "./types"

type FlowSet = StoreApi<FlowState>["setState"]
type FlowGet = StoreApi<FlowState>["getState"]

function canonicalNodeId(parentCanvasId: CanonicalScopeId, node: WorkflowNode): string | null {
  if (parentCanvasId === null && typeof node.data.internalOf === "string") return null
  return node.data.internalStepId ?? canonicalLocalNodeId(parentCanvasId, node.id)
}

function canonicalEdgeId(parentCanvasId: CanonicalScopeId, edge: WorkflowEdge): string | null {
  if (parentCanvasId === null && typeof edge.data?.internalOf === "string") return null
  const storedId = edge.data?.internalEdgeId
  return typeof storedId === "string" ? storedId : canonicalLocalEdgeId(parentCanvasId, edge.id)
}

function removeCanonicalCanvasItems(
  project: WorkflowProject,
  parentCanvasId: CanonicalScopeId,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
): WorkflowProject {
  const nodeIds = new Set(
    nodes.map((node) => canonicalNodeId(parentCanvasId, node)).filter((id): id is string => Boolean(id)),
  )
  const edgeIds = new Set(
    edges.map((edge) => canonicalEdgeId(parentCanvasId, edge)).filter((id): id is string => Boolean(id)),
  )
  return parseWorkflowProject(removeCanonicalNetworkItems(project, parentCanvasId, nodeIds, edgeIds))
}

function canonicalSubtreeDepth(node: WorkflowProjectNode): number {
  const children = (node.internals?.nodes ?? []).filter((candidate): candidate is WorkflowProjectNode => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return false
    const value = candidate as Partial<WorkflowProjectNode>
    return typeof value.id === "string" && typeof value.kind === "string" && typeof value.capability === "string"
  })
  return 1 + Math.max(0, ...children.map(canonicalSubtreeDepth))
}

export function createWhiteboardActions(
  set: FlowSet,
  get: FlowGet,
): Pick<FlowState, "setToolMode" | "setPenColor" | "setPenSize" | "addStroke" | "clearDrawings"> {
  return {
    setToolMode: (mode) => set({ toolMode: mode }),
    setPenColor: (color) => set({ penColor: color }),
    setPenSize: (size) => set({ penSize: size }),
    addStroke: (stroke) => set((state) => ({ drawings: [...state.drawings, stroke] })),
    clearDrawings: () => {
      get().takeSnapshot()
      set({ drawings: [] })
    },
  }
}

export function createCanvasChangeActions(
  set: FlowSet,
  get: FlowGet,
): Pick<FlowState, "onNodesChange" | "onEdgesChange" | "onConnect" | "connectNodes" | "onReconnect" | "setNodes" | "setSelectedIds" | "clearHelperLines"> {
  const connectNodes: FlowState["connectNodes"] = (connection, options) => {
    if (!options?.suppressSnapshot) get().takeSnapshot()
    const state = get()
    const parentNetwork = state.networkStack.at(-1)
    const scopeId = parentNetwork?.nodeId ?? null
    const canonicalScope = readCanonicalNetworkScope(state.workflowProject, scopeId)
    if (canonicalScope) {
      const sourceNode = state.nodes.find((node) => node.id === connection.source)
      const targetNode = state.nodes.find((node) => node.id === connection.target)
      const source = sourceNode ? canonicalNodeId(scopeId, sourceNode) : null
      const target = targetNode ? canonicalNodeId(scopeId, targetNode) : null
      const canonicalNodeIds = new Set(canonicalScope.nodes.map((node) => node.id))
      if (source && target && canonicalNodeIds.has(source) && canonicalNodeIds.has(target)) {
        const internalEdgeId = `edge-${nanoid(6)}`
        const edge: WorkflowEdge = {
          id: parentNetwork ? `e-${parentNetwork.nodeId}__${internalEdgeId}` : internalEdgeId,
          source: connection.source,
          target: connection.target,
          sourceHandle: connection.sourceHandle,
          targetHandle: connection.targetHandle,
          type: "workflow",
          animated: false,
          data: {
            ...(parentNetwork ? { internalOf: parentNetwork.nodeId, internalEdgeId } : {}),
            sourcePort: connection.sourceHandle ?? undefined,
            targetPort: connection.targetHandle ?? undefined,
          },
        }
        const workflowProject = parseWorkflowProject(
          appendCanonicalNetworkEdge(state.workflowProject, scopeId, {
            id: internalEdgeId,
            source,
            target,
            sourcePort: connection.sourceHandle ?? undefined,
            targetPort: connection.targetHandle ?? undefined,
          }),
        )
        set({ workflowProject, edges: addEdge(edge, state.edges) })
        return
      }
    }
    set({
      edges: addEdge(
        {
          ...connection,
          type: "workflow",
          animated: false,
        },
        state.edges,
      ),
    })
  }

  return {
    onNodesChange: (changes) => {
      const state = get()
      const { changes: nextChanges, helperLines } = applyHelperLines(
        changes,
        state.nodes,
        useSettingsStore.getState().snapToHelperLines,
      )
      const nodes = applyNodeChanges(nextChanges, state.nodes)
      const parentNetwork = state.networkStack.at(-1)
      const scopeId = parentNetwork?.nodeId ?? null
      const movedNodeIds = new Set(
        nextChanges.flatMap((change) =>
          change.type === "position" && change.dragging !== true ? [change.id] : [],
        ),
      )
      const workflowProject =
        movedNodeIds.size > 0
          ? parseWorkflowProject(
              syncCanonicalNetworkNodePositions(
                state.workflowProject,
                scopeId,
                nodes.filter(
                  (node) =>
                    movedNodeIds.has(node.id) &&
                    (scopeId !== null || typeof node.data.internalOf !== "string"),
                ),
              ),
            )
          : state.workflowProject
      set({
        workflowProject,
        nodes,
        helperLines,
      })
    },

    onEdgesChange: (changes) => {
      const state = get()
      const removedEdgeIds = new Set(
        changes.flatMap((change) => (change.type === "remove" ? [change.id] : [])),
      )
      const removedEdges = state.edges.filter((edge) => removedEdgeIds.has(edge.id))
      const parentNetwork = state.networkStack.at(-1)
      const scopeId = parentNetwork?.nodeId ?? null
      set({
        workflowProject:
          removedEdges.length > 0
            ? removeCanonicalCanvasItems(state.workflowProject, scopeId, [], removedEdges)
            : state.workflowProject,
        edges: applyEdgeChanges(changes, state.edges),
      })
    },

    onConnect: (connection) => connectNodes(connection),
    connectNodes,

    onReconnect: (oldEdge, connection) => {
      get().takeSnapshot()
      const state = get()
      const parentNetwork = state.networkStack.at(-1)
      const scopeId = parentNetwork?.nodeId ?? null
      const canonicalScope = readCanonicalNetworkScope(state.workflowProject, scopeId)
      const sourceNode = state.nodes.find((node) => node.id === connection.source)
      const targetNode = state.nodes.find((node) => node.id === connection.target)
      const source = sourceNode ? canonicalNodeId(scopeId, sourceNode) : null
      const target = targetNode ? canonicalNodeId(scopeId, targetNode) : null
      const canonicalId = canonicalEdgeId(scopeId, oldEdge)
      const nextEdges = reconnectEdge(oldEdge, connection, state.edges, { shouldReplaceId: false }).map((edge) =>
        edge.id === oldEdge.id
          ? {
              ...edge,
              data: {
                ...edge.data,
                sourcePort: connection.sourceHandle ?? undefined,
                targetPort: connection.targetHandle ?? undefined,
              },
            }
          : edge,
      )
      const workflowProject =
        canonicalScope && canonicalId && source && target
          ? parseWorkflowProject(updateCanonicalNetworkEdge(state.workflowProject, scopeId, canonicalId, (edge) => ({
              ...edge,
              source,
              target,
              sourcePort: connection.sourceHandle ?? undefined,
              targetPort: connection.targetHandle ?? undefined,
            })))
          : state.workflowProject
      set({
        workflowProject,
        edges: nextEdges,
      })
    },

    setNodes: (updater) => set((state) => ({ nodes: updater(state.nodes) })),
    setSelectedIds: (ids) => set({ selectedIds: ids }),
    clearHelperLines: () => set({ helperLines: { snapPosition: {} } }),
  }
}

export function createHistoryActions(
  set: FlowSet,
  get: FlowGet,
): Pick<FlowState, "takeSnapshot" | "undo" | "redo" | "canUndo" | "canRedo"> {
  return {
    takeSnapshot: () => {
      set((state) => ({
        past: [...state.past, snapshot(state)].slice(-HISTORY_LIMIT),
        future: [],
      }))
    },

    undo: () => {
      const { past } = get()
      if (past.length === 0) return
      const previous = past[past.length - 1]
      set((state) => ({
        past: state.past.slice(0, -1),
        future: [snapshot(state), ...state.future].slice(0, HISTORY_LIMIT),
        nodes: previous.nodes,
        edges: previous.edges,
        drawings: previous.drawings ?? [],
        workflowProject: previous.workflowProject ?? state.workflowProject,
        networkStack: previous.networkStack ?? state.networkStack,
        helperLines: { snapPosition: {} },
      }))
    },

    redo: () => {
      const { future } = get()
      if (future.length === 0) return
      const next = future[0]
      set((state) => ({
        past: [...state.past, snapshot(state)].slice(-HISTORY_LIMIT),
        future: state.future.slice(1),
        nodes: next.nodes,
        edges: next.edges,
        drawings: next.drawings ?? [],
        workflowProject: next.workflowProject ?? state.workflowProject,
        networkStack: next.networkStack ?? state.networkStack,
        helperLines: { snapPosition: {} },
      }))
    },

    canUndo: () => get().past.length > 0,
    canRedo: () => get().future.length > 0,
  }
}

export function createSelectionActions(
  set: FlowSet,
  get: FlowGet,
): Pick<
  FlowState,
  | "deleteSelected"
  | "disconnectSelectedConnections"
  | "disconnectNodeConnections"
  | "removeEdgesByIds"
  | "selectConnectedComponent"
  | "duplicateSelected"
  | "copy"
  | "cut"
  | "paste"
> {
  return {
    deleteSelected: () => {
      const { workflowProject, nodes, edges, networkStack } = get()
      const selectedNodes = nodes.filter((node) => node.selected)
      const selectedEdges = edges.filter((edge) => edge.selected)
      const selectedNodeIds = new Set(selectedNodes.map((node) => node.id))
      const selectedEdgeIds = new Set(selectedEdges.map((edge) => edge.id))
      if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return
      get().takeSnapshot()
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      set({
        workflowProject: removeCanonicalCanvasItems(
          workflowProject,
          scopeId,
          selectedNodes,
          selectedEdges,
        ),
        nodes: nodes.filter((n) => !selectedNodeIds.has(n.id)),
        edges: edges.filter(
          (e) => !selectedEdgeIds.has(e.id) && !selectedNodeIds.has(e.source) && !selectedNodeIds.has(e.target),
        ),
      })
    },

    disconnectSelectedConnections: () => {
      const { workflowProject, nodes, edges, networkStack } = get()
      const selectedNodeIds = new Set(nodes.filter((n) => n.selected).map((n) => n.id))
      const selectedEdgeIds = new Set(edges.filter((e) => e.selected).map((e) => e.id))
      if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return 0
      const nextEdges = edges.filter(
        (e) => !selectedEdgeIds.has(e.id) && !selectedNodeIds.has(e.source) && !selectedNodeIds.has(e.target),
      )
      const removedEdges = edges.filter((edge) => !nextEdges.includes(edge))
      const removed = edges.length - nextEdges.length
      if (removed === 0) return 0
      get().takeSnapshot()
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      set({
        workflowProject: removeCanonicalCanvasItems(workflowProject, scopeId, [], removedEdges),
        edges: nextEdges,
      })
      return removed
    },

    disconnectNodeConnections: (nodeId, options) => {
      const { workflowProject, edges, networkStack } = get()
      const nextEdges = edges.filter((e) => e.source !== nodeId && e.target !== nodeId)
      const removedEdges = edges.filter((edge) => !nextEdges.includes(edge))
      const removed = edges.length - nextEdges.length
      if (removed === 0) return 0
      if (!options?.suppressSnapshot) get().takeSnapshot()
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      set({
        workflowProject: removeCanonicalCanvasItems(workflowProject, scopeId, [], removedEdges),
        edges: nextEdges,
      })
      return removed
    },

    removeEdgesByIds: (edgeIds, options) => {
      const ids = new Set(edgeIds)
      if (ids.size === 0) return 0
      const { workflowProject, edges, networkStack } = get()
      const nextEdges = edges.filter((e) => !ids.has(e.id))
      const removedEdges = edges.filter((edge) => ids.has(edge.id))
      const removed = edges.length - nextEdges.length
      if (removed === 0) return 0
      if (!options?.suppressSnapshot) get().takeSnapshot()
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      set({
        workflowProject: removeCanonicalCanvasItems(workflowProject, scopeId, [], removedEdges),
        edges: nextEdges,
      })
      return removed
    },

    selectConnectedComponent: (nodeId) => {
      const { nodes, edges } = get()
      const nodeIds = findConnectedComponentForNode(nodeId, nodes, edges)
      const edgeIds = connectedComponentEdges(nodeIds, edges)
      const selectedNodes = new Set(nodeIds)
      const selectedEdges = new Set(edgeIds)
      set({
        nodes: nodes.map((node) => ({ ...node, selected: selectedNodes.has(node.id) })),
        edges: edges.map((edge) => ({ ...edge, selected: selectedEdges.has(edge.id) })),
      })
      return { nodeIds, edgeIds }
    },

    duplicateSelected: () => {
      const { clipboard, nodes } = get()
      const selected = nodes.filter((n) => n.selected)
      if (selected.length === 0) return
      const minX = Math.min(...selected.map((node) => node.position.x))
      const minY = Math.min(...selected.map((node) => node.position.y))
      get().copy()
      get().paste({ x: minX + 40, y: minY + 40 })
      set({ clipboard })
    },

    copy: () => {
      const { workflowProject, networkStack, nodes, edges } = get()
      const selectedNodes = nodes.filter((n) => n.selected)
      if (selectedNodes.length === 0) return
      const ids = new Set(selectedNodes.map((n) => n.id))
      const internalEdges = edges.filter((e) => ids.has(e.source) && ids.has(e.target))
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      const canonicalScope = readCanonicalNetworkScope(workflowProject, scopeId)
      const canonicalNodes = selectedNodes.flatMap((node) => {
        const localId = canonicalNodeId(scopeId, node)
        const canonicalNode = canonicalScope?.nodes.find((candidate) => candidate.id === localId)
        return canonicalNode
          ? [{ canvasNodeId: node.id, node: JSON.parse(JSON.stringify(canonicalNode)) }]
          : []
      })
      const selectedCanonicalIds = new Set(canonicalNodes.map((entry) => entry.node.id))
      const canonicalEdges = internalEdges.flatMap((edge) => {
        const localId = canonicalEdgeId(scopeId, edge)
        const canonicalEdge = canonicalScope?.edges.find((candidate) => candidate.id === localId)
        if (
          !canonicalEdge ||
          !selectedCanonicalIds.has(canonicalEdge.source) ||
          !selectedCanonicalIds.has(canonicalEdge.target)
        ) {
          return []
        }
        return [{ canvasEdgeId: edge.id, edge: JSON.parse(JSON.stringify(canonicalEdge)) }]
      })
      set({
        clipboard: {
          nodes: JSON.parse(JSON.stringify(selectedNodes)),
          edges: JSON.parse(JSON.stringify(internalEdges)),
          ...(canonicalNodes.length > 0 ? { canonical: { nodes: canonicalNodes, edges: canonicalEdges } } : {}),
        },
      })
    },

    cut: () => {
      get().copy()
      get().deleteSelected()
    },

    paste: (position) => {
      const { clipboard, workflowProject, networkStack, nodes } = get()
      if (!clipboard || clipboard.nodes.length === 0) return
      const scopeId = networkStack.at(-1)?.nodeId ?? null
      const destinationParentDepth = scopeId ? scopeId.split("__").length : 0
      const copiedSubtreeDepth = Math.max(
        0,
        ...(clipboard.canonical?.nodes ?? []).map((entry) => canonicalSubtreeDepth(entry.node)),
      )
      if (destinationParentDepth + copiedSubtreeDepth > MAX_WORKFLOW_NODE_DEPTH) return
      get().takeSnapshot()

      const idMap = new Map<string, string>()
      const canonicalNodeIdMap = new Map<string, string>()
      const minX = Math.min(...clipboard.nodes.map((n) => n.position.x))
      const minY = Math.min(...clipboard.nodes.map((n) => n.position.y))
      const offsetX = position ? position.x - minX : 48
      const offsetY = position ? position.y - minY : 48
      const canonicalByCanvasId = new Map(
        (clipboard.canonical?.nodes ?? []).map((entry) => [entry.canvasNodeId, entry.node]),
      )
      const canonicalClones: NonNullable<typeof clipboard.canonical>["nodes"] = []

      const newNodes = clipboard.nodes.map((n) => {
        const canonicalNode = canonicalByCanvasId.get(n.id)
        const newLocalId = canonicalNode ? workflowNodeId() : null
        const newId = newLocalId
          ? scopeId === null
            ? newLocalId
            : `${scopeId}__${newLocalId}`
          : nanoid(8)
        idMap.set(n.id, newId)
        if (canonicalNode && newLocalId) canonicalNodeIdMap.set(canonicalNode.id, newLocalId)
        const nextPosition = { x: n.position.x + offsetX, y: n.position.y + offsetY }
        const data = JSON.parse(JSON.stringify(n.data))
        if (canonicalNode && newLocalId) {
          if (scopeId === null) {
            delete data.internalOf
            delete data.internalStepId
          } else {
            data.internalOf = scopeId
            data.internalStepId = newLocalId
          }
          canonicalClones.push({
            canvasNodeId: newId,
            node: {
              ...JSON.parse(JSON.stringify(canonicalNode)),
              id: newLocalId,
              ui: {
                ...(canonicalNode.ui ?? {}),
                position: scopeId === null ? nextPosition : canonicalPositionFromNetworkCanvas(nextPosition),
              },
            },
          })
        }
        return {
          ...n,
          id: newId,
          selected: true,
          position: nextPosition,
          data,
        }
      })

      const canonicalEdgeByCanvasId = new Map(
        (clipboard.canonical?.edges ?? []).map((entry) => [entry.canvasEdgeId, entry.edge]),
      )
      const canonicalEdgeClones: NonNullable<typeof clipboard.canonical>["edges"] = []
      const newEdges = clipboard.edges.map((edge) => {
        const canonicalEdge = canonicalEdgeByCanvasId.get(edge.id)
        const newLocalEdgeId = canonicalEdge ? `edge-${nanoid(6)}` : null
        if (canonicalEdge && newLocalEdgeId) {
          const source = canonicalNodeIdMap.get(canonicalEdge.source)
          const target = canonicalNodeIdMap.get(canonicalEdge.target)
          if (source && target) {
            canonicalEdgeClones.push({
              canvasEdgeId: newLocalEdgeId,
              edge: { ...JSON.parse(JSON.stringify(canonicalEdge)), id: newLocalEdgeId, source, target },
            })
          }
        }
        return {
          ...edge,
          id: newLocalEdgeId
            ? scopeId === null
              ? newLocalEdgeId
              : `e-${scopeId}__${newLocalEdgeId}`
            : `e-${nanoid(6)}`,
          source: idMap.get(edge.source) ?? edge.source,
          target: idMap.get(edge.target) ?? edge.target,
          selected: false,
          data: newLocalEdgeId
            ? {
                ...edge.data,
                ...(scopeId === null ? {} : { internalOf: scopeId, internalEdgeId: newLocalEdgeId }),
              }
            : edge.data,
        }
      })

      const nextProject = canonicalClones.length > 0
        ? parseWorkflowProject(updateCanonicalNetworkScope(workflowProject, scopeId, (scope) => ({
            nodes: [...scope.nodes, ...canonicalClones.map((entry) => entry.node)],
            edges: [...scope.edges, ...canonicalEdgeClones.map((entry) => entry.edge)],
          })))
        : workflowProject

      set((state) => ({
        workflowProject: nextProject,
        nodes: [...nodes.map((n) => ({ ...n, selected: false })), ...newNodes],
        edges: [...state.edges, ...newEdges],
      }))
    },
  }
}

export function createEdgeActions(
  set: FlowSet,
  get: FlowGet,
): Pick<FlowState, "updateEdgeWaypoints" | "updateEdgeData" | "updateEdgeType" | "toggleEdgeAnimated"> {
  return {
    updateEdgeWaypoints: (edgeId, waypoints) => {
      set((state) => ({
        edges: state.edges.map((e) =>
          e.id === edgeId ? { ...e, type: "editable", data: { ...e.data, waypoints } } : e,
        ),
      }))
    },

    updateEdgeData: (edgeId, data) => {
      const state = get()
      const canvasEdge = state.edges.find((edge) => edge.id === edgeId)
      if (!canvasEdge) return
      const parentNetwork = state.networkStack.at(-1)
      const scopeId = parentNetwork?.nodeId ?? null
      const localEdgeId = canonicalEdgeId(scopeId, canvasEdge)
      const { label, semantic, weight, contractId, proposalState } = data
      const uiPatch = { ...data }
      delete uiPatch.internalOf
      delete uiPatch.internalEdgeId
      delete uiPatch.sourcePort
      delete uiPatch.targetPort
      delete uiPatch.label
      delete uiPatch.semantic
      delete uiPatch.weight
      delete uiPatch.contractId
      delete uiPatch.proposalState
      const workflowProject = localEdgeId
        ? parseWorkflowProject(
            updateCanonicalNetworkScope(state.workflowProject, scopeId, (scope) => ({
              ...scope,
              edges: scope.edges.map((edge) =>
                edge.id === localEdgeId
                  ? {
                      ...edge,
                      ...(label !== undefined ? { label } : {}),
                      ...(semantic !== undefined ? { semantic } : {}),
                      ...(weight !== undefined ? { weight } : {}),
                      ...(contractId !== undefined ? { contractId } : {}),
                      ...(proposalState !== undefined ? { proposalState } : {}),
                      ui: { ...edge.ui, ...uiPatch },
                    }
                  : edge,
              ),
            })),
          )
        : state.workflowProject
      set({
        workflowProject,
        edges: state.edges.map((edge) =>
          edge.id === edgeId ? { ...edge, data: { ...edge.data, ...data } } : edge,
        ),
      })
    },

    updateEdgeType: (edgeId, type) => {
      get().takeSnapshot()
      set((state) => ({
        edges: state.edges.map((e) =>
          e.id === edgeId
            ? { ...e, type, data: { ...e.data, ...(type === "editable" ? {} : { waypoints: undefined }) } }
            : e,
        ),
      }))
    },

    toggleEdgeAnimated: (edgeId) => {
      set((state) => ({
        edges: state.edges.map((e) => (e.id === edgeId ? { ...e, animated: !e.animated } : e)),
      }))
    },
  }
}
