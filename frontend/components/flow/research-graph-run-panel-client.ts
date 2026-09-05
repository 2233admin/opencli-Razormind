import {
  fetchWorkflowResearchGraph,
  fetchWorkspaceWorkflowResearchGraph,
  mutateWorkflowResearchGraph,
  mutateWorkspaceWorkflowResearchGraph,
  type WorkflowResearchGraphEntity,
  type WorkflowResearchGraphMutationAction,
  type WorkflowResearchGraphMutationResponse,
  type WorkflowResearchGraphProjection,
  type WorkflowResearchGraphScope,
} from "@/lib/workflow/research-graph-client"

import type { RunPanelExtensionContext } from "@/lib/workflow/run-panel-extensions"

export function researchGraphRunPanelRequestOptions(
  context: Pick<RunPanelExtensionContext, "authorization" | "scope">,
): ResearchGraphRunPanelRequestOptions {
  return { authorization: context.authorization, scope: context.scope }
}

export type ResearchGraphRunPanelRequestOptions = {
  authorization: string | null
  scope?: WorkflowResearchGraphScope | null
}

export async function loadResearchGraphRunPanel(
  runId: string,
  options: ResearchGraphRunPanelRequestOptions,
): Promise<WorkflowResearchGraphProjection | null> {
  try {
    return options.scope
      ? await fetchWorkspaceWorkflowResearchGraph(options.scope, runId, options)
      : await fetchWorkflowResearchGraph(runId, options)
  } catch {
    return null
  }
}

export async function mutateResearchGraphRunPanel(
  graph: WorkflowResearchGraphProjection | null,
  entity: WorkflowResearchGraphEntity,
  action: WorkflowResearchGraphMutationAction,
  options: ResearchGraphRunPanelRequestOptions,
): Promise<WorkflowResearchGraphMutationResponse> {
  const request = {
    schemaVersion: 1 as const,
    idempotencyKey: crypto.randomUUID(),
    expectedRevision: graph?.currentRevision ?? null,
    expectedSequence: graph?.lastSequence ?? 0,
    action,
    traceId: entity.traceId,
    nodeId: entity.nodeId,
    ...(action === "propose"
      ? {
          entity: {
            id: entity.id,
            kind: entity.kind,
            label: entity.label,
            sourceIds: entity.sourceIds,
            evidenceIds: entity.evidenceIds,
            attributes: entity.attributes,
          },
        }
      : { targetId: entity.id }),
  }
  return options.scope
    ? mutateWorkspaceWorkflowResearchGraph(options.scope, entity.runId, request, options)
    : mutateWorkflowResearchGraph(entity.runId, request, options)
}
