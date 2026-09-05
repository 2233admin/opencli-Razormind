import type { Edge, Node } from "@xyflow/react"
import type { WorkflowResearchGraphEntity, WorkflowResearchGraphProjection } from "./research-graph-client"

export type ResearchGraphNodeData = WorkflowResearchGraphEntity & { displayLabel: string }
export type ResearchGraphFlowNode = Node<ResearchGraphNodeData, "default">
export type ResearchGraphFlowEdge = Edge<{ relation: WorkflowResearchGraphEntity }, "default">

const kindOrder = { source: 0, evidence: 1, claim: 2, relation: 3 } as const

function label(entity: WorkflowResearchGraphEntity) {
  return entity.label?.trim() || `${entity.kind}:${entity.id}`
}

export function researchGraphToReactFlow(graph: WorkflowResearchGraphProjection): {
  nodes: ResearchGraphFlowNode[]
  edges: ResearchGraphFlowEdge[]
} {
  const entities = [...graph.entities].sort((a, b) => kindOrder[a.kind] - kindOrder[b.kind] || a.id.localeCompare(b.id))
  const nodes = entities.map((entity, index) => ({
    id: `research:${entity.id}`,
    type: "default" as const,
    position: { x: (index % 4) * 220, y: Math.floor(index / 4) * 120 },
    data: { ...entity, displayLabel: label(entity) },
  }))
  const edges = [...graph.relations]
    .filter((relation) => relation.subjectId && relation.objectId)
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((relation) => ({
      id: `research:${relation.id}`,
      source: `research:${relation.subjectId}`,
      target: `research:${relation.objectId}`,
      type: "default" as const,
      data: { relation },
    }))
  return { nodes, edges }
}
