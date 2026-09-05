import type {
  WorkflowResearchGraphEntity,
  WorkflowResearchGraphMutationAction,
} from "./research-graph-client"

export function researchGraphClaimActions(
  entity: Pick<WorkflowResearchGraphEntity, "kind" | "state">,
): WorkflowResearchGraphMutationAction[] {
  if (entity.kind !== "claim") return []
  if (entity.state === "proposed") return ["verify", "reject"]
  if (entity.state === "verified") return ["retract"]
  return []
}
