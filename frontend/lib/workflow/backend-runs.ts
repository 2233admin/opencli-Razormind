import type { WorkflowProject } from "./schema"
import { workflowRequestAuthHeaders } from "./request-auth"

type ApiResponse<T> = {
  success?: boolean
  data?: T
  error?: string
  message?: string
}

export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "waiting"
  | "partial"
  | "partial_success"
  | "blocked"
  | "completed"
  | "failed"

export type WorkflowRunTrigger = {
  kind: "manual" | "ai" | "schedule" | "webhook"
  triggerNodeId?: string
  requestId?: string
  idempotencyKey?: string
}

export type WorkflowRunInput = {
  payload: Record<string, unknown>
  headers?: Record<string, string>
  query?: Record<string, string>
  source?: "operator" | "external" | "automation" | "webhook"
  sourceId?: string
}

type ManualInputSchema = {
  required: string[]
  properties: Record<string, {
    type?: string
    format?: string
    title?: string
    accept?: string
  }>
  additionalProperties: boolean
}

export type WorkflowRunFileInput = {
  name: string
  title: string
  accept: string
}

export type WorkflowRunScope = {
  workspaceId: string
  projectId: string
  workflowId: string
}

function withWorkflowRunScope(endpoint: string, scope?: WorkflowRunScope): string {
  if (!scope) return endpoint
  const search = new URLSearchParams({
    workspace: scope.workspaceId,
    project: scope.projectId,
    workflow: scope.workflowId,
  })
  return `${endpoint}?${search.toString()}`
}

function manualInputSchema(project: WorkflowProject): ManualInputSchema | null {
  const trigger = project.nodes.find(
    (node) => node.kind === "schedule" && node.capability === "trigger" && node.params.mode === "manual",
  )
  const value = trigger?.params.inputSchema
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const schema = value as Record<string, unknown>
  const properties = schema.properties
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) return null
  return {
    required: Array.isArray(schema.required)
      ? schema.required.filter((item): item is string => typeof item === "string")
      : [],
    properties: Object.fromEntries(
      Object.entries(properties).filter((entry): entry is [string, ManualInputSchema["properties"][string]] => (
        Boolean(entry[1]) && typeof entry[1] === "object" && !Array.isArray(entry[1])
      )),
    ),
    additionalProperties: schema.additionalProperties !== false,
  }
}

export function buildWorkflowRunInputTemplate(project: WorkflowProject): Record<string, unknown> {
  const schema = manualInputSchema(project)
  if (!schema) return {}
  return Object.fromEntries(
    schema.required
      .filter((name) => schema.properties[name]?.format !== "binary")
      .map((name) => [name, schema.properties[name]?.type === "string" ? "" : null]),
  )
}

export function getWorkflowRunFileInput(project: WorkflowProject): WorkflowRunFileInput | null {
  const schema = manualInputSchema(project)
  if (!schema) return null
  const name = schema.required.find((candidate) => schema.properties[candidate]?.format === "binary")
  if (!name) return null
  const property = schema.properties[name]
  return {
    name,
    title: property?.title?.trim() || name,
    accept: property?.accept?.trim() || "",
  }
}

export function parseWorkflowRunInput(project: WorkflowProject, text: string): WorkflowRunInput {
  const decoded: unknown = JSON.parse(text)
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
    throw new Error("Workflow Run input must be a JSON object")
  }
  const payload = decoded as Record<string, unknown>
  const schema = manualInputSchema(project)
  if (schema) {
    for (const name of schema.required) {
      if (schema.properties[name]?.format === "binary") continue
      const value = payload[name]
      if (value === undefined || value === null || (typeof value === "string" && !value.trim())) {
        throw new Error(`${name} is required`)
      }
      if (schema.properties[name]?.type === "string" && typeof value !== "string") {
        throw new Error(`${name} must be a string`)
      }
    }
    if (!schema.additionalProperties) {
      const unexpected = Object.keys(payload).find((name) => !(name in schema.properties))
      if (unexpected) throw new Error(`${unexpected} is not allowed`)
    }
  }
  return { payload, source: "operator" }
}

export type WorkflowResearchStatus =
  | "running"
  | "needs_evidence"
  | "final"
  | "incomplete"
  | "blocked"
  | "failed"

export type WorkflowNodeRunEventType =
  | "queued"
  | "started"
  | "waiting"
  | "blocked"
  | "batch_ready"
  | "tool_call_started"
  | "tool_call_completed"
  | "partial"
  | "completed"
  | "failed"

