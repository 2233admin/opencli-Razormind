export type WorkflowResearchGraphEntity = {
  id: string
  kind: "source" | "evidence" | "claim" | "relation"
  label?: string | null
  sourceIds: string[]
  evidenceIds: string[]
  subjectId?: string | null
  objectId?: string | null
  attributes: Record<string, unknown>
  eventId: string
  sequence: number
  runId: string
  traceId: string
  nodeId: string
  revisionId?: string | null
  lineage: Record<string, string>
  state: "recorded" | "proposed" | "verified" | "rejected" | "retracted"
  authoritative: boolean
}

export type WorkflowResearchGraphProjection = {
  runId: string
  traceId: string
  eventCount: number
  lastSequence: number
  currentRevision?: string | null
  entities: WorkflowResearchGraphEntity[]
  relations: WorkflowResearchGraphEntity[]
}

export type WorkflowResearchGraphScope = {
  workspaceId: string
  projectId: string
  workflowId: string
}


export type ResearchGraphReadOptions = {
  authorization?: string | null
  upToSequence?: number
  revisionId?: string
  entityId?: string
  limit?: number
  scope?: WorkflowResearchGraphScope | null
}

export type WorkflowResearchGraphMutationAction = "propose" | "verify" | "reject" | "retract"

export type WorkflowResearchGraphMutationRequest = {
  schemaVersion: 1
  idempotencyKey: string
  expectedRevision?: string | null
  expectedSequence: number
  action: WorkflowResearchGraphMutationAction
  traceId: string
  nodeId: string
  revisionId?: string | null
  lineage?: Record<string, string>
  entity?: Omit<
    WorkflowResearchGraphEntity,
    "eventId" | "sequence" | "runId" | "traceId" | "nodeId" | "revisionId" | "lineage" | "state" | "authoritative"
  >
  targetId?: string
}

export type WorkflowResearchGraphMutationEvent = {
  id: string
  sequence: number
  workflowId: string
  workflowRunId: string
  traceId: string
  nodeId: string
  eventType: string
  createdAt: string
  details: Record<string, unknown>
}

export type WorkflowResearchGraphMutationResponse = {
  events: WorkflowResearchGraphMutationEvent[]
  graph: WorkflowResearchGraphProjection
}


function researchGraphEndpoint(
  runId: string,
  scope?: WorkflowResearchGraphScope,
): string {
  if (!scope) {
    return `/api/workflow/runs/${encodeURIComponent(runId)}/research-graph`
  }
  return `/api/v1/workspaces/${encodeURIComponent(scope.workspaceId)}`
    + `/projects/${encodeURIComponent(scope.projectId)}`
    + `/workflows/${encodeURIComponent(scope.workflowId)}`
    + `/runs/${encodeURIComponent(runId)}/research-graph`
}

async function read<T>(path: string, options: ResearchGraphReadOptions, fallback: string): Promise<T> {
  const search = new URLSearchParams()
  if (options.upToSequence !== undefined) search.set("upToSequence", String(options.upToSequence))
  if (options.revisionId) search.set("revisionId", options.revisionId)
  if (options.entityId) search.set("entityId", options.entityId)
  if (options.limit !== undefined) search.set("limit", String(options.limit))
  const response = await fetch(`${path}${search.size ? `?${search}` : ""}`, {
    headers: options.authorization ? { Authorization: options.authorization } : {},
    cache: "no-store",
  })
  const body = await response.json() as { data?: T; error?: string; message?: string }
  if (!response.ok || !body.data) throw new Error(body.error ?? body.message ?? fallback)
  return body.data
}

async function mutate(
  path: string,
  request: WorkflowResearchGraphMutationRequest,
  options: ResearchGraphReadOptions,
): Promise<WorkflowResearchGraphMutationResponse> {
  const response = await fetch(`${path}/mutations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(options.authorization ? { Authorization: options.authorization } : {}),
    },
    body: JSON.stringify(request),
  })
  const body = await response.json() as {
    data?: WorkflowResearchGraphMutationResponse
    error?: string
    message?: string
  }
  if (!response.ok || !body.data) {
    throw new Error(body.error ?? body.message ?? "ResearchGraph mutation failed")
  }
  return body.data
}

export function fetchWorkflowResearchGraph(
  runId: string,
  options: ResearchGraphReadOptions = {},
) {
  return read<WorkflowResearchGraphProjection>(
    researchGraphEndpoint(runId, options.scope ?? undefined),
    options,
    "ResearchGraph projection failed",
  )
}

export function fetchWorkspaceWorkflowResearchGraph(
  scope: WorkflowResearchGraphScope,
  runId: string,
  options: ResearchGraphReadOptions = {},
) {
  return read<WorkflowResearchGraphProjection>(
    researchGraphEndpoint(runId, scope),
    options,
    "Workspace ResearchGraph projection failed",
  )
}

export function mutateWorkflowResearchGraph(
  runId: string,
  request: WorkflowResearchGraphMutationRequest,
  options: ResearchGraphReadOptions = {},
) {
  return mutate(researchGraphEndpoint(runId, options.scope ?? undefined), request, options)
}

export function mutateWorkspaceWorkflowResearchGraph(
  scope: WorkflowResearchGraphScope,
  runId: string,
  request: WorkflowResearchGraphMutationRequest,
  options: ResearchGraphReadOptions = {},
) {
  return mutate(researchGraphEndpoint(runId, scope), request, options)
}
