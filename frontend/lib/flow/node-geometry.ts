export const WORKFLOW_NODE_GEOMETRY = {
  width: 240,
  minHeight: 96,
  headerHeight: 72,
  interfaceRowHeight: 24,
} as const

export type WorkflowNodeDensity = "low" | "mid" | "high"

type PortLike = { direction?: unknown }

export function workflowNodeHeight(interfaceRowCount: number): number {
  return Math.max(
    WORKFLOW_NODE_GEOMETRY.minHeight,
    WORKFLOW_NODE_GEOMETRY.headerHeight + interfaceRowCount * WORKFLOW_NODE_GEOMETRY.interfaceRowHeight,
  )
}

export function workflowNodeSize(interfaceRowCount = 0) {
  return {
    width: WORKFLOW_NODE_GEOMETRY.width,
    height: workflowNodeHeight(interfaceRowCount),
  }
}

export function workflowNodePortRowCount(data: { interfaceRowCount?: unknown; primitivePorts?: unknown; canonical?: { kind?: unknown } }): number {
  if (typeof data.interfaceRowCount === "number" && Number.isFinite(data.interfaceRowCount)) {
    return Math.max(0, Math.floor(data.interfaceRowCount))
  }

  if (Array.isArray(data.primitivePorts)) {
    return data.primitivePorts.filter((port): port is PortLike => Boolean(port) && typeof port === "object" && (
      (port as PortLike).direction === "input" || (port as PortLike).direction === "output"
    )).length
  }

  switch (data.canonical?.kind) {
    case "schedule":
      return 1
    case "source":
    case "sink":
      return 2
    default:
      return 0
  }
}

export function workflowNodeSizeForData(data: { interfaceRowCount?: unknown; primitivePorts?: unknown; canonical?: { kind?: unknown } }) {
  return workflowNodeSize(workflowNodePortRowCount(data))
}

/** The only density contract for canvas and workflow nodes. */
export function workflowNodeDensity(zoom: number, contextualZoom = true): WorkflowNodeDensity {
  if (!contextualZoom) return "high"
  if (zoom < 0.5) return "low"
  if (zoom < 1) return "mid"
  return "high"
}