export type WorkflowRunBlockReason = {
  code: string
  message: string
  source?: string | null
  details: Record<string, unknown>
}

export type WorkflowRunBatchReference = {
  batchId: string
  itemCount: number
  recordCount: number
  sourceGroup?: string | null
  adapterTaskId?: string | null
  odpRef?: string | null
  manifestUri?: string | null
}

export type WorkflowEvidenceBatchSummary = WorkflowRunBatchReference & {
  runId: string
  traceId: string
  nodeId: string
  nodePath?: string[]
  packageNodeId?: string | null
  internalNodeId?: string | null
  status: WorkflowRunStatus
}

export type WorkflowEvidenceBatchListResponse = {
  runId: string
  batches: WorkflowEvidenceBatchSummary[]
  nextCursor?: string | null
}

export type WorkflowSourceCoverage = {
  sourceGroup?: string | null
  status: WorkflowRunStatus
  batchCount: number
  itemCount: number
  recordCount: number
}

export type WorkflowEvidenceBatchDetail = {
  runId: string
  batch: WorkflowEvidenceBatchSummary
  manifestUri?: string | null
  odpRef?: string | null
  recordCount: number
  itemCount: number
  sourceCoverage: WorkflowSourceCoverage
}

export type WorkflowMissingSource = {
  nodeId: string
  sourceGroup?: string | null
  status: WorkflowRunStatus
  reasons: WorkflowRunBlockReason[]
}

export type WorkflowEvidenceSummary = {
  summaryId: string
  sourceGroup?: string | null
  status: WorkflowRunStatus
  batchIds: string[]
  itemCount: number
  recordCount: number
}

export type WorkflowProjectionArtifact = {
  artifactId: string
  batchId: string
  nodeId: string
  manifestUri?: string | null
  odpRef?: string | null
}

export type WorkflowEvidenceBatchProjection = {
  runId: string
  traceId: string
  status: WorkflowRunStatus
  nodes: WorkflowRunNodeState[]
  clusters: Array<Record<string, unknown>>
  missingSources: WorkflowMissingSource[]
  summaries: WorkflowEvidenceSummary[]
  conflicts: Array<Record<string, unknown>>
  artifacts: WorkflowProjectionArtifact[]
}

const workflowRunEndpoint = (runId: string) => `/api/workflow/runs/${encodeURIComponent(runId)}`

const workflowEvidenceBatchEndpoint = (runId: string, batchId?: string) => {
  const root = `${workflowRunEndpoint(runId)}/evidence-batches`
  return batchId ? `${root}/${encodeURIComponent(batchId)}` : root
}

export type WorkflowNodeRunEvent = {
  id: string
  sequence: number
  workflowId: string
  workflowRunId: string
  traceId: string
  nodeId: string
  nodePath?: string[]
  eventType: WorkflowNodeRunEventType
  createdAt: string
  packageNodeId?: string | null
  internalNodeId?: string | null
  sourceGroup?: string | null
  message?: string | null
  blockReason?: WorkflowRunBlockReason | null
  batch?: WorkflowRunBatchReference | null
  details: Record<string, unknown>
}

export type WorkflowRunNodeState = {
  nodeId: string
  status: WorkflowRunStatus
  nodePath?: string[]
  packageNodeId?: string | null
  internalNodeId?: string | null
  sourceGroups: string[]
  latestEventId?: string | null
  eventCount: number
  blockReasons: WorkflowRunBlockReason[]
  batches: WorkflowRunBatchReference[]
}

export type WorkflowRunProjection = {
  workflowId: string
  runId: string
  traceId: string
  valid: boolean
  status: WorkflowRunStatus
  packageNodeId?: string | null
  startedAt: string
  updatedAt: string
  eventCount: number
  nodeStates: WorkflowRunNodeState[]
  errors: Array<{ code: string; message: string; node_id?: string | null; edge_id?: string | null }>
}

export type WorkspaceWorkflowRunScope = {
  workspaceId: string
  projectId: string
  workflowId: string
}

type WorkspaceWorkflowRunTrace = {
  trace: WorkflowRunTraceResponse
}

function workspaceWorkflowRunEndpoint(
  scope: WorkspaceWorkflowRunScope,
  runId?: string,
): string {
  const root = `/api/v1/workspaces/${encodeURIComponent(scope.workspaceId)}`
    + `/projects/${encodeURIComponent(scope.projectId)}`
    + `/workflows/${encodeURIComponent(scope.workflowId)}/runs`
  return runId ? `${root}/${encodeURIComponent(runId)}` : root
}

