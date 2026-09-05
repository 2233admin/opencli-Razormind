import { apiClient } from "./client";
import type { AuthIdentity } from "@/lib/auth/types";
import type {
  ApiResponse,
  ApprovalDecision,
  ApprovalDecisionResult,
  Automation,
  GovernedProjectSummary,
  OperationsAgent,
  OperationsAgentDraft,
  OperationsAgentMode,
  OperationsAgentProfile,
  OperationsAgentRun,
  OperationsWorkItem,
  ProjectAppType,
  ProjectBootstrapResult,
  ProjectRecordGraphPreview,
  RunAnalysisSnapshotCapability,
  RunAnalysisSnapshotPreview,
  RunAnalysisSnapshotRangeRequest,
  RunAnalysisSnapshotReceipt,
  RunAnalysisSnapshotSummary,
  ProjectRuntimeLogPage,
  ProjectRuntimeSummary,
  ProjectRuntimeTrace,
  ProjectSummary,
  PublishedOperationsAgentVersion,
  SourceBinding,
  SourceBindingInput,
  SourceBindingRevision,
  WorkflowAssetSummary,
  WorkflowDraftRead,
  WorkflowVersionSummary,
  WorkspaceSettingsRead,
  WorkspaceSettingsValues,
  WorkspaceSource,
  WorkspaceSummary,
} from "./types";

export const getWorkspaceSettings = () =>
  apiClient
    .get<ApiResponse<WorkspaceSettingsRead>>("/settings")
    .then((r) => r.data.data);

export const updateWorkspaceSettings = (
  data: Partial<WorkspaceSettingsValues>,
) =>
  apiClient
    .patch<ApiResponse<WorkspaceSettingsRead>>("/settings", data)
    .then((r) => r.data.data);

export const resetWorkspaceSettings = () =>
  apiClient
    .delete<ApiResponse<WorkspaceSettingsRead>>("/settings")
    .then((r) => r.data.data);

export const getCurrentIdentity = () =>
  apiClient.get<ApiResponse<AuthIdentity>>("/auth/me").then((r) => r.data.data);

export const listMyWorkspaces = () =>
  apiClient
    .get<ApiResponse<WorkspaceSummary[]>>("/workspaces")
    .then((r) => r.data.data);

export const listWorkspaceProjects = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<ProjectSummary[]>>(`/workspaces/${workspaceId}/projects`)
    .then((r) => r.data.data);

export const listGovernedWorkspaces = () =>
  apiClient
    .get<ApiResponse<WorkspaceSummary[]>>("/governance/workspaces")
    .then((r) => r.data.data);

export const listGovernedWorkspaceProjects = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<GovernedProjectSummary[]>>(
      `/governance/workspaces/${workspaceId}/projects`,
    )
    .then((r) => r.data.data);

export const listWorkspaceSources = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<WorkspaceSource[]>>(`/workspaces/${workspaceId}/sources`)
    .then((r) => r.data.data);

export const listProjectSourceBindings = (
  workspaceId: string,
  projectId: string,
) =>
  apiClient
    .get<ApiResponse<SourceBinding[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/source-bindings`,
    )
    .then((r) => r.data.data);

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
    .then((r) => r.data.data);

export const listProjectSourceBindingRevisions = (
  workspaceId: string,
  projectId: string,
  bindingId: string,
) =>
  apiClient
    .get<ApiResponse<SourceBindingRevision[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/source-bindings/${bindingId}/revisions`,
    )
    .then((r) => r.data.data);

export const deleteWorkspaceProject = (
  workspaceId: string,
  projectId: string,
) =>
  apiClient
    .delete<ApiResponse<null>>(
      `/workspaces/${workspaceId}/projects/${projectId}`,
    )
    .then((r) => r.data);

export const bootstrapWorkspaceProject = (
  workspaceId: string,
  data: {
    project: {
      name: string;
      slug: string;
      description?: string;
      app_type?: ProjectAppType;
    };
    workflow: {
      name: string;
      description?: string;
      graph: import("@/lib/workflow/schema").WorkflowProject;
    };
  },
) =>
  apiClient
    .post<ApiResponse<ProjectBootstrapResult>>(
      `/workspaces/${workspaceId}/projects/bootstrap`,
      data,
    )
    .then((r) => r.data.data);

