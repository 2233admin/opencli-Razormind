import { apiClient, rootClient } from "./client";
import { API_HEALTH_REQUEST_TIMEOUT_MS } from "./recovery";
import type { ApiHealthSnapshot } from "./restart-orchestration";
import type {
  AgentConversation,
  AgentConversationDetail,
  AgentConversationMessageResult,
  AgentConversationListFilters,
  CreateAgentConversationInput,
  SendAgentConversationMessageInput,
} from "./agent-conversations";
import type {
  AIAgent,
  AdvisoryReport,
  ApiResponse,
  BrowserActPack,
  ChromeEndpoint,
  CollectedRecord,
  CollectionTask,
  ConnectionTestResult,
  ControlActionRecord,
  CronSchedule,
  DashboardActivity,
  DashboardStats,
  DataSource,
  EdgeNode,
  EdgeNodeEvent,
  FeedProvider,
  FeedProviderConnectionTest,
  FeedProviderInput,
  FeedProviderWorkflowNode,
  FeedProviderWorkflowNodeInput,
  GovernedProjectSummary,
  KillSwitchState,
  ModelDefaultCandidate,
  ModelDefaultRead,
  ModelProvider,
  ModelProviderInput,
  ModelRole,
  NodeStats,
  NotificationLog,
  NotificationRule,
  NotificationRuleInput,
  OdpSystemState,
  OpinionMonitor,
  PlanGraph,
  PlanHealthRead,
  PlanRead,
  PlanRunRead,
  PresetsGrouped,
  ProviderModelDiscoveryInput,
  ProviderModelRead,
  ProviderModelSyncResult,
  RssCatalogImportResult,
  Skill,
  SkillBrief,
  SourceBinding,
  SourceBindingInput,
  SourceBindingRevision,
  SourceControlState,
  SourceMeasurementRecord,
  SystemConfig,
  TaskRun,
  TaskRunEvent,
  WorkerNode,
  WorkspaceSummary,
  ProjectSummary,
  ProjectRecordGraphPreview,
  ProjectBootstrapResult,
  ProjectAppType,
  ProjectRuntimeLogPage,
  ProjectRuntimeSummary,
  ProjectRuntimeTrace,
  WorkflowAssetSummary,
  WorkflowDraftRead,
  WorkflowVersionSummary,
  OperationsWorkItem,
  ApprovalDecision,
  ApprovalDecisionResult,
  OperationsAgent,
  OperationsAgentDraft,
  OperationsAgentMode,
  OperationsAgentProfile,
  OperationsAgentRun,
  OperationsAgentTeam,
  PublishedOperationsAgentVersion,
  Automation,
  WorkspaceSource,
  DeliveryConnection,
  DeliveryConnectionInput,
  FeishuBitableProbeInput,
  FeishuBitableProbeResult,
} from "./types";
import type { WorkflowProject } from "@/lib/workflow/schema";

export * from "./workspace-endpoints";
export * from "./browser-endpoints";
export * from "./workbench-endpoints";

export type SystemConfigPatch = Partial<Omit<SystemConfig, "agent_pool_endpoints" | "effective_cdp_endpoints">> & {
  agent_pool_endpoints?: string;
};

export const loginWithPassword = (username: string, password: string) =>
  apiClient
    .post<
      ApiResponse<{
        access_token: string;
        token_type: "bearer";
        using_default_password: boolean;
      }>
    >("/auth/login", { username, password })
    .then((r) => r.data.data);

export const changeLocalPassword = (
  currentPassword: string,
  newPassword: string,
) =>
  apiClient
    .post<ApiResponse<{ message: string }>>("/auth/password", {
      current_password: currentPassword,
      new_password: newPassword,
    })
    .then((r) => r.data.data);

export const listMyWorkspaces = () =>
  apiClient
    .get<ApiResponse<WorkspaceSummary[]>>("/workspaces")
    .then((r) => r.data.data);

export const listAgentConversations = (
  workspaceId: string,
  limit = 20,
  filters?: AgentConversationListFilters,
) =>
  apiClient
    .get<ApiResponse<AgentConversation[]>>('/chat/sessions', {
      params: {
        workspace_id: workspaceId,
        limit,
        ...(filters?.project_id ? { project_id: filters.project_id } : {}),
        ...(filters?.workflow_id ? { workflow_id: filters.workflow_id } : {}),
        ...(filters?.run_id ? { run_id: filters.run_id } : {}),
      },
    })
    .then((r) => r.data.data)

export const getAgentConversation = (conversationId: string, afterSequence = 0, limit = 50) =>
  apiClient
    .get<ApiResponse<AgentConversationDetail>>(`/chat/sessions/${conversationId}`, {
      params: { after_sequence: afterSequence, limit },
    })
    .then((r) => r.data.data)

type CreateAgentConversationResponse = {
  conversation_id: string
  session: AgentConversation
}

export const createAgentConversation = (data: CreateAgentConversationInput) =>
  apiClient
    .post<ApiResponse<CreateAgentConversationResponse>>('/chat/sessions', data)
    .then((r) => r.data.data.session)

export const sendAgentConversationMessage = (
  conversationId: string,
  data: SendAgentConversationMessageInput,
) =>
  apiClient
    .post<ApiResponse<AgentConversationMessageResult>>(`/chat/sessions/${conversationId}/messages`, data)
    .then((r) => r.data.data)

