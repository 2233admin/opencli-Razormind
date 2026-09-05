import type { WorkflowEdge, WorkflowNode, WorkflowNodeData, NodeCategory, WorkflowNodeType } from "@/lib/flow/types"
import type { WorkflowRuntimeCapability } from "./capabilities"
import type { WorkflowNodeRunEvent, WorkflowRunNodeState, WorkflowRunStatus } from "./backend-runs"
import type { WorkflowProject, WorkflowProjectNode } from "./schema"

const KIND_TO_NODE_TYPE: Record<WorkflowProjectNode["kind"], WorkflowNodeType> = {
  schedule: "trigger",
  source: "http",
  agent: "action",
  router: "condition",
  flow: "condition",
  control: "condition",
  notify: "action",
  inbox: "action",
  action: "action",
  sink: "action",
  media: "action",
}

const KIND_TO_CATEGORY: Record<WorkflowProjectNode["kind"], NodeCategory> = {
  schedule: "trigger",
  source: "data",
  agent: "action",
  router: "logic",
  flow: "logic",
  control: "logic",
  notify: "action",
  inbox: "data",
  action: "action",
  sink: "data",
  media: "data",
}

const KIND_TO_ICON: Record<WorkflowProjectNode["kind"], string> = {
  schedule: "Clock",
  source: "Globe",
  agent: "Sparkles",
  router: "GitBranch",
  flow: "GitMerge",
  control: "BadgeCheck",
  notify: "Send",
  inbox: "Inbox",
  action: "Play",
  sink: "Database",
  media: "Image",
}

export function workflowProjectToReactFlow(project: WorkflowProject): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } {
  return {
    nodes: project.nodes.map((node, index) => workflowNodeToReactFlow(node, index)),
    edges: project.edges.map((edge) => {
      const targetNode = project.nodes.find((node) => node.id === edge.target)
      const targetPort = (
        targetNode?.capability === "merge" &&
        (edge.targetPort === "in1" || edge.targetPort === "in2")
      )
        ? "in"
        : edge.targetPort
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourcePort,
        targetHandle: targetPort,
        label: edge.label,
        type: "workflow",
        animated: true,
        data: {
          label: edge.label,
          semantic: edge.semantic,
          weight: edge.weight,
          contractId: edge.contractId,
          proposalState: edge.proposalState,
          sourcePort: edge.sourcePort,
          targetPort,
          ...(edge.ui ?? {}),
        },
      }
    }),
  }
}

export function workflowNodeToReactFlow(node: WorkflowProjectNode, index: number): WorkflowNode {
  const ui = node.ui ?? {}
  const position = readPosition(ui) ?? { x: 520, y: 80 + index * 220 }
  const runtimeRunState = readRuntimeRunState(ui)
  const catalogId = readString(ui, "catalogId")
  const data: WorkflowNodeData = {
    label: readString(ui, "label") ?? node.id,
    description: readString(ui, "description") ?? `${node.kind}:${node.capability}`,
    nodeType: KIND_TO_NODE_TYPE[node.kind],
    category: KIND_TO_CATEGORY[node.kind],
    icon: readString(ui, "icon") ?? KIND_TO_ICON[node.kind],
    color: readString(ui, "color") ?? "var(--chart-2)",
    status: runtimeRunState ? workflowNodeStatusFromRun(runtimeRunState.status) : "idle",
    fields: Object.entries(node.params).map(([id, value]) => ({ id, label: id, value: formatParamValue(value) })),
    canonical: {
      kind: node.kind,
      capability: node.capability,
      adapter: node.adapter,
      params: node.params,
      catalogId,
    },
    sourceAnchor: node.sourceAnchor,
    runArtifact: node.runArtifact,
    runtimeCapability: readRuntimeCapability(ui),
    runtimeContract: readRuntimeContract(ui),
    runtimeRunState,
    runtimeLatestEvent: readRuntimeLatestEvent(ui),
    imageStudioSummary: readImageStudioSummary(ui),
    miniNetwork: node.miniNetwork,
    topicCollapse: node.topicCollapse,
    proposalState: node.proposalState,
    parameterInterface: node.parameterInterface,
    externalWorkflow: readExternalWorkflow(ui),
  }
  if (catalogId === "media.image-generation") data.interfaceRowCount = 4
  else if (catalogId === "media.image-asset") data.interfaceRowCount = 1

  return {
    id: node.id,
    type: data.nodeType === "note" ? "note" : "workflow",
    position,
    data,
  }
}