export const listProjectWorkflows = (workspaceId: string, projectId: string) =>
  apiClient
    .get<ApiResponse<WorkflowAssetSummary[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows`,
    )
    .then((r) => r.data.data);

export const getProjectRuntimeSummary = (
  workspaceId: string,
  projectId: string,
) =>
  apiClient
    .get<ApiResponse<ProjectRuntimeSummary>>(
      `/workspaces/${workspaceId}/projects/${projectId}/runtime-summary`,
    )
    .then((r) => r.data.data);

export const listProjectRuntimeLogs = (
  workspaceId: string,
  projectId: string,
  params: { status?: string; search?: string; page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<import("./types").ProjectRuntimeLog[]>>(
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
    }));

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
    .then((r) => r.data.data);

export const getRunAnalysisSnapshotCapability = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
) =>
  apiClient
    .get<ApiResponse<RunAnalysisSnapshotCapability>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots/capability`,
    )
    .then((r) => r.data.data);

export const previewRunAnalysisSnapshot = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
  data: RunAnalysisSnapshotRangeRequest,
) =>
  apiClient
    .post<ApiResponse<RunAnalysisSnapshotPreview>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots/preview`,
      data,
    )
    .then((r) => r.data.data);

export const createRunAnalysisSnapshot = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
  data: RunAnalysisSnapshotRangeRequest,
) =>
  apiClient
    .post<ApiResponse<RunAnalysisSnapshotReceipt>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots`,
      data,
    )
    .then((r) => r.data.data);

export const listRunAnalysisSnapshots = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
) =>
  apiClient
    .get<ApiResponse<RunAnalysisSnapshotReceipt[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots`,
    )
    .then((r) => r.data.data);

export const getRunAnalysisSnapshot = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
  snapshotId: string,
) =>
  apiClient
    .get<ApiResponse<RunAnalysisSnapshotReceipt>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots/${snapshotId}`,
    )
    .then((r) => r.data.data);

export const getRunAnalysisSnapshotSummary = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  runId: string,
  snapshotId: string,
) =>
  apiClient
    .get<ApiResponse<RunAnalysisSnapshotSummary>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/runs/${runId}/analysis-snapshots/${snapshotId}/summary`,
    )
    .then((r) => r.data.data);

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
    .then((r) => r.data.data);

export const createProjectWorkflow = (
  workspaceId: string,
  projectId: string,
  data: {
    name: string;
    description?: string;
    graph: import("@/lib/workflow/schema").WorkflowProject;
  },
) =>
  apiClient
    .post<ApiResponse<WorkflowAssetSummary>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows`,
      data,
    )
    .then((r) => r.data.data);

export const getProjectWorkflowDraft = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
) =>
  apiClient
    .get<ApiResponse<WorkflowDraftRead>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft`,
    )
    .then((r) => r.data.data);

export const updateProjectWorkflowDraft = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  graph: import("@/lib/workflow/schema").WorkflowProject,
  expectedRevision: number,
) =>
  apiClient
    .put<ApiResponse<WorkflowDraftRead>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft`,
      { graph, revision: expectedRevision },
    )
    .then((r) => r.data.data);

export const validateProjectWorkflowDraft = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
) =>
  apiClient
    .post<
      ApiResponse<import("@/lib/workflow/backend-runs").WorkflowRunProjection>
    >(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/draft/validation-runs`,
      {},
    )
    .then((r) => r.data.data);

export const publishProjectWorkflow = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
  data: { reason: string; expectedRevision: number; validationRunId: string },
) =>
  apiClient
    .post<ApiResponse<WorkflowVersionSummary>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/versions`,
      data,
    )
    .then((r) => r.data.data);

export const listProjectWorkflowVersions = (
  workspaceId: string,
  projectId: string,
  workflowId: string,
) =>
  apiClient
    .get<ApiResponse<WorkflowVersionSummary[]>>(
      `/workspaces/${workspaceId}/projects/${projectId}/workflows/${workflowId}/versions`,
    )
    .then((r) => r.data.data);