export const closeAgentConversation = (conversationId: string) =>
  apiClient.post<ApiResponse<AgentConversation>>(`/chat/sessions/${conversationId}/close`).then((r) => r.data.data)

export const listWorkspaceProjects = (workspaceId: string) =>
  apiClient.get<ApiResponse<ProjectSummary[]>>(`/workspaces/${workspaceId}/projects`).then((r) => r.data.data)

export const listGovernedWorkspaces = () =>
  apiClient.get<ApiResponse<WorkspaceSummary[]>>('/governance/workspaces').then((r) => r.data.data)

export const listGovernedWorkspaceProjects = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<GovernedProjectSummary[]>>(`/governance/workspaces/${workspaceId}/projects`)
    .then((r) => r.data.data)

export const listWorkspaceSources = (workspaceId: string) =>
  apiClient.get<ApiResponse<WorkspaceSource[]>>(`/workspaces/${workspaceId}/sources`).then((r) => r.data.data)

export const listProjectSourceBindings = (workspaceId: string, projectId: string) =>
  apiClient
    .get<ApiResponse<SourceBinding[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/source-bindings`,
    )
    .then((r) => r.data.data)

export const createProjectSourceBinding = (
  workspaceId: string,
  projectId: string,
  data: SourceBindingInput,
) =>
  apiClient
    .post<ApiResponse<SourceBinding>>(
      `/workspaces/${workspaceId}/projects/${projectId}/source-bindings`,
      data,
    )
    .then((r) => r.data.data)

export const listProjectSourceBindingRevisions = (
  workspaceId: string,
  projectId: string,
  bindingId: string,
) =>
  apiClient
    .get<ApiResponse<SourceBindingRevision[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/source-bindings/${bindingId}/revisions`,
    )
    .then((r) => r.data.data)

export const deleteWorkspaceProject = (workspaceId: string, projectId: string) =>
  apiClient.delete<ApiResponse<null>>(`/workspaces/${workspaceId}/projects/${projectId}`).then((r) => r.data)

export const bootstrapWorkspaceProject = (
  workspaceId: string,
  data: {
    project: { name: string; slug: string; description?: string; app_type?: ProjectAppType }
    workflow: { name: string; description?: string; graph: WorkflowProject }
  },
) =>
  apiClient
    .post<ApiResponse<ProjectBootstrapResult>>(`/workspaces/${workspaceId}/projects/bootstrap`, data)
    .then((r) => r.data.data)
export const bootstrapGovernedWorkspaceProject = (
  workspaceId: string,
  data: {
    project: { name: string; slug: string; description?: string; app_type?: ProjectAppType }
    workflow: { name: string; description?: string; graph: WorkflowProject }
  },
) =>
  apiClient
    .post<ApiResponse<ProjectBootstrapResult>>(
      `/governance/workspaces/${workspaceId}/projects/bootstrap`,
      data,
    )
    .then((r) => r.data.data)

export const listProjectWorkflows = (workspaceId: string, projectId: string) =>
  apiClient.get<ApiResponse<WorkflowAssetSummary[]>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows`).then((r) => r.data.data)

export const getProjectRuntimeSummary = (workspaceId: string, projectId: string) =>
  apiClient
    .get<ApiResponse<ProjectRuntimeSummary>>(
      `/workspaces/${workspaceId}/projects/${projectId}/runtime-summary`,
    )
    .then((r) => r.data.data)

export const listProjectRuntimeLogs = (
  workspaceId: string,
  projectId: string,
  params: { status?: string; search?: string; page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<import('./types').ProjectRuntimeLog[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/runtime-logs`,
      { params },
    )
    .then((r): ProjectRuntimeLogPage => ({
      logs: r.data.data,
      meta: r.data.meta ?? {
        total: r.data.data.length,
        page: params.page ?? 1,
        limit: params.limit ?? 20,
        pages: 1,
      },
    }))

export const getProjectRuntimeTrace = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
  params?: { afterSequence?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<ProjectRuntimeTrace>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/trace`,
      { params },
    )
    .then((r) => r.data.data)

export const getProjectRecordGraph = (
  workspaceId: string,
  projectId: string,
  params?: { max_nodes?: number },
) =>
  apiClient
    .get<ApiResponse<ProjectRecordGraphPreview>>(
      `/workspaces/${workspaceId}/projects/${projectId}/record-graph`,
      { params },
    )
    .then((r) => r.data.data)

export const createProjectWorkflow = (workspaceId: string, projectId: string, data: { name: string; description?: string; graph: import('@/lib/workflow/schema').WorkflowProject }) =>
  apiClient.post<ApiResponse<WorkflowAssetSummary>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows`, data).then((r) => r.data.data)

export const getProjectWorkflowDraft = (workspaceId: string, projectId: string, workflowId: string) =>
  apiClient.get<ApiResponse<WorkflowDraftRead>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft`).then((r) => r.data.data)

export const updateProjectWorkflowDraft = (workspaceId: string, projectId: string, workflowId: string, graph: import('@/lib/workflow/schema').WorkflowProject, expectedRevision: number) =>
  apiClient.put<ApiResponse<WorkflowDraftRead>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft`, { graph, revision: expectedRevision }).then((r) => r.data.data)