function readImageStudioSummary(ui: Record<string, unknown>): WorkflowNodeData["imageStudioSummary"] {
  const value = ui.imageStudioSummary
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  const recentAssetIds = Array.isArray(record.recentAssetIds)
    ? record.recentAssetIds.filter((item): item is string => typeof item === "string")
    : []
  return {
    snapshotId: typeof record.snapshotId === "string" ? record.snapshotId : undefined,
    modelFingerprint: typeof record.modelFingerprint === "string" ? record.modelFingerprint : undefined,
    recentAssetIds,
  }
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

function readRuntimeCapability(ui: Record<string, unknown>): WorkflowRuntimeCapability | undefined {
  const value = ui.runtimeCapability
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  if (typeof record.id !== "string" || typeof record.status !== "string") return undefined
  if (
    record.status !== "runnable" &&
    record.status !== "blocked" &&
    record.status !== "preview_only" &&
    record.status !== "design_only"
  ) {
    return undefined
  }
  return value as WorkflowRuntimeCapability
}

function readRuntimeContract(ui: Record<string, unknown>): WorkflowNodeData["runtimeContract"] {
  const value = ui.runtimeContract
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  if (typeof record.bindingId !== "string" || typeof record.status !== "string") return undefined
  return value as WorkflowNodeData["runtimeContract"]
}

function readRuntimeRunState(ui: Record<string, unknown>): WorkflowRunNodeState | undefined {
  const value = ui.runtimeRunState
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  if (typeof record.nodeId !== "string" || typeof record.status !== "string") return undefined
  if (!isWorkflowRunStatus(record.status)) return undefined
  return value as WorkflowRunNodeState
}

function readRuntimeLatestEvent(ui: Record<string, unknown>): WorkflowNodeRunEvent | undefined {
  const value = ui.runtimeLatestEvent
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  if (typeof record.id !== "string" || typeof record.nodeId !== "string" || typeof record.eventType !== "string") {
    return undefined
  }
  return value as WorkflowNodeRunEvent
}

function isWorkflowRunStatus(value: string): value is WorkflowRunStatus {
  return (
    value === "queued" ||
    value === "running" ||
    value === "waiting" ||
    value === "partial" ||
    value === "partial_success" ||
    value === "blocked" ||
    value === "completed" ||
    value === "failed"
  )
}

function readPosition(ui: Record<string, unknown>) {
  const value = ui.position
  if (!value || typeof value !== "object") return null
  const position = value as { x?: unknown; y?: unknown }
  if (typeof position.x !== "number" || typeof position.y !== "number") return null
  return { x: position.x, y: position.y }
}

function readString(ui: Record<string, unknown>, key: string): string | undefined {
  const value = ui[key]
  return typeof value === "string" ? value : undefined
}

function formatParamValue(value: unknown): string {
  if (value === undefined) return ""
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function readExternalWorkflow(ui: Record<string, unknown>): WorkflowNodeData["externalWorkflow"] {
  const n8n = ui.n8n
  if (!n8n || typeof n8n !== "object" || Array.isArray(n8n)) return undefined
  const record = n8n as Record<string, unknown>
  if (record.source !== "n8n") return undefined
  return {
    source: "n8n",
    originalId: typeof record.originalId === "string" ? record.originalId : undefined,
    originalName: typeof record.originalName === "string" ? record.originalName : undefined,
    type: typeof record.type === "string" ? record.type : undefined,
  }
}