export const listOperationsInbox = (
  workspaceId: string,
  params?: { type?: string; status?: string; page?: number; limit?: number },
) =>
  apiClient
    .get<ApiResponse<OperationsWorkItem[]>>(
      `/workspaces/${workspaceId}/operations-inbox`,
      { params },
    )
    .then((r) => r.data);

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
    .then((r) => r.data.data);

export const listOperationsAgents = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<OperationsAgent[]>>(
      `/workspaces/${workspaceId}/operations-agents`,
    )
    .then((r) => r.data.data);

export const listOperationsAgentActivity = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<OperationsAgentRun[]>>(
      `/workspaces/${workspaceId}/operations-agents/activity`,
    )
    .then((r) => r.data.data);

export const getOperationsAgentDraft = (workspaceId: string, agentId: string) =>
  apiClient
    .get<ApiResponse<OperationsAgentDraft>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/draft`,
    )
    .then((r) => r.data.data);

export const updateOperationsAgentDraft = (
  workspaceId: string,
  agentId: string,
  data: Pick<
    OperationsAgentDraft,
    "revision" | "instructions" | "model_configuration" | "tool_configuration"
  >,
) =>
  apiClient
    .put<ApiResponse<OperationsAgentDraft>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/draft`,
      data,
    )
    .then((r) => r.data.data);

export const listOperationsAgentVersions = (
  workspaceId: string,
  agentId: string,
) =>
  apiClient
    .get<ApiResponse<PublishedOperationsAgentVersion[]>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/versions`,
    )
    .then((r) => r.data.data);

export const getOperationsAgentVersion = (
  workspaceId: string,
  agentId: string,
  version: number,
) =>
  apiClient
    .get<ApiResponse<PublishedOperationsAgentVersion>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/versions/${version}`,
    )
    .then((r) => r.data.data);

export const publishOperationsAgentVersion = (
  workspaceId: string,
  agentId: string,
  reason: string,
) =>
  apiClient
    .post<ApiResponse<PublishedOperationsAgentVersion>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/versions`,
      { reason },
    )
    .then((r) => r.data.data);

export const startOperationsAgentRun = (
  workspaceId: string,
  agentId: string,
  data: {
    target_resource_type: string;
    target_resource_id: string;
    input_payload: Record<string, unknown>;
    state_payload: Record<string, unknown>;
  },
) =>
  apiClient
    .post<ApiResponse<OperationsAgentRun>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/runs`,
      data,
    )
    .then((r) => r.data.data);

export const listAutomations = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<Automation[]>>(`/workspaces/${workspaceId}/automations`)
    .then((r) => r.data.data);

export const installAutomationStarters = (workspaceId: string) =>
  apiClient
    .post<ApiResponse<{ created_count: number; skipped_count: number }>>(
      `/workspaces/${workspaceId}/automations/starters/install`,
    )
    .then((r) => r.data.data);

export const createAutomation = (
  workspaceId: string,
  data: Omit<
    Automation,
    | "id"
    | "workspace_id"
    | "starter_key"
    | "revision"
    | "created_by_user_id"
    | "created_at"
    | "updated_at"
  >,
) =>
  apiClient
    .post<ApiResponse<Automation>>(
      `/workspaces/${workspaceId}/automations`,
      data,
    )
    .then((r) => r.data.data);

export const patchAutomation = (
  workspaceId: string,
  automationId: string,
  data: Partial<Automation>,
) =>
  apiClient
    .patch<ApiResponse<Automation>>(
      `/workspaces/${workspaceId}/automations/${automationId}`,
      data,
    )
    .then((r) => r.data.data);

export const patchOperationsAgent = (
  workspaceId: string,
  agentId: string,
  disabled: boolean,
) =>
  apiClient
    .patch<ApiResponse<OperationsAgent>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}`,
      { disabled },
    )
    .then((r) => r.data.data);

export const assignOperationsAgentProfile = (
  workspaceId: string,
  agentId: string,
  data: {
    mode: OperationsAgentMode;
    tool_scope: string[];
    resource_scope: string[];
    action_scope: string[];
    reason: string;
  },
) =>
  apiClient
    .post<ApiResponse<OperationsAgentProfile>>(
      `/workspaces/${workspaceId}/operations-agents/${agentId}/profiles`,
      data,
    )
    .then((r) => r.data.data);