function workspaceWorkflowEvidenceBatchEndpoint(
  scope: WorkspaceWorkflowRunScope,
  runId: string,
  batchId?: string,
): string {
  const root = `${workspaceWorkflowRunEndpoint(scope, runId)}/evidence-batches`
  return batchId ? `${root}/${encodeURIComponent(batchId)}` : root
}

export type WorkflowResearchLedgerEntry = {
  runId: string
  parentRunId?: string | null
  rootRunId: string
  iteration: number
  additionalCollectionCount: number
  revisionId?: string | null
  parentRevisionId?: string | null
  claimSetHash?: string | null
  semanticClaimSetHash?: string | null
  scenarioSetHash?: string | null
  decision?: "finalize" | "collect_more" | "stop_incomplete" | null
  researchStatus: WorkflowResearchStatus
  stopReason?: string | null
  proposal?: {
    proposalId?: string
    action?: string
    gaps?: string[]
    nextIteration?: number
    nextAdditionalCollectionCount?: number
  } | null
  gaps: string[]
  publishAllowed?: boolean | null
  gateReasons: string[]
  evidenceRefs: Array<Record<string, unknown>>
  createdAt: string
}

export type WorkflowResearchLedgerResponse = {
  ledgerId: string
  rootRunId: string
  currentRunId: string
  entries: WorkflowResearchLedgerEntry[]
}

export type WorkflowResearchContinuationResponse = {
  ledgerId: string
  parentRunId: string
  childRunId: string
  iteration: number
  additionalCollectionCount: number
  researchStatus: WorkflowResearchStatus
  replayed: boolean
  projectionPath: string
  eventsPath: string
  projection: WorkflowRunProjection
}

export type WorkflowRunCheckpoint = {
  checkpointId: string
  workflowId: string
  runId: string
  traceId: string
  status: WorkflowRunStatus
  valid: boolean
  eventCount: number
  lastSequence: number
  updatedAt: string
  nodeStates: WorkflowRunNodeState[]
  sourceOutputNodeIds: string[]
  sourceOutputItemCount: number
  canContinueWithSourceOutputs: boolean
  continuationPath: string
  tracePath: string
}

export type WorkflowRunTraceResponse = {
  projection: WorkflowRunProjection
  checkpoint: WorkflowRunCheckpoint
  events: WorkflowNodeRunEvent[]
  filters: {
    afterSequence?: number | null
    nodeId?: string | null
    eventType?: WorkflowNodeRunEventType | null
    limit?: number | null
  }
  nextAfterSequence: number
}

export type WorkflowRunStreamReplay = {
  events: WorkflowNodeRunEvent[]
  projection: WorkflowRunProjection | null
}

export function inferWorkflowRunTrigger(project: WorkflowProject): WorkflowRunTrigger {
  const scheduleNode = project.nodes.find(
    (node) => node.kind === "schedule" && node.capability === "trigger",
  )
  if (!scheduleNode) return { kind: "manual" }
  return scheduleNode.params.mode === "manual"
    ? { kind: "manual", triggerNodeId: scheduleNode.id }
    : { kind: "schedule", triggerNodeId: scheduleNode.id }
}

