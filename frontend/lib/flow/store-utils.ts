import { customAlphabet } from "nanoid"
import type { FlowSnapshot, FreehandStroke, WorkflowEdge, WorkflowNode } from "./types"
import type { WorkflowProject, WorkflowProjectEdge, WorkflowProjectNode } from "../workflow/schema"

export const HISTORY_LIMIT = 100
export const workflowNodeId = customAlphabet("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", 8)

export type FlowNetworkStackEntry = { nodeId: string; label: string; snapshot: FlowSnapshot }
export type FlowStoreSnapshot = FlowSnapshot & {
  workflowProject?: WorkflowProject
  networkStack?: FlowNetworkStackEntry[]
}
export type FlowClipboard = FlowSnapshot & {
  canonical?: {
    nodes: Array<{ canvasNodeId: string; node: WorkflowProjectNode }>
    edges: Array<{ canvasEdgeId: string; edge: WorkflowProjectEdge }>
  }
}

export function snapshot(state: {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  drawings: FreehandStroke[]
  workflowProject?: WorkflowProject
  networkStack?: FlowNetworkStackEntry[]
}): FlowStoreSnapshot {
  return {
    nodes: clone(state.nodes),
    edges: clone(state.edges),
    drawings: clone(state.drawings),
    ...(state.workflowProject ? { workflowProject: clone(state.workflowProject) } : {}),
    ...(state.networkStack ? { networkStack: clone(state.networkStack) } : {}),
  }
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}