export const validateProjectWorkflowDraft = (workspaceId: string, projectId: string, workflowId: string) =>
  apiClient.post<ApiResponse<import('@/lib/workflow/backend-runs').WorkflowRunProjection>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft/validation-runs`, {}).then((r) => r.data.data)

export const publishProjectWorkflow = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  data: { reason: string; expectedRevision: number; validationRunId: string },
) =>
  apiClient.post<ApiResponse<WorkflowVersionSummary>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/versions`, data).then((r) => r.data.data)

export const listProjectWorkflowVersions = (workspaceId: string, projectId: string, workflowId: string) =>
  apiClient.get<ApiResponse<WorkflowVersionSummary[]>>(`/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/versions`).then((r) => r.data.data)

export const listOperationsInbox = (
  workspaceId: string,
  params?: { type?: string; status?: string; page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<OperationsWorkItem[]>>(`/workspaces/${workspaceId}/operations-inbox`, { params })
    .then((r) => r.data)

export const decideOperationsApproval = (
  workspaceId: string,
  approvalId: string,
  data: { decision: ApprovalDecision; reason: string },
) =>
  apiClient
    .post<ApiResponse<ApprovalDecisionResult>>(
      `/workspaces/${workspaceId}/operations-inbox/${approvalId}/decision`,
      data,
    )
    .then((r) => r.data.data)

export const listOperationsAgents = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<OperationsAgent[]>>(`/workspaces/${workspaceId}/operations-agents`)
    .then((r) => r.data.data)

export const listOperationsAgentTeams = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<OperationsAgentTeam[]>>(`/workspaces/${workspaceId}/operations-agents/teams`)
    .then((r) => r.data.data)

export const createOperationsAgent = (
  workspaceId: string,
  data: { name: string; description?: string | null; owning_team_id?: string | null },
) =>
  apiClient
    .post<ApiResponse<OperationsAgent>>(`/workspaces/${workspaceId}/operations-agents`, data)
    .then((r) => r.data.data)

export const listOperationsAgentActivity = (workspaceId: string) =>
  apiClient.get<ApiResponse<OperationsAgentRun[]>>(`/workspaces/${workspaceId}/operations-agents/activity`).then((r) => r.data.data)

export const getOperationsAgentDraft = (workspaceId: string, agentId: string) =>
  apiClient.get<ApiResponse<OperationsAgentDraft>>(`/workspaces/${workspaceId}/operations-agents/${agentId}/draft`).then((r) => r.data.data)

export const updateOperationsAgentDraft = (
  workspaceId: string,
  agentId: string,
  data: Pick<OperationsAgentDraft, 'revision' | 'instructions' | 'model_configuration' | 'tool_configuration'>,
) =>
  apiClient.put<ApiResponse<OperationsAgentDraft>>(`/workspaces/${workspaceId}/operations-agents/${agentId}/draft`, data).then((r) => r.data.data)

export const listOperationsAgentVersions = (workspaceId: string, agentId: string) =>
  apiClient.get<ApiResponse<PublishedOperationsAgentVersion[]>>(`/workspaces/${workspaceId}/operations-agents/${agentId}/versions`).then((r) => r.data.data)

export const getOperationsAgentVersion = (workspaceId: string, agentId: string, version: number) =>
  apiClient.get<ApiResponse<PublishedOperationsAgentVersion>>(`/workspaces/${workspaceId}/operations-agents/${agentId}/versions/${version}`).then((r) => r.data.data)

export const publishOperationsAgentVersion = (workspaceId: string, agentId: string, reason: string) =>
  apiClient.post<ApiResponse<PublishedOperationsAgentVersion>>(`/workspaces/${workspaceId}/operations-agents/${agentId}/versions`, { reason }).then((r) => r.data.data)

export const startOperationsAgentRun = (
  workspaceId: string,
  agentId: string,
  data: {
    target_resource_type: string
    target_resource_id: string
    input_payload: Record<string, unknown>
    state_payload: Record<string, unknown>
  },
) =>
  apiClient
    .post<ApiResponse<OperationsAgentRun>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/runs`,
      data,
    )
    .then((r) => r.data.data)

export const listAutomations = (workspaceId: string) =>
  apiClient.get<ApiResponse<Automation[]>>(`/workspaces/${workspaceId}/automations`).then((r) => r.data.data)

export const installAutomationStarters = (workspaceId: string) =>
  apiClient
    .post<ApiResponse<{ created_count: number; skipped_count: number }>>(
      `/workspaces/${workspaceId}/automations/starters/install`,
    )
    .then((r) => r.data.data)

export const createAutomation = (
  workspaceId: string,
  data: Omit<Automation, 'id' | 'workspace_id' | 'starter_key' | 'revision' | 'created_by_user_id' | 'created_at' | 'updated_at'>,
) =>
  apiClient.post<ApiResponse<Automation>>(`/workspaces/${workspaceId}/automations`, data).then((r) => r.data.data)

export const patchAutomation = (
  workspaceId: string,
  automationId: string,
  data: Partial<Omit<Automation, 'id' | 'workspace_id' | 'revision' | 'created_by_user_id' | 'created_at' | 'updated_at'>>,
) =>
  apiClient.patch<ApiResponse<Automation>>(`/workspaces/${workspaceId}/automations/${automationId}`, data).then((r) => r.data.data)

export const startAutomationRun = (workspaceId: string, automationId: string) =>
  apiClient
    .post<ApiResponse<OperationsAgentRun>>(
      `/workspaces/${workspaceId}/automations/${automationId}/runs`,
    )
    .then((r) => r.data.data)

export const patchOperationsAgent = (workspaceId: string, agentId: string, disabled: boolean) =>
  apiClient
    .patch<ApiResponse<OperationsAgent>>(`/workspaces/${workspaceId}/operations-agents/${agentId}`, { disabled })
    .then((r) => r.data.data)

export const assignOperationsAgentProfile = (
  workspaceId: string,
  agentId: string,
  data: {
    mode: OperationsAgentMode
    tool_scope: string[]
    resource_scope: string[]
    action_scope: string[]
    reason: string
  },
) =>
  apiClient
    .post<ApiResponse<OperationsAgentProfile>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/profiles`,
      data,
    )
    .then((r) => r.data.data)

// ── Dashboard ──────────────────────────────────────────────────────────────────
export const getDashboardStats = (params?: {
  range?: string;
  start?: string;
  end?: string;
}) =>
  apiClient
    .get<ApiResponse<DashboardStats>>("/dashboard/stats", { params })
    .then((r) => r.data.data);

export const getDashboardActivity = (params?: {
  days?: number;
  tz_offset?: number;
}) =>
  apiClient
    .get<ApiResponse<DashboardActivity>>("/dashboard/activity", { params })
    .then((r) => r.data.data);

export const getOpinionMonitor = (params?: {
  range?: string;
  start?: string;
  end?: string;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<OpinionMonitor>>("/dashboard/opinion-monitor", { params })
    .then((r) => r.data.data);

// ── Sources ────────────────────────────────────────────────────────────────────
export const listSources = (params?: {
  page?: number;
  limit?: number;
  enabled?: boolean;
}) =>
  apiClient
    .get<ApiResponse<DataSource[]>>("/sources", { params })
    .then((r) => r.data);

export const getSource = (id: string) =>
  apiClient
    .get<ApiResponse<DataSource>>(`/sources/${id}`)
    .then((r) => r.data.data);

export const createSource = (data: Partial<DataSource>) =>
  apiClient
    .post<ApiResponse<DataSource>>("/sources", data)
    .then((r) => r.data.data);

export const updateSource = (id: string, data: Partial<DataSource>) =>
  apiClient
    .patch<ApiResponse<DataSource>>(`/sources/${id}`, data)
    .then((r) => r.data.data);

export const deleteSource = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/sources/${id}`).then((r) => r.data);

export const testSourceConnectivity = (id: string) =>
  apiClient
    .post<ApiResponse<{ connected: boolean; errors: string[] }>>(
      `/sources/${id}/test`,
    )
    .then((r) => r.data.data);

export const importRssCatalog = (url: string) =>
  apiClient
    .post<ApiResponse<RssCatalogImportResult>>("/sources/import-opml-url", {
      url,
    })
    .then((r) => r.data.data);

// Encrypted credential store (backend.auth.AuthManager) — key names only ever
// come back; the secret is write-only once stored.
export const listSourceCredentials = (id: string) =>
  apiClient
    .get<ApiResponse<{ key_name: string }[]>>(`/sources/${id}/credentials`)
    .then((r) => r.data.data);

export const storeSourceCredential = (
  id: string,
  data: { key_name: string; secret: string },
) =>
  apiClient
    .post<ApiResponse<null>>(`/sources/${id}/credentials`, data)
    .then((r) => r.data);

export const deleteSourceCredential = (id: string, keyName: string) =>
  apiClient
    .delete<ApiResponse<null>>(`/sources/${id}/credentials/${keyName}`)
    .then((r) => r.data);

// Read-only sensor-honesty view (C0 Control Room v0) — poll this, don't infer
// health from anything else. See SourceControlState: an incomplete-sensor
// source reports confidence "low" / control_state "unknown", never a fake
// "healthy". No websocket in v0 — TanStack Query refetchInterval only.
export const getSourceControlState = (id: string) =>
  apiClient
    .get<ApiResponse<SourceControlState>>(`/sources/${id}/control-state`)
    .then((r) => r.data.data);

// Set, update, or clear (objective_override: null) a source's per-source
// SourceObjective override. The resolved objective (override merged over
// defaults) is what control-state actually classifies against.
export const setSourceObjective = (
  id: string,
  objectiveOverride: Record<string, unknown> | null,
) =>
  apiClient
    .patch<ApiResponse<DataSource>>(`/sources/${id}/objective`, {
      objective_override: objectiveOverride,
    })
    .then((r) => r.data.data);

// Raw per-run sensor history behind control-state's folded latest-measurement
// + trend summary — the Source Control Room's trend chart data. Paginated,
// newest-first, like every other list endpoint. Zero rows is a legitimate
// "pre-measurement source" state, not an error.
export const listSourceMeasurements = (
  id: string,
  params?: { page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<SourceMeasurementRecord[]>>(
      `/sources/${id}/measurements`,
      { params },
    )
    .then((r) => r.data);

// ── Control (issue 07 — topology ODP node + action history) ────────────────────
// System-level ODP data-plane snapshot: no source_id, singleton. Same
// "never fake healthy" contract as control-state — each section degrades to
// `available: false` independently rather than the whole endpoint erroring.
export const getOdpState = () =>
  apiClient
    .get<ApiResponse<OdpSystemState>>("/control/odp-state")
    .then((r) => r.data.data);

// Row-level Evidence Ledger listing (control_actions) — the operator's audit
// surface over every suggestion/execution the controller has ever produced.
// Read-only; paginated like every other list endpoint.
export const listControlActions = (params?: {
  source_id?: string;
  mode?: string;
  outcome?: string;
  page?: number;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<ControlActionRecord[]>>("/control/actions", { params })
    .then((r) => r.data);

// Global actuator kill switch (issue 03) — the Control Cycle checks this
// before ever executing anything in "automatic" mode. GET is a snapshot;
// POST sets the in-memory runtime override (resets to config on restart).
export const getKillSwitch = () =>
  apiClient
    .get<ApiResponse<KillSwitchState>>("/control/kill-switch")
    .then((r) => r.data.data);

export const setKillSwitch = (engaged: boolean) =>
  apiClient
    .post<ApiResponse<KillSwitchState>>("/control/kill-switch", { engaged })
    .then((r) => r.data.data);

// Agreement/recovery report over the control_actions evidence ledger
// (PR-Control-3.5) — the gate data for ever flipping CONTROL_MODE to
// "automatic" per state class. Runs a lazy outcome-evaluation pass before
// aggregating, so no separate "evaluate" button is needed in the UI.
export const getAdvisoryReport = () =>
  apiClient
    .get<ApiResponse<AdvisoryReport>>("/control/advisory-report")
    .then((r) => r.data.data);

// ── Tasks ──────────────────────────────────────────────────────────────────────
export const listTasks = (params?: {
  source_id?: string;
  status?: string;
  page?: number;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<CollectionTask[]>>("/tasks", { params })
    .then((r) => r.data);

export const getTask = (id: string) =>
  apiClient
    .get<ApiResponse<CollectionTask>>(`/tasks/${id}`)
    .then((r) => r.data.data);

export const recoverTask = (
  id: string,
  data: {
    idempotency_key: string;
    reason: string;
    mode?: "recollect";
    initiating_actor?: string;
  },
) =>
  apiClient
    .post<ApiResponse<{ task_id: string; retry_of_task_id: string; status: string; recovery_mode: string; idempotency_replayed: boolean }>>(
      `/tasks/${id}/recover`,
      data,
    )
    .then((r) => r.data.data);

export const listTaskRuns = (task_id: string) =>
  apiClient
    .get<ApiResponse<TaskRun[]>>(`/tasks/${task_id}/runs`)
    .then((r) => r.data);

export const listRunEvents = (task_id: string, run_id: string) =>
  apiClient
    .get<ApiResponse<TaskRunEvent[]>>(`/tasks/${task_id}/runs/${run_id}/events`)
    .then((r) => r.data.data);

// ── Records ────────────────────────────────────────────────────────────────────
export const listRecords = (params?: {
  source_id?: string;
  task_id?: string;
  project_id?: string;
  workflow_id?: string;
  workflow_run_id?: string;
  status?: string;
  search?: string;
  page?: number;
  limit?: number;
  sort_by?:
    | "created_at"
    | "updated_at"
    | "status"
    | "source_id"
    | "workflow_id"
    | "workflow_run_id";
  sort_order?: "asc" | "desc";
}) =>
  apiClient
    .get<ApiResponse<CollectedRecord[]>>("/records", { params })
    .then((r) => r.data);

export const getRecord = (id: string) =>
  apiClient
    .get<ApiResponse<CollectedRecord>>(`/records/${id}`)
    .then((r) => r.data.data);

export const deleteRecord = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/records/${id}`).then((r) => r.data);

export const batchDeleteRecords = (ids: string[]) =>
  apiClient
    .post<ApiResponse<{ deleted: number }>>("/records/batch-delete", { ids })
    .then((r) => r.data);

export const clearAllRecords = (source_id?: string) =>
  apiClient
    .delete<ApiResponse<{ deleted: number }>>("/records", {
      params: source_id ? { source_id } : {},
    })
    .then((r) => r.data);

// ── Skills (record→distill→execute→correct loop, ADR-0003) ─────────────────────
export const listSkills = (params?: {
  domain?: string;
  enabled?: boolean;
  page?: number;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<Skill[]>>("/skills", { params })
    .then((r) => r.data);

export const getSkill = (id: string) =>
  apiClient.get<ApiResponse<Skill>>(`/skills/${id}`).then((r) => r.data.data);

// trace omitted → backend falls back to skill.last_failing_trace.
export const redistillSkill = (id: string, trace?: Record<string, unknown>) =>
  apiClient
    .post<
      ApiResponse<{
        skill_id: string;
        version: number;
        domain: string;
        capability: string;
      }>
    >(`/skills/${id}/redistill`, trace ? { trace } : {})
    .then((r) => r.data.data);

export const dismissCorrection = (id: string) =>
  apiClient.post<ApiResponse<SkillBrief>>(`/skills/${id}/dismiss-correction`).then((r) => r.data.data)

export const rollbackSkill = (id: string) =>
  apiClient
    .post<ApiResponse<Skill>>(`/skills/${id}/rollback`)
    .then((r) => r.data.data);

// ── Record leg (2026-07-01 addendum): capture a demo → journey_trace_v1 ─────────
export const recordStart = (data: {
  domain: string;
  capability: string;
  cdp_endpoint?: string;
}) =>
  apiClient
    .post<ApiResponse<{ session_id: string; cdp_endpoint: string }>>(
      "/skills/record/start",
      data,
    )
    .then((r) => r.data.data);

export const recordStop = (
  sessionId: string,
  data: { status: string; note?: string },
) =>
  apiClient
    .post<ApiResponse<{ trace: Record<string, unknown> }>>(
      `/skills/record/${sessionId}/stop`,
      data,
    )
    .then((r) => r.data.data.trace);

export const distillSkill = (data: {
  trace: Record<string, unknown>;
  domain?: string;
  capability?: string;
}) =>
  apiClient
    .post<
      ApiResponse<{
        id: string;
        domain: string;
        capability: string;
        name: string;
        version: number;
        status: string;
      }>
    >("/skills/distill", data)
    .then((r) => r.data.data);

// ── Schedules ──────────────────────────────────────────────────────────────────
export const listSchedules = (params?: {
  source_id?: string;
  enabled?: boolean;
}) =>
  apiClient
    .get<ApiResponse<CronSchedule[]>>("/schedules", { params })
    .then((r) => r.data);

export const createSchedule = (data: Partial<CronSchedule>) =>
  apiClient
    .post<ApiResponse<CronSchedule>>("/schedules", data)
    .then((r) => r.data.data);

export const updateSchedule = (id: string, data: Partial<CronSchedule>) =>
  apiClient
    .patch<ApiResponse<CronSchedule>>(`/schedules/${id}`, data)
    .then((r) => r.data.data);

export const deleteSchedule = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/schedules/${id}`).then((r) => r.data);

// ── Notifications ──────────────────────────────────────────────────────────────
export const listNotificationRules = (params?: { page?: number; limit?: number }) =>
  apiClient
    .get<ApiResponse<NotificationRule[]>>('/notifications/rules', { params })
    .then((r) => r.data)

export const getNotificationRule = (id: string) =>
  apiClient
    .get<ApiResponse<NotificationRule>>(`/notifications/rules/${id}`)
    .then((r) => r.data.data)

export const createNotificationRule = (data: NotificationRuleInput) =>
  apiClient
    .post<ApiResponse<NotificationRule>>("/notifications/rules", data)
    .then((r) => r.data.data);

export const updateNotificationRule = (
  id: string,
  data: NotificationRuleInput,
) =>
  apiClient
    .patch<ApiResponse<NotificationRule>>(`/notifications/rules/${id}`, data)
    .then((r) => r.data.data);

export const deleteNotificationRule = (id: string) =>
  apiClient
    .delete<ApiResponse<null>>(`/notifications/rules/${id}`)
    .then((r) => r.data);

export const listNotificationLogs = (params?: {
  rule_id?: string;
  page?: number;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<NotificationLog[]>>("/notifications/logs", { params })
    .then((r) => r.data);

// ── Model Providers ────────────────────────────────────────────────────────────
export const listProviders = () =>
  apiClient.get<ApiResponse<ModelProvider[]>>("/providers").then((r) => r.data);

export const createProvider = (data: ModelProviderInput) =>
  apiClient
    .post<ApiResponse<ModelProvider>>("/providers", data)
    .then((r) => r.data.data);

export const updateProvider = (id: string, data: ModelProviderInput) =>
  apiClient
    .patch<ApiResponse<ModelProvider>>(`/providers/${id}`, data)
    .then((r) => r.data.data);

export const deleteProvider = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/providers/${id}`).then((r) => r.data);

export const discoverProviderModels = (data: ProviderModelDiscoveryInput) =>
  apiClient
    .post<ApiResponse<string[]>>("/providers/discover-models", data)
    .then((r) => r.data.data);

// Probes the provider's live endpoint (backend/llm adapter). NEVER raises for
// an ordinary connection failure — the failure is `ok: false` in a normal 200
// response; it can still 404 if the provider id doesn't exist.
export const testProvider = (id: string) =>
  apiClient
    .post<ApiResponse<ConnectionTestResult>>(`/providers/${id}/test`)
    .then((r) => r.data.data);

// Discovers models via the adapter and idempotently upserts the provider's
// catalog — 'source=manual' rows are never touched. Can 502 if discovery
// itself fails (LlmAdapterError), surfaced via the axios interceptor's
// normalized Error.message.
export const syncProviderModels = (id: string) =>
  apiClient
    .post<ApiResponse<ProviderModelSyncResult>>(`/providers/${id}/models/sync`)
    .then((r) => r.data.data);

export const listProviderModels = (id: string) =>
  apiClient
    .get<ApiResponse<ProviderModelRead[]>>(`/providers/${id}/models`)
    .then((r) => r.data);

// Manual catalog add — `source` is forced server-side to 'manual', never send it.
export const addProviderModel = (
  providerId: string,
  data: {
    model_id: string;
    model_type?: string;
    capabilities?: Record<string, unknown> | null;
    enabled?: boolean;
  },
) =>
  apiClient
    .post<ApiResponse<ProviderModelRead>>(
      `/providers/${providerId}/models`,
      data,
    )
    .then((r) => r.data.data);

export const updateProviderModel = (
  providerId: string,
  modelRowId: string,
  data: {
    model_type?: string;
    capabilities?: Record<string, unknown> | null;
    enabled?: boolean;
  },
) =>
  apiClient
    .patch<ApiResponse<ProviderModelRead>>(
      `/providers/${providerId}/models/${modelRowId}`,
      data,
    )
    .then((r) => r.data.data);

export const deleteProviderModel = (providerId: string, modelRowId: string) =>
  apiClient
    .delete<ApiResponse<null>>(`/providers/${providerId}/models/${modelRowId}`)
    .then((r) => r.data);

// ── RSS generator Providers ───────────────────────────────────────────────────
export const listFeedProviders = () =>
  apiClient
    .get<ApiResponse<FeedProvider[]>>("/providers/feed-generators")
    .then((r) => r.data);

export const createFeedProvider = (data: FeedProviderInput) =>
  apiClient
    .post<ApiResponse<FeedProvider>>("/providers/feed-generators", data)
    .then((r) => r.data.data);

export const updateFeedProvider = (id: string, data: FeedProviderInput) =>
  apiClient
    .patch<ApiResponse<FeedProvider>>(`/providers/feed-generators/${id}`, data)
    .then((r) => r.data.data);

export const deleteFeedProvider = (id: string) =>
  apiClient
    .delete<ApiResponse<null>>(`/providers/feed-generators/${id}`)
    .then((r) => r.data);

export const testFeedProvider = (id: string) =>
  apiClient
    .post<ApiResponse<FeedProviderConnectionTest>>(
      `/providers/feed-generators/${id}/test`,
    )
    .then((r) => r.data.data);

export const getFeedProviderCatalog = (id: string) =>
  apiClient
    .get<ApiResponse<Record<string, unknown>>>(
      `/providers/feed-generators/${id}/catalog`,
    )
    .then((r) => r.data.data);

export const buildFeedProviderWorkflowNode = (
  id: string,
  data: FeedProviderWorkflowNodeInput,
) =>
  apiClient
    .post<ApiResponse<FeedProviderWorkflowNode>>(
      `/providers/feed-generators/${id}/workflow-node`,
      data,
    )
    .then((r) => r.data.data);

// ── Reusable delivery connections ───────────────────────────────────────────
export const listDeliveryConnections = () =>
  apiClient.get<ApiResponse<DeliveryConnection[]>>('/delivery-connections').then((r) => r.data)

export const createDeliveryConnection = (data: DeliveryConnectionInput) =>
  apiClient
    .post<ApiResponse<DeliveryConnection>>('/delivery-connections', data)
    .then((r) => r.data.data)

export const updateDeliveryConnection = (id: string, data: DeliveryConnectionInput) =>
  apiClient
    .patch<ApiResponse<DeliveryConnection>>(`/delivery-connections/${id}`, data)
    .then((r) => r.data.data)

export const deleteDeliveryConnection = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/delivery-connections/${id}`).then((r) => r.data)

export const probeFeishuBitable = (id: string, data: FeishuBitableProbeInput) =>
  apiClient
    .post<ApiResponse<FeishuBitableProbeResult>>(`/delivery-connections/${id}/probe`, data)
    .then((r) => r.data.data)

// ── Model defaults (model-provider runtime — role-based failover candidate lists) ───────────────
// A role can be entirely absent from the list on a fresh install — that's a
// legitimate "no candidates configured yet" state, not an error.
export const listModelDefaults = () =>
  apiClient
    .get<ApiResponse<ModelDefaultRead[]>>("/model-defaults")
    .then((r) => r.data);

// role goes in the URL path, not the body. Can 400 if a candidate names a
// nonexistent provider or a model not in that provider's catalog.
export const putModelDefault = (
  role: ModelRole,
  candidates: ModelDefaultCandidate[],
) =>
  apiClient
    .put<ApiResponse<ModelDefaultRead>>(`/model-defaults/${role}`, {
      candidates,
    })
    .then((r) => r.data.data);

// ── Agents ─────────────────────────────────────────────────────────────────────
export const listAgents = (params?: { enabled?: boolean }) =>
  apiClient
    .get<ApiResponse<AIAgent[]>>("/agents", { params })
    .then((r) => r.data);

export const createAgent = (data: Partial<AIAgent>) =>
  apiClient
    .post<ApiResponse<AIAgent>>("/agents", data)
    .then((r) => r.data.data);

export const updateAgent = (id: string, data: Partial<AIAgent>) =>
  apiClient
    .patch<ApiResponse<AIAgent>>(`/agents/${id}`, data)
    .then((r) => r.data.data);

export const deleteAgent = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/agents/${id}`).then((r) => r.data);

// ── System ─────────────────────────────────────────────────────────────────────
// Liveness plus an opaque per-process identifier used to verify API restarts.
// For deployment detail (task_executor, ...) use getSystemConfig.
export const getHealth = (
  timeout = API_HEALTH_REQUEST_TIMEOUT_MS,
  signal?: AbortSignal,
) =>
  rootClient
    .get<ApiHealthSnapshot>("/health", { timeout, signal })
    .then((r) => r.data);

export const getSystemConfig = () =>
  apiClient
    .get<ApiResponse<SystemConfig>>("/system/config")
    .then((r) => r.data.data);

export const updateSystemConfig = (data: SystemConfigPatch) =>
  apiClient
    .patch<ApiResponse<SystemConfig>>("/system/config", data)
    .then((r) => r.data.data);

export const getWsAgentStatus = () =>
  apiClient
    .get<ApiResponse<{ connected: string[] }>>("/browsers/agents/ws-status")
    .then((r) => r.data.data);

// ── Workers ────────────────────────────────────────────────────────────────────
export const listWorkers = () =>
  apiClient.get<ApiResponse<WorkerNode[]>>("/workers").then((r) => r.data);

export const getCeleryStats = () =>
  apiClient
    .get<ApiResponse<Record<string, unknown>>>("/workers/celery-stats")
    .then((r) => r.data.data);

// ── Edge Nodes ─────────────────────────────────────────────────────────────────
export const listNodes = () =>
  apiClient.get<ApiResponse<EdgeNode[]>>("/nodes").then((r) => r.data);

export const getNodeEvents = (id: string) =>
  apiClient
    .get<ApiResponse<EdgeNodeEvent[]>>(`/nodes/${id}/events`)
    .then((r) => r.data);

export const getNodeStats = (
  id: string,
  params?: { range?: string; start?: string; end?: string },
) =>
  apiClient
    .get<ApiResponse<NodeStats>>(`/nodes/${id}/stats`, { params })
    .then((r) => r.data.data);

export const deleteNode = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/nodes/${id}`).then((r) => r.data);

export const getInstallScriptUrl = (base: string) =>
  `${base}/api/v1/nodes/install/agent.sh`;

export const getChromePool = () =>
  apiClient
    .get<
      ApiResponse<{
        endpoints: ChromeEndpoint[];
        total: number;
        available: number;
      }>
    >("/workers/chrome-pool")
    .then((r) => r.data.data);

export const updateChromeEndpointMode = (
  endpoint: string,
  mode: "bridge" | "cdp",
) => {
  const b64 = btoa(endpoint)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
  return apiClient
    .patch<ApiResponse<{ endpoint: string; mode: string }>>(
      `/workers/chrome-pool/${b64}/mode`,
      { mode },
    )
    .then((r) => r.data.data);
};

// ── Presets (Plan IR issue 06) ──────────────────────────────────────────────────
// Read-only, grouped by channel_type — palette (issue 07) source of truth.
export const listPresets = () =>
  apiClient
    .get<ApiResponse<PresetsGrouped>>("/presets")
    .then((r) => r.data.data);

// ── BrowserAct packs (Browser Act integration PR-E, decision #9) ─────────────────────────────────
// Read-only vendored-pack catalog for the 'browser_act' channel's one-click
// config preset (pack picker) — never carries a credential/api_key.
export const listBrowserActPacks = () =>
  apiClient
    .get<ApiResponse<BrowserActPack[]>>("/browser-act/packs")
    .then((r) => r.data.data);

// ── Plans (Plan IR issue 02) — Collection Canvas persistence ────────────────────
// Save (create/update) validates server-side and 422s with a node-anchored
// error list (backend.plan_ir.validation.PlanValidationError.to_dict()) on an
// invalid graph. client.ts's normalizeApiError attaches that raw array onto
// the thrown Error as `.detail` (see PlanSaveError in planCanvasModel.ts for
// the typed accessor) — callers that only want the message keep working
// unchanged; callers that need node-anchored errors read `.detail`.
export const listPlans = (params?: {
  draft?: boolean;
  page?: number;
  limit?: number;
}) =>
  apiClient
    .get<ApiResponse<PlanRead[]>>("/plans", { params })
    .then((r) => r.data);

export const getPlan = (id: string) =>
  apiClient.get<ApiResponse<PlanRead>>(`/plans/${id}`).then((r) => r.data.data);

export const createPlan = (data: { name: string; graph: PlanGraph }) =>
  apiClient
    .post<ApiResponse<PlanRead>>("/plans", data)
    .then((r) => r.data.data);

export const updatePlan = (
  id: string,
  data: { name?: string; graph?: PlanGraph },
) =>
  apiClient
    .patch<ApiResponse<PlanRead>>(`/plans/${id}`, data)
    .then((r) => r.data.data);

export const deletePlan = (id: string) =>
  apiClient.delete<ApiResponse<null>>(`/plans/${id}`).then((r) => r.data);

// ── Plan run + health (Plan IR issue 03/04/08) — Collection Canvas observe lens ──
// runPlan invokes the SYNCHRONOUS manual whole-plan run endpoint
// (backend/api/v1/plans.py run_plan) — the response already reflects the
// completed run (or its per-source/shared-segment failure detail), no
// polling needed. getPlanHealth is read-only Plan Health for the observe
// lens's shared-node badges (issue 04's per-node health dimension).
export const runPlan = (id: string, parameters: Record<string, unknown> = {}) =>
  apiClient
    .post<ApiResponse<PlanRunRead>>(`/plans/${id}/run`, parameters)
    .then((r) => r.data.data);

export const getPlanHealth = (
  id: string,
  params?: { run_key?: string; page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<PlanHealthRead[]>>(`/plans/${id}/health`, { params })
    .then((r) => r.data);