export async function startWorkflowRun(
  project: WorkflowProject,
  options: {
    authorization?: string | null
    runId?: string
    traceId?: string
    ephemeral?: boolean
    packageNodeId?: string
    sourceOutputs?: Record<string, Array<Record<string, unknown>>>
    trigger?: WorkflowRunTrigger
    input?: WorkflowRunInput
    questionBankFile?: File
    scope?: WorkflowRunScope
    conversationId?: string | null
  } = {},
): Promise<WorkflowRunProjection> {
  if (options.questionBankFile && options.sourceOutputs) {
    throw new Error("Question bank file Runs do not accept sourceOutputs")
  }
  const trigger = options.trigger ?? inferWorkflowRunTrigger(project)
  const draftRequest = {
    project,
    ...(options.runId ? { runId: options.runId } : {}),
    ...(options.traceId ? { traceId: options.traceId } : {}),
    ...(options.packageNodeId ? { packageNodeId: options.packageNodeId } : {}),
    ...(options.sourceOutputs ? { sourceOutputs: options.sourceOutputs } : {}),
    ...(options.input ? { input: options.input } : {}),
    trigger,
  }
  const request = options.scope
    ? {
        inputs: options.input?.payload ?? {},
        response_mode: "async",
        user: options.input?.sourceId?.trim() || "studio-operator",
        ...(trigger.requestId ? { request_id: trigger.requestId } : {}),
        ...(trigger.idempotencyKey ? { idempotency_key: trigger.idempotencyKey } : {}),
        ...(options.conversationId ? { conversation_id: options.conversationId } : {}),
      }
    : draftRequest
  const questionBankBody = options.questionBankFile ? new FormData() : null
  if (questionBankBody && options.questionBankFile) {
    questionBankBody.set("questionBank", options.questionBankFile)
    questionBankBody.set("request", JSON.stringify(request))
  }
  const questionBankEndpoint = withWorkflowRunScope("/api/workflow/run/question-bank", options.scope)
  const response = await fetch(questionBankBody ? questionBankEndpoint : "/api/workflow/run", {
    method: "POST",
    headers: {
      ...(!questionBankBody ? { "Content-Type": "application/json" } : {}),
      ...workflowRequestAuthHeaders(options.authorization),
    },
    body: questionBankBody ?? JSON.stringify({
      project,
      ...(options.runId ? { runId: options.runId } : {}),
      ...(options.traceId ? { traceId: options.traceId } : {}),
      ...(options.ephemeral ? { ephemeral: true } : {}),
      ...(options.packageNodeId ? { packageNodeId: options.packageNodeId } : {}),
      ...(options.sourceOutputs ? { sourceOutputs: options.sourceOutputs } : {}),
      trigger: options.trigger ?? inferWorkflowRunTrigger(project),
    }),
  })
  return readApiResponse(response, "Workflow run failed")
}

export async function startWorkspaceWorkflowRun(
  scope: WorkspaceWorkflowRunScope,
  options: {
    authorization?: string | null
    input?: WorkflowRunInput
    conversationId?: string | null
  } = {},
): Promise<WorkflowRunProjection> {
  const response = await fetch(workspaceWorkflowRunEndpoint(scope), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...workflowRequestAuthHeaders(options.authorization),
    },
    body: JSON.stringify({
      inputs: options.input?.payload ?? {},
      user: "studio-run-trace",
      ...(options.conversationId ? { conversation_id: options.conversationId } : {}),
    }),
  })
  return readApiResponse(response, "Published Studio workflow run failed")
}

