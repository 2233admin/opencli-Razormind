import dagre from "dagre"
import { stratify, tree } from "d3-hierarchy"
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
} from "d3-force"
import ELK from "elkjs/lib/elk.bundled.js"
import { Position } from "@xyflow/react"
import type { WorkflowNode, WorkflowEdge } from "./types"
import { workflowNodeSize, workflowNodeSizeForData } from "./node-geometry"
export type LayoutDirection = "TB" | "LR"
export type LayoutEngine = "dagre" | "d3-hierarchy" | "elk" | "d3-force"

function nodeSize(node: WorkflowNode) {
  const fallback = node.type === "workflow"
    ? workflowNodeSizeForData(node.data)
    : workflowNodeSize()
  return {
    width: node.measured?.width ?? (node.width as number) ?? fallback.width,
    height: node.measured?.height ?? (node.height as number) ?? fallback.height,
  }
}

function withHandles(node: WorkflowNode, isHorizontal: boolean): WorkflowNode {
  return {
    ...node,
    targetPosition: isHorizontal ? Position.Left : Position.Top,
    sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
  }
}

/* -------------------------------- dagre -------------------------------- */
function layoutDagre(nodes: WorkflowNode[], edges: WorkflowEdge[], direction: LayoutDirection) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 90, marginx: 40, marginy: 40 })
  const isHorizontal = direction === "LR"
  const layoutNodes = nodes.filter((n) => !n.parentId)

  layoutNodes.forEach((node) => {
    const { width, height } = nodeSize(node)
    g.setNode(node.id, { width, height })
  })
  edges.forEach((edge) => {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) g.setEdge(edge.source, edge.target)
  })
  dagre.layout(g)

  return nodes.map((node) => {
    if (node.parentId) return node
    const pos = g.node(node.id)
    if (!pos) return node
    const { width, height } = nodeSize(node)
    return { ...withHandles(node, isHorizontal), position: { x: pos.x - width / 2, y: pos.y - height / 2 } }
  })
}

/* ---------------------------- d3-hierarchy ---------------------------- */
function layoutD3Hierarchy(nodes: WorkflowNode[], edges: WorkflowEdge[], direction: LayoutDirection) {
  const isHorizontal = direction === "LR"
  const roots = nodes.filter((n) => !n.parentId)
  if (roots.length === 0) return nodes

  const parentOf = new Map<string, string | undefined>()
  roots.forEach((n) => parentOf.set(n.id, undefined))
  edges.forEach((e) => {
    if (parentOf.has(e.target) && parentOf.get(e.target) === undefined && e.source !== e.target) {
      parentOf.set(e.target, e.source)
    }
  })

  // ensure a single virtual root to allow forests
  const VIRTUAL = "__root__"
  const strat = stratify<WorkflowNode | { id: string }>()
    .id((d) => d.id)
    .parentId((d) => {
      if (d.id === VIRTUAL) return undefined
      return parentOf.get(d.id) ?? VIRTUAL
    })

  let hierarchy
  try {
    hierarchy = strat([{ id: VIRTUAL } as { id: string }, ...roots])
  } catch {
    return layoutDagre(nodes, edges, direction)
  }

  const layout = tree<WorkflowNode | { id: string }>().nodeSize([200, 160])
  const rootPoint = layout(hierarchy)

  const positions = new Map<string, { x: number; y: number }>()
  rootPoint.each((d) => {
    if (d.id === VIRTUAL) return
    positions.set(d.id!, isHorizontal ? { x: d.y, y: d.x } : { x: d.x, y: d.y })
  })

  return nodes.map((node) => {
    const p = positions.get(node.id)
    if (!p) return node
    return { ...withHandles(node, isHorizontal), position: p }
  })
}

/* -------------------------------- elk -------------------------------- */
const elk = new ELK()
export async function layoutElk(nodes: WorkflowNode[], edges: WorkflowEdge[], direction: LayoutDirection) {
  const isHorizontal = direction === "LR"
  const layoutNodes = nodes.filter((n) => !n.parentId)
  const layoutNodeIds = new Set(layoutNodes.map((n) => n.id))
  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": isHorizontal ? "RIGHT" : "DOWN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.spacing.nodeNodeBetweenLayers": "110",
      "elk.layered.spacing.edgeNodeBetweenLayers": "48",
      "elk.spacing.nodeNode": "72",
      "elk.padding": "[top=40,left=40,bottom=40,right=40]",
    },
    children: layoutNodes.map((n) => {
      const { width, height } = nodeSize(n)
      return { id: n.id, width, height }
    }),
    edges: edges
      .filter((e) => layoutNodeIds.has(e.source) && layoutNodeIds.has(e.target))
      .map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }

  const res = await elk.layout(graph)
  const positions = new Map<string, { x: number; y: number }>()
  res.children?.forEach((c) => positions.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 }))

  return nodes.map((node) => {
    const p = positions.get(node.id)
    if (!p) return node
    return { ...withHandles(node, isHorizontal), position: p }
  })
}

/* ------------------------------ d3-force ------------------------------ */
type ForceNode = SimulationNodeDatum & { id: string }
export function layoutForce(nodes: WorkflowNode[], edges: WorkflowEdge[]) {
  const layoutNodes = nodes.filter((n) => !n.parentId)
  const simNodes: ForceNode[] = layoutNodes.map((n) => ({ id: n.id, x: n.position.x, y: n.position.y }))
  const simLinks = edges
    .filter((e) => layoutNodes.some((n) => n.id === e.source) && layoutNodes.some((n) => n.id === e.target))
    .map((e) => ({ source: e.source, target: e.target }))

  const sim = forceSimulation(simNodes)
    .force("charge", forceManyBody().strength(-800))
    .force(
      "link",
      forceLink(simLinks)
        .id((d) => (d as ForceNode).id)
        .distance(220)
        .strength(0.6),
    )
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(120))
    .stop()

  for (let i = 0; i < 300; i++) sim.tick()

  const positions = new Map<string, { x: number; y: number }>()
  simNodes.forEach((n) => positions.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 }))

  return nodes.map((node) => {
    const p = positions.get(node.id)
    if (!p) return node
    return { ...node, position: p }
  })
}

/* ------------------------------ dispatcher ------------------------------ */
export async function getLayoutedElements(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  direction: LayoutDirection = "TB",
  engine: LayoutEngine = "elk",
): Promise<{ nodes: WorkflowNode[]; edges: WorkflowEdge[] }> {
  let layouted: WorkflowNode[]
  switch (engine) {
    case "d3-hierarchy":
      layouted = layoutD3Hierarchy(nodes, edges, direction)
      break
    case "elk":
      layouted = await layoutElk(nodes, edges, direction)
      break
    case "d3-force":
      layouted = layoutForce(nodes, edges)
      break
    default:
      layouted = layoutDagre(nodes, edges, direction)
  }
  return { nodes: layouted, edges }
}