export async function replayWorkspaceWorkflowRunTrace(
  scope: WorkspaceWorkflowRunScope,
  runId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowRunStreamReplay> {
  const response = await fetch(`${workspaceWorkflowRunEndpoint(scope, runId)}/trace`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  const result = await readApiResponse<WorkspaceWorkflowRunTrace>(
    response,
    "Published Studio workflow trace failed",
  )
  return {
    events: result.trace.events,
    projection: result.trace.projection,
  }
}

export async function fetchWorkflowRunProjection(
  runId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowRunProjection> {
  const response = await fetch(workflowRunEndpoint(runId), {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow run projection failed")
}

export async function fetchWorkflowRunCheckpoint(
  runId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowRunCheckpoint> {
  const response = await fetch(`${workflowRunEndpoint(runId)}/checkpoint`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow run checkpoint failed")
}

export async function queryWorkflowRunTrace(
  runId: string,
  options: {
    authorization?: string | null
    scope?: WorkflowRunScope
    signal?: AbortSignal
    afterSequence?: number
    nodeId?: string
    eventType?: WorkflowNodeRunEventType
    limit?: number
  } = {},
): Promise<WorkflowRunTraceResponse> {
  const search = new URLSearchParams()
  if (typeof options.afterSequence === "number") search.set("afterSequence", String(options.afterSequence))
  if (options.nodeId) search.set("nodeId", options.nodeId)
  if (options.eventType) search.set("eventType", options.eventType)
  if (typeof options.limit === "number") search.set("limit", String(options.limit))
  const endpoint = withWorkflowRunScope(`${workflowRunEndpoint(runId)}/trace`, options.scope)
  const separator = endpoint.includes("?") ? "&" : "?"
  const suffix = search.size > 0 ? `${separator}${search.toString()}` : ""
  const response = await fetch(`${endpoint}${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
    signal: options.signal,
  })
  const result = await readApiResponse<WorkflowRunTraceResponse | { trace: WorkflowRunTraceResponse }>(
    response, "Workflow run trace query failed",
  )
  return "trace" in result ? result.trace : result
}

export async function resumeGaojixingWorkflowRun(
  runId: string,
  options: { authorization?: string | null; scope?: WorkflowRunScope; expectedChatUrl?: string } = {},
): Promise<WorkflowRunProjection> {
  const expectedChatUrl = options.expectedChatUrl?.trim()
  if (expectedChatUrl && !/^https:\/\/www\.doubao\.com\/chat\/\d+$/.test(expectedChatUrl)) {
    throw new Error("请填写验证后的豆包正式会话链接")
  }
  const response = await fetch(
    withWorkflowRunScope(`${workflowRunEndpoint(runId)}/gaojixing/resume`, options.scope),
    {
      method: "POST",
      headers: {
        ...workflowRequestAuthHeaders(options.authorization),
        ...(expectedChatUrl ? { "Content-Type": "application/json" } : {}),
      },
      ...(expectedChatUrl ? { body: JSON.stringify({ expectedChatUrl }) } : {}),
    },
  )
  return readApiResponse(response, "Gaojixing Run resume failed")
}

export async function fetchWorkflowRunEvents(
  runId: string,
  options: {
    authorization?: string | null
    afterSequence?: number
    nodeId?: string
    eventType?: WorkflowNodeRunEventType
    limit?: number
  } = {},
): Promise<WorkflowNodeRunEvent[]> {
  const search = new URLSearchParams()
  if (typeof options.afterSequence === "number") search.set("afterSequence", String(options.afterSequence))
  if (options.nodeId) search.set("nodeId", options.nodeId)
  if (options.eventType) search.set("eventType", options.eventType)
  if (typeof options.limit === "number") search.set("limit", String(options.limit))
  const suffix = search.size > 0 ? `?${search.toString()}` : ""
  const response = await fetch(`${workflowRunEndpoint(runId)}/events${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow run events failed")
}

export async function continueWorkflowRunWithSourceOutputs(
  runId: string,
  sourceOutputs: Record<string, Array<Record<string, unknown>>>,
  options: { authorization?: string | null } = {},
): Promise<WorkflowRunProjection> {
  const response = await fetch(`${workflowRunEndpoint(runId)}/source-outputs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...workflowRequestAuthHeaders(options.authorization),
    },
    body: JSON.stringify({ sourceOutputs }),
  })
  return readApiResponse(response, "Workflow run continuation failed")
}

export async function fetchWorkflowResearchLedger(
  runId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowResearchLedgerResponse> {
  const response = await fetch(`${workflowRunEndpoint(runId)}/research-ledger`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow research ledger failed")
}

export async function continueWorkflowResearch(
  runId: string,
  input: {
    expectedRevisionId: string
    proposalId: string
    idempotencyKey: string
    sourceOutputs: Record<string, Array<Record<string, unknown>>>
  },
  options: { authorization?: string | null } = {},
): Promise<WorkflowResearchContinuationResponse> {
  const response = await fetch(`${workflowRunEndpoint(runId)}/research-continuations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...workflowRequestAuthHeaders(options.authorization),
    },
    body: JSON.stringify(input),
  })
  return readApiResponse(response, "Workflow research continuation failed")
}

export async function replayWorkflowRunEventStream(
  runId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowRunStreamReplay> {
  const retryDelays = [0, 75, 200, 500]
  for (const [index, delay] of retryDelays.entries()) {
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay))
    }
    const response = await fetch(`${workflowRunEndpoint(runId)}/events/stream`, {
      headers: {
        ...workflowRequestAuthHeaders(options.authorization),
      },
      cache: "no-store",
    })
    if (response.ok) {
      const text = await response.text()
      return parseWorkflowRunEventStream(text)
    }
    if (response.status !== 404 || index === retryDelays.length - 1) {
      const payload = (await response.json().catch(() => null)) as { message?: string; error?: string } | null
      throw new Error(payload?.message ?? payload?.error ?? `Workflow event stream failed (${response.status})`)
    }
  }
  throw new Error("Workflow event stream replay exhausted")
}

export async function fetchWorkflowEvidenceBatches(
  runId: string,
  options: {
    authorization?: string | null
    nodeId?: string
    sourceGroup?: string
    cursor?: string
    limit?: number
  } = {},
): Promise<WorkflowEvidenceBatchListResponse> {
  const search = new URLSearchParams()
  if (options.nodeId) search.set("node_id", options.nodeId)
  if (options.sourceGroup) search.set("source_group", options.sourceGroup)
  if (options.cursor) search.set("cursor", options.cursor)
  if (typeof options.limit === "number") search.set("limit", String(options.limit))
  const suffix = search.size > 0 ? `?${search.toString()}` : ""
  const response = await fetch(`${workflowEvidenceBatchEndpoint(runId)}${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow evidence batches failed")
}

export async function fetchWorkspaceWorkflowEvidenceBatches(
  scope: WorkspaceWorkflowRunScope,
  runId: string,
  options: {
    authorization?: string | null
    nodeId?: string
    sourceGroup?: string
    cursor?: string
    limit?: number
  } = {},
): Promise<WorkflowEvidenceBatchListResponse> {
  const search = new URLSearchParams()
  if (options.nodeId) search.set("node_id", options.nodeId)
  if (options.sourceGroup) search.set("source_group", options.sourceGroup)
  if (options.cursor) search.set("cursor", options.cursor)
  if (typeof options.limit === "number") search.set("limit", String(options.limit))
  const suffix = search.size > 0 ? `?${search.toString()}` : ""
  const response = await fetch(`${workspaceWorkflowEvidenceBatchEndpoint(scope, runId)}${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Published Studio workflow evidence batches failed")
}

export async function fetchWorkflowEvidenceBatchDetail(
  runId: string,
  batchId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowEvidenceBatchDetail> {
  const response = await fetch(workflowEvidenceBatchEndpoint(runId, batchId), {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow evidence batch detail failed")
}

export async function fetchWorkspaceWorkflowEvidenceBatchDetail(
  scope: WorkspaceWorkflowRunScope,
  runId: string,
  batchId: string,
  options: { authorization?: string | null } = {},
): Promise<WorkflowEvidenceBatchDetail> {
  const response = await fetch(workspaceWorkflowEvidenceBatchEndpoint(scope, runId, batchId), {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Published Studio workflow evidence batch detail failed")
}

export async function fetchWorkflowEvidenceBatchProjection(
  runId: string,
  options: {
    authorization?: string | null
    nodeId?: string
    sourceGroup?: string
    include?: string[]
  } = {},
): Promise<WorkflowEvidenceBatchProjection> {
  const search = new URLSearchParams()
  if (options.nodeId) search.set("node_id", options.nodeId)
  if (options.sourceGroup) search.set("source_group", options.sourceGroup)
  for (const value of options.include ?? []) search.append("include", value)
  const suffix = search.size > 0 ? `?${search.toString()}` : ""
  const response = await fetch(`${workflowEvidenceBatchEndpoint(runId)}/projection${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Workflow evidence batch projection failed")
}

export async function fetchWorkspaceWorkflowEvidenceBatchProjection(
  scope: WorkspaceWorkflowRunScope,
  runId: string,
  options: {
    authorization?: string | null
    nodeId?: string
    sourceGroup?: string
    include?: string[]
  } = {},
): Promise<WorkflowEvidenceBatchProjection> {
  const search = new URLSearchParams()
  if (options.nodeId) search.set("node_id", options.nodeId)
  if (options.sourceGroup) search.set("source_group", options.sourceGroup)
  for (const value of options.include ?? []) search.append("include", value)
  const suffix = search.size > 0 ? `?${search.toString()}` : ""
  const response = await fetch(`${workspaceWorkflowRunEndpoint(scope, runId)}/projection${suffix}`, {
    headers: {
      ...workflowRequestAuthHeaders(options.authorization),
    },
    cache: "no-store",
  })
  return readApiResponse(response, "Published Studio workflow evidence batch projection failed")
}

export function parseWorkflowRunEventStream(text: string): WorkflowRunStreamReplay {
  const events: WorkflowNodeRunEvent[] = []
  let projection: WorkflowRunProjection | null = null

  for (const block of text.split(/\r?\n\r?\n/)) {
    if (!block.trim()) continue
    const eventName = block.match(/^event:\s*(.+)$/m)?.[1]?.trim()
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice("data:".length).trimStart())
      .join("\n")
    if (!eventName || !data) continue
    if (eventName === "node_event") {
      events.push(JSON.parse(data) as WorkflowNodeRunEvent)
    } else if (eventName === "run_state") {
      projection = JSON.parse(data) as WorkflowRunProjection
    }
  }

  return { events, projection }
}

async function readApiResponse<T>(response: Response, fallback: string): Promise<T> {
  const payload = (await response.json().catch(() => null)) as ApiResponse<T> | null
  if (!response.ok || !payload?.data) {
    throw new Error(payload?.message ?? payload?.error ?? `${fallback} (${response.status})`)
  }
  return payload.data
}
