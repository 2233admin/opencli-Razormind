"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import * as api from "./endpoints";
import { getApiInstanceId } from "./restart-orchestration";
import type {
  AIAgent,
  ApprovalDecision,
  Automation,
  FeedProviderInput,
  ModelDefaultCandidate,
  ModelProviderInput,
  ModelRole,
  NotificationRuleInput,
  OperationsAgentMode,
  ProviderModelDiscoveryInput,
  SystemConfig,
  WorkspaceSettingsValues,
} from "./types";

export function useMyWorkspaces() {
  return useQuery({ queryKey: ["workspaces"], queryFn: api.listMyWorkspaces });
}

export function useWorkspaceProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ["workspace-projects", workspaceId],
    queryFn: () => api.listWorkspaceProjects(workspaceId as string),
    enabled: !!workspaceId,
  });
}

export function useGovernedWorkspaces() {
  return useQuery({
    queryKey: ["governance-workspaces"],
    queryFn: api.listGovernedWorkspaces,
  });
}

export function useGovernedWorkspaceProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ["governance-workspace-projects", workspaceId],
    queryFn: () => api.listGovernedWorkspaceProjects(workspaceId as string),
    enabled: !!workspaceId,
  });
}

export function useWorkspaceSources(workspaceId: string | null) {
  return useQuery({
    queryKey: ["workspace-sources", workspaceId],
    queryFn: () => api.listWorkspaceSources(workspaceId as string),
    enabled: !!workspaceId,
  });
}

export function useProjectSourceBindings(
  workspaceId: string | null,
  projectId: string | null,
) {
  return useQuery({
    queryKey: ["project-source-bindings", workspaceId, projectId],
    queryFn: () =>
      api.listProjectSourceBindings(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
  });
}

export function useProjectSourceBindingRevisions(
  workspaceId: string | null,
  projectId: string | null,
  bindingId: string | null,
) {
  return useQuery({
    queryKey: [
      "project-source-binding-revisions",
      workspaceId,
      projectId,
      bindingId,
    ],
    queryFn: () =>
      api.listProjectSourceBindingRevisions(
        workspaceId as string,
        projectId as string,
        bindingId as string,
      ),
    enabled: !!workspaceId && !!projectId && !!bindingId,
  });
}

export function useCreateProjectSourceBinding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
      data,
    }: {
      workspaceId: string;
      projectId: string;
      data: Parameters<typeof api.createProjectSourceBinding>[2];
    }) => api.createProjectSourceBinding(workspaceId, projectId, data),
    onSuccess: (_result, { workspaceId, projectId }) =>
      queryClient.invalidateQueries({
        queryKey: ["project-source-bindings", workspaceId, projectId],
      }),
  });
}

export function useDeleteWorkspaceProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
    }: {
      workspaceId: string;
      projectId: string;
    }) => api.deleteWorkspaceProject(workspaceId, projectId),
    onSuccess: (_result, { workspaceId, projectId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["workspace-projects", workspaceId],
      });
      queryClient.removeQueries({
        queryKey: ["project-workflows", workspaceId, projectId],
      });
    },
  });
}

export function useProjectWorkflows(
  workspaceId: string | null,
  projectId: string | null,
) {
  return useQuery({
    queryKey: ["project-workflows", workspaceId, projectId],
    queryFn: () =>
      api.listProjectWorkflows(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
  });
}

export function useProjectRuntimeSummary(
  workspaceId: string | null,
  projectId: string | null,
) {
  return useQuery({
    queryKey: ["project-runtime-summary", workspaceId, projectId],
    queryFn: () =>
      api.getProjectRuntimeSummary(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
    refetchInterval: 15_000,
  });
}

export function useProjectRuntimeLogs(
  workspaceId: string | null,
  projectId: string | null,
  params: { status?: string; search?: string; page: number; limit: number },
) {
  return useQuery({
    queryKey: ["project-runtime-logs", workspaceId, projectId, params],
    queryFn: () =>
      api.listProjectRuntimeLogs(
        workspaceId as string,
        projectId as string,
        params,
      ),
    enabled: !!workspaceId && !!projectId,
    refetchInterval: 10_000,
  });
}

export function useProjectRuntimeTrace(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  params?: { afterSequence?: number; limit?: number },
) {
  return useQuery({
    queryKey: [
      "project-runtime-trace",
      workspaceId,
      projectId,
      workflowId,
      runId,
      params,
    ],
    queryFn: () =>
      api.getProjectRuntimeTrace(
        workspaceId as string,
        projectId as string,
        workflowId as string,
        runId as string,
        params,
      ),
    enabled: !!workspaceId && !!projectId && !!workflowId && !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.trace.projection.status;
      return status &&
        ["completed", "failed", "blocked", "partial_success"].includes(status)
        ? false
        : 3_000;
    },
  });
}

export function useRunAnalysisSnapshotCapability(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "run-analysis-snapshot-capability",
      workspaceId,
      projectId,
      workflowId,
      runId,
    ],
    queryFn: () =>
      api.getRunAnalysisSnapshotCapability(
        workspaceId as string,
        projectId as string,
        workflowId as string,
        runId as string,
      ),
    enabled:
      enabled && !!workspaceId && !!projectId && !!workflowId && !!runId,
    staleTime: 15_000,
  });
}

export function usePreviewRunAnalysisSnapshot() {
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
      workflowId,
      runId,
      data,
    }: {
      workspaceId: string;
      projectId: string;
      workflowId: string;
      runId: string;
      data: Parameters<typeof api.previewRunAnalysisSnapshot>[4];
    }) =>
      api.previewRunAnalysisSnapshot(
        workspaceId,
        projectId,
        workflowId,
        runId,
        data,
      ),
  });
}

export function useCreateRunAnalysisSnapshot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
      workflowId,
      runId,
      data,
    }: {
      workspaceId: string;
      projectId: string;
      workflowId: string;
      runId: string;
      data: Parameters<typeof api.createRunAnalysisSnapshot>[4];
    }) =>
      api.createRunAnalysisSnapshot(
        workspaceId,
        projectId,
        workflowId,
        runId,
        data,
      ),
    onSuccess: (receipt, { workspaceId, projectId, workflowId, runId }) => {
      queryClient.setQueryData(
        [
          "run-analysis-snapshot-receipt",
          workspaceId,
          projectId,
          workflowId,
          runId,
          receipt.snapshotId,
        ],
        receipt,
      );
      void queryClient.invalidateQueries({
        queryKey: [
          "run-analysis-snapshot-receipts",
          workspaceId,
          projectId,
          workflowId,
          runId,
        ],
      });
    },
    onError: (_error, { workspaceId, projectId, workflowId, runId }) =>
      queryClient.invalidateQueries({
        queryKey: [
          "run-analysis-snapshot-receipts",
          workspaceId,
          projectId,
          workflowId,
          runId,
        ],
      }),
  });
}

export function useRunAnalysisSnapshotReceipts(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "run-analysis-snapshot-receipts",
      workspaceId,
      projectId,
      workflowId,
      runId,
    ],
    queryFn: () =>
      api.listRunAnalysisSnapshots(
        workspaceId as string,
        projectId as string,
        workflowId as string,
        runId as string,
      ),
    enabled:
      enabled && !!workspaceId && !!projectId && !!workflowId && !!runId,
  });
}

export function useRunAnalysisSnapshotReceipt(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  snapshotId: string | null,
) {
  return useQuery({
    queryKey: [
      "run-analysis-snapshot-receipt",
      workspaceId,
      projectId,
      workflowId,
      runId,
      snapshotId,
    ],
    queryFn: () =>
      api.getRunAnalysisSnapshot(
        workspaceId as string,
        projectId as string,
        workflowId as string,
        runId as string,
        snapshotId as string,
      ),
    enabled:
      !!workspaceId &&
      !!projectId &&
      !!workflowId &&
      !!runId &&
      !!snapshotId,
    refetchInterval: (query) =>
      query.state.data?.status === "exporting" ? 1_000 : false,
  });
}

export function useRunAnalysisSnapshotSummary(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  snapshotId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["run-analysis-snapshot-summary", workspaceId, projectId, workflowId, runId, snapshotId],
    queryFn: () =>
      api.getRunAnalysisSnapshotSummary(
        workspaceId as string,
        projectId as string,
        workflowId as string,
        runId as string,
        snapshotId as string,
      ),
    enabled:
      enabled &&
      !!workspaceId &&
      !!projectId &&
      !!workflowId &&
      !!runId &&
      !!snapshotId,
  });
}

export function useProjectRecordGraph(
  workspaceId: string | null,
  projectId: string | null,
  maxNodes: number,
) {
  return useQuery({
    queryKey: ["project-record-graph", workspaceId, projectId, maxNodes],
    queryFn: () =>
      api.getProjectRecordGraph(workspaceId as string, projectId as string, {
        max_nodes: maxNodes,
      }),
    enabled: !!workspaceId && !!projectId,
    staleTime: 30_000,
  });
}

export function useBootstrapWorkspaceProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      data,
    }: {
      workspaceId: string;
      data: Parameters<typeof api.bootstrapWorkspaceProject>[1];
    }) => api.bootstrapWorkspaceProject(workspaceId, data),
    onSuccess: (result, { workspaceId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["workspace-projects", workspaceId],
      });
      queryClient.setQueryData(
        ["project-workflows", workspaceId, result.project.id],
        [result.primary_workflow],
      );
    },
  });
}

export function useCreateProjectWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
      data,
    }: {
      workspaceId: string;
      projectId: string;
      data: Parameters<typeof api.createProjectWorkflow>[2];
    }) => api.createProjectWorkflow(workspaceId, projectId, data),
    onSuccess: (_workflow, { workspaceId, projectId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["project-workflows", workspaceId, projectId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["workspace-projects", workspaceId],
      });
    },
  });
}

export function useOperationsInbox(
  workspaceId: string | null,
  status?: string,
) {
  return useQuery({
    queryKey: ["operations-inbox", workspaceId, status],
    queryFn: () =>
      api.listOperationsInbox(workspaceId as string, { status, limit: 100 }),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  });
}

export function useDecideOperationsApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      approvalId,
      decision,
      reason,
    }: {
      workspaceId: string;
      approvalId: string;
      decision: ApprovalDecision;
      reason: string;
    }) =>
      api.decideOperationsApproval(workspaceId, approvalId, {
        decision,
        reason,
      }),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({
        queryKey: ["operations-inbox", workspaceId],
      }),
  });
}

export function useOperationsAgents(workspaceId: string | null) {
  return useQuery({
    queryKey: ["operations-agents", workspaceId],
    queryFn: () => api.listOperationsAgents(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  });
}

export function useOperationsAgentActivity(workspaceId: string | null) {
  return useQuery({
    queryKey: ["operations-agent-activity", workspaceId],
    queryFn: () => api.listOperationsAgentActivity(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 5_000,
  });
}

export function useOperationsAgentDraft(
  workspaceId: string | null,
  agentId: string | null,
) {
  return useQuery({
    queryKey: ["operations-agent-draft", workspaceId, agentId],
    queryFn: () =>
      api.getOperationsAgentDraft(workspaceId as string, agentId as string),
    enabled: !!workspaceId && !!agentId,
  });
}

export function useOperationsAgentVersions(
  workspaceId: string | null,
  agentId: string | null,
) {
  return useQuery({
    queryKey: ["operations-agent-versions", workspaceId, agentId],
    queryFn: () =>
      api.listOperationsAgentVersions(workspaceId as string, agentId as string),
    enabled: !!workspaceId && !!agentId,
  });
}

export function useUpdateOperationsAgentDraft() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      data,
    }: {
      workspaceId: string;
      agentId: string;
      data: Parameters<typeof api.updateOperationsAgentDraft>[2];
    }) => api.updateOperationsAgentDraft(workspaceId, agentId, data),
    onSuccess: (draft, { workspaceId, agentId }) =>
      queryClient.setQueryData(
        ["operations-agent-draft", workspaceId, agentId],
        draft,
      ),
  });
}

export function usePublishOperationsAgentVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      reason,
    }: {
      workspaceId: string;
      agentId: string;
      reason: string;
    }) => api.publishOperationsAgentVersion(workspaceId, agentId, reason),
    onSuccess: (_version, { workspaceId, agentId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["operations-agent-versions", workspaceId, agentId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["operations-agents", workspaceId],
      });
    },
  });
}

export function useStartOperationsAgentRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      data,
    }: {
      workspaceId: string;
      agentId: string;
      data: Parameters<typeof api.startOperationsAgentRun>[2];
    }) => api.startOperationsAgentRun(workspaceId, agentId, data),
    onSuccess: (_run, { workspaceId }) =>
      queryClient.invalidateQueries({
        queryKey: ["operations-agent-activity", workspaceId],
      }),
  });
}

export function useAutomations(workspaceId: string | null) {
  return useQuery({
    queryKey: ["automations", workspaceId],
    queryFn: () => api.listAutomations(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  });
}

export function useInstallAutomationStarters() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId }: { workspaceId: string }) => api.installAutomationStarters(workspaceId),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ['automations', workspaceId] }),
  })
}

export function useCreateAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      data,
    }: {
      workspaceId: string;
      data: Omit<
        Automation,
        | "id"
        | "workspace_id"
        | "created_by_user_id"
        | "created_at"
        | "updated_at"
      >;
    }) => api.createAutomation(workspaceId, data),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ["automations", workspaceId] }),
  });
}

export function usePatchAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      automationId,
      data,
    }: {
      workspaceId: string;
      automationId: string;
      data: Partial<Automation>;
    }) => api.patchAutomation(workspaceId, automationId, data),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ["automations", workspaceId] }),
  });
}

export function usePatchOperationsAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      disabled,
    }: {
      workspaceId: string;
      agentId: string;
      disabled: boolean;
    }) => api.patchOperationsAgent(workspaceId, agentId, disabled),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({
        queryKey: ["operations-agents", workspaceId],
      }),
  });
}

export function useAssignOperationsAgentProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      mode,
      toolScope,
      resourceScope,
      actionScope,
      reason,
    }: {
      workspaceId: string;
      agentId: string;
      mode: OperationsAgentMode;
      toolScope: string[];
      resourceScope: string[];
      actionScope: string[];
      reason: string;
    }) =>
      api.assignOperationsAgentProfile(workspaceId, agentId, {
        mode,
        tool_scope: toolScope,
        resource_scope: resourceScope,
        action_scope: actionScope,
        reason,
      }),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({
        queryKey: ["operations-agents", workspaceId],
      }),
  });
}

export function useDashboardStats() {
  return useQuery({
    queryKey: ["dashboard", "stats"],
    queryFn: () => api.getDashboardStats(),
    refetchInterval: 30_000,
  });
}

export function useDashboardActivity(days = 14) {
  return useQuery({
    queryKey: ["dashboard", "activity", days],
    queryFn: () => api.getDashboardActivity({ days }),
  });
}

export function useOpinionMonitor() {
  return useQuery({
    queryKey: ["dashboard", "opinion-monitor"],
    queryFn: () => api.getOpinionMonitor({ range: "7d", limit: 8 }),
    refetchInterval: 30_000,
  });
}

export function useSources(params?: {
  page?: number;
  limit?: number;
  enabled?: boolean;
}) {
  return useQuery({
    queryKey: ["sources", params],
    queryFn: () => api.listSources(params),
  });
}

export function useTasks(params?: {
  source_id?: string;
  status?: string;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["tasks", params],
    queryFn: () => api.listTasks(params),
  });
}

export function useInfiniteTasks(params?: {
  source_id?: string;
  status?: string;
  limit?: number;
}) {
  return useInfiniteQuery({
    queryKey: ["tasks", "infinite", params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.listTasks({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta;
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined;
    },
  });
}

export function useRecords(params?: {
  source_id?: string;
  project_id?: string;
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
}) {
  return useQuery({
    queryKey: ["records", params],
    queryFn: () => api.listRecords(params),
  });
}

export function usePresets() {
  return useQuery({
    queryKey: ["presets"],
    queryFn: () => api.listPresets(),
    staleTime: 5 * 60_000,
  });
}

export function useBrowserActPacks() {
  return useQuery({
    queryKey: ["browser-act-packs"],
    queryFn: () => api.listBrowserActPacks(),
    staleTime: 5 * 60_000,
  });
}

export function useBrowserRuntimeBundles() {
  return useQuery({
    queryKey: ["browser-runtime-bundles"],
    queryFn: api.listBrowserRuntimeBundles,
    staleTime: 30_000,
  });
}

export function useCreateBrowserRuntimeBundle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createBrowserRuntimeBundle,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["browser-runtime-bundles"] }),
  });
}

export function useUpdateBrowserRuntimeBundle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Parameters<typeof api.updateBrowserRuntimeBundle>[1];
    }) => api.updateBrowserRuntimeBundle(id, data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["browser-runtime-bundles"] }),
  });
}

export function useDeleteBrowserRuntimeBundle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteBrowserRuntimeBundle,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["browser-runtime-bundles"] }),
  });
}

// ── Chrome instances (browsers.py pool CRUD) ────────────────────────────────
// Note: useChromePool() (the list/status read, GET /workers/chrome-pool) is
// defined further below alongside the other execution-resource reads — reused
// here rather than duplicated.
export function useAddChromeInstance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      count?: number;
      mode?: "bridge" | "cdp";
      agent_url?: string | null;
      agent_protocol?: "http" | "ws" | "" | null;
      profile_kind?: "anonymous" | "authenticated";
      profile_name?: string;
      runtime_bundle_id?: string | null;
      resource_class?: string;
      startup_pages?: string[];
      network_policy?: Record<string, unknown>;
    }) =>
      api.addChromeInstance(
        data.count,
        data.mode,
        data.agent_url ?? "",
        data.agent_protocol ?? "",
        {
          mode: data.mode,
          agent_url: data.agent_url || null,
          agent_protocol: data.agent_protocol || null,
          profile_kind: data.profile_kind,
          profile_name: data.profile_name,
          runtime_bundle_id: data.runtime_bundle_id,
          resource_class: data.resource_class,
          startup_pages: data.startup_pages,
          network_policy: data.network_policy,
        },
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chrome-pool"] }),
  });
}

export function useUpdateChromeInstanceConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      endpoint,
      data,
    }: {
      endpoint: string;
      data: {
        mode?: "bridge" | "cdp";
        agent_url?: string | null;
        agent_protocol?: "http" | "ws" | null;
        profile_kind?: "anonymous" | "authenticated";
        profile_name?: string;
        runtime_bundle_id?: string | null;
        resource_class?: string;
        startup_pages?: string[];
        network_policy?: Record<string, unknown>;
      };
    }) => api.updateChromeInstanceConfig(endpoint, data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chrome-pool"] }),
  });
}

export function useRemoveChromeInstance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (n: number) => api.removeChromeInstance(n),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["chrome-pool"] }),
  });
}

// ── Browser bindings (site → browser endpoint routing) ──────────────────────
export function useBrowserBindings() {
  return useQuery({
    queryKey: ["browser-bindings"],
    queryFn: () => api.listBrowserBindings(),
  });
}

export function useCreateBrowserBinding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      browser_endpoint: string;
      site: string;
      notes?: string;
    }) => api.createBrowserBinding(data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["browser-bindings"] }),
  });
}

export function useDeleteBrowserBinding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteBrowserBinding(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["browser-bindings"] }),
  });
}

export function usePlans(params?: {
  draft?: boolean;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["plans", params],
    queryFn: () => api.listPlans(params),
  });
}

export function usePlan(id: string | null) {
  return useQuery({
    queryKey: ["plans", id],
    queryFn: () => api.getPlan(id as string),
    enabled: !!id,
  });
}

export function useCreatePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createPlan>[0]) =>
      api.createPlan(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plans"] }),
  });
}

export function useUpdatePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Parameters<typeof api.updatePlan>[1];
    }) => api.updatePlan(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plans"] }),
  });
}

export function useDeletePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deletePlan(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plans"] }),
  });
}

// Run is synchronous (the response already reflects the completed run — see
// endpoints.ts's runPlan comment), so callers keep the PlanRunRead result in
// local component state keyed by plan id, same non-caching rationale as
// useTestProvider above. Success still invalidates Plan Health so the
// per-plan health badge picks up the new run's rows.
export function useRunPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      parameters,
    }: {
      id: string;
      parameters?: Record<string, unknown>;
    }) => api.runPlan(id, parameters),
    onSuccess: (_result, { id }) =>
      queryClient.invalidateQueries({ queryKey: ["plans", id, "health"] }),
  });
}

export function usePlanHealth(
  id: string | null,
  params?: { run_key?: string; page?: number; limit?: number },
) {
  return useQuery({
    queryKey: ["plans", id, "health", params],
    queryFn: () => api.getPlanHealth(id as string, params),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useSource(id: string | null) {
  return useQuery({
    queryKey: ["sources", id],
    queryFn: () => api.getSource(id as string),
    enabled: !!id,
  });
}

export function useSourceControlState(id: string | null) {
  return useQuery({
    queryKey: ["sources", id, "control-state"],
    queryFn: () => api.getSourceControlState(id as string),
    enabled: !!id,
    refetchInterval: 15_000,
  });
}

export function useSourceMeasurements(
  id: string | null,
  params?: { page?: number; limit?: number },
) {
  return useQuery({
    queryKey: ["sources", id, "measurements", params],
    queryFn: () => api.listSourceMeasurements(id as string, params),
    enabled: !!id,
  });
}

// Encrypted credential store (backend.auth.AuthManager). The list endpoint
// only ever returns key_name — see api.listSourceCredentials — so there is
// nothing secret-shaped in this query's cache to worry about redisplaying.
export function useSourceCredentials(id: string | null) {
  return useQuery({
    queryKey: ["sources", id, "credentials"],
    queryFn: () => api.listSourceCredentials(id as string),
    enabled: !!id,
  });
}

export function useStoreSourceCredential(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { key_name: string; secret: string }) =>
      api.storeSourceCredential(id, data),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["sources", id, "credentials"],
      }),
  });
}

export function useDeleteSourceCredential(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (keyName: string) => api.deleteSourceCredential(id, keyName),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["sources", id, "credentials"],
      }),
  });
}

export function useSchedules(params?: {
  source_id?: string;
  enabled?: boolean;
}) {
  return useQuery({
    queryKey: ["schedules", params],
    queryFn: () => api.listSchedules(params),
    refetchInterval: 30_000,
  });
}

export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createSchedule>[0]) =>
      api.createSchedule(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Parameters<typeof api.updateSchedule>[1];
    }) => api.updateSchedule(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSchedule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useAgents(params?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["agents", params],
    queryFn: () => api.listAgents(params),
  });
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<AIAgent>) => api.createAgent(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useUpdateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<AIAgent> }) =>
      api.updateAgent(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAgent(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
}

// Singleton workspace settings (GET/PATCH/DELETE /settings) — unlike every
// other resource in this file, there's no list to invalidate; the query key
// is a fixed singleton tuple and every mutation invalidates that same key.
export function useWorkspaceSettings() {
  return useQuery({
    queryKey: ["workspace-settings"],
    queryFn: () => api.getWorkspaceSettings(),
  });
}

export function useUpdateWorkspaceSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<WorkspaceSettingsValues>) =>
      api.updateWorkspaceSettings(data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["workspace-settings"] }),
  });
}

export function useResetWorkspaceSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.resetWorkspaceSettings(),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["workspace-settings"] }),
  });
}

export function useSkills(params?: {
  domain?: string;
  enabled?: boolean;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["skills", params],
    queryFn: () => api.listSkills(params),
  });
}

export function useSkill(id: string | null) {
  return useQuery({
    queryKey: ["skills", id],
    queryFn: () => api.getSkill(id as string),
    enabled: !!id,
  });
}

export function useDismissCorrection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.dismissCorrection(id),
    // The list query key carries a variable `params` object, so invalidate the
    // whole 'skills' prefix — covers the list (any params) and this skill's
    // own detail query (['skills', id]) in one call.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useRollbackSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.rollbackSkill(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useNotificationRules() {
  return useQuery({
    queryKey: ["notification-rules"],
    queryFn: () => api.listNotificationRules(),
  });
}

export function useCreateNotificationRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: NotificationRuleInput) =>
      api.createNotificationRule(data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["notification-rules"] }),
  });
}

export function useUpdateNotificationRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: NotificationRuleInput }) =>
      api.updateNotificationRule(id, data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["notification-rules"] }),
  });
}

export function useDeleteNotificationRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteNotificationRule(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["notification-rules"] }),
  });
}

export function useNotificationLogs(params?: {
  rule_id?: string;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["notification-logs", params],
    queryFn: () => api.listNotificationLogs(params),
  });
}

export function useInfiniteNotificationLogs(params?: {
  rule_id?: string;
  limit?: number;
}) {
  return useInfiniteQuery({
    queryKey: ["notification-logs", "infinite", params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.listNotificationLogs({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta;
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined;
    },
  });
}

export function useProviders() {
  return useQuery({
    queryKey: ["providers"],
    queryFn: () => api.listProviders(),
  });
}

export function useProviderModels(providerId: string | null) {
  return useQuery({
    queryKey: ["providers", providerId, "models"],
    queryFn: () => api.listProviderModels(providerId as string),
    enabled: !!providerId,
  });
}

export function useModelDefaults() {
  return useQuery({
    queryKey: ["model-defaults"],
    queryFn: () => api.listModelDefaults(),
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ModelProviderInput) => api.createProvider(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDiscoverProviderModels() {
  return useMutation({
    mutationFn: (data: ProviderModelDiscoveryInput) =>
      api.discoverProviderModels(data),
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ModelProviderInput }) =>
      api.updateProvider(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

export function useDeleteProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
}

// Result is intentionally NOT cached in react-query state long-term by the
// caller — GET /providers never returns the last test outcome, so callers
// keep it in local component state keyed by provider id for the page session.
export function useTestProvider() {
  return useMutation({
    mutationFn: (id: string) => api.testProvider(id),
  });
}

export function useFeedProviders() {
  return useQuery({
    queryKey: ["feed-providers"],
    queryFn: () => api.listFeedProviders(),
  });
}

export function useCreateFeedProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: FeedProviderInput) => api.createFeedProvider(data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["feed-providers"] }),
  });
}

export function useUpdateFeedProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: FeedProviderInput }) =>
      api.updateFeedProvider(id, data),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["feed-providers"] }),
  });
}

export function useDeleteFeedProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteFeedProvider(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["feed-providers"] }),
  });
}

export function useTestFeedProvider() {
  return useMutation({ mutationFn: (id: string) => api.testFeedProvider(id) });
}

export function useFeedProviderCatalog(providerId: string | null) {
  return useQuery({
    queryKey: ["feed-providers", providerId, "catalog"],
    queryFn: () => api.getFeedProviderCatalog(providerId as string),
    enabled: !!providerId,
  });
}

export function useBuildFeedProviderWorkflowNode() {
  return useMutation({
    mutationFn: ({
      providerId,
      data,
    }: {
      providerId: string;
      data: Parameters<typeof api.buildFeedProviderWorkflowNode>[1];
    }) => api.buildFeedProviderWorkflowNode(providerId, data),
  });
}

export function useSyncProviderModels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.syncProviderModels(id),
    onSuccess: (_result, id) =>
      queryClient.invalidateQueries({ queryKey: ["providers", id, "models"] }),
  });
}

export function useAddProviderModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerId,
      data,
    }: {
      providerId: string;
      data: Parameters<typeof api.addProviderModel>[1];
    }) => api.addProviderModel(providerId, data),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({
        queryKey: ["providers", providerId, "models"],
      }),
  });
}

export function useUpdateProviderModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerId,
      modelRowId,
      data,
    }: {
      providerId: string;
      modelRowId: string;
      data: Parameters<typeof api.updateProviderModel>[2];
    }) => api.updateProviderModel(providerId, modelRowId, data),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({
        queryKey: ["providers", providerId, "models"],
      }),
  });
}

export function useDeleteProviderModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      providerId,
      modelRowId,
    }: {
      providerId: string;
      modelRowId: string;
    }) => api.deleteProviderModel(providerId, modelRowId),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({
        queryKey: ["providers", providerId, "models"],
      }),
  });
}

export function usePutModelDefault() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      role,
      candidates,
    }: {
      role: ModelRole;
      candidates: ModelDefaultCandidate[];
    }) => api.putModelDefault(role, candidates),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["model-defaults"] }),
  });
}

export function useNodes() {
  return useQuery({
    queryKey: ["nodes"],
    queryFn: () => api.listNodes(),
    refetchInterval: 20_000,
  });
}

export function useWorkers() {
  return useQuery({
    queryKey: ["workers"],
    queryFn: () => api.listWorkers(),
    refetchInterval: 20_000,
  });
}

// ── System / ops (WIRING_GAP_LEDGER W5) ─────────────────────────────────────────
export function useSystemConfig() {
  return useQuery({
    queryKey: ["system-config"],
    queryFn: () => api.getSystemConfig(),
  });
}

// PATCH /system/config only actually persists collection_mode (see
// backend/api/v1/system.py ConfigPatch) — task_executor and image_tag are
// deployment-time settings; sending them is a silent no-op. Callers should
// only pass collection_mode.
export function useUpdateSystemConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<SystemConfig>) => api.updateSystemConfig(data),
    onSuccess: (result) => queryClient.setQueryData(["system-config"], result),
  });
}

// Restarts the whole backend API container (backend/api/v1/browsers.py
// restart_api) — affects every connected user/agent, not just this session.
// Do not invalidate immediately: the process is about to go down. The caller
// waits for bounded liveness recovery and then refreshes active data.
export function useRestartApi() {
  return useMutation({
    mutationFn: async () => {
      let baselineInstanceId: string | undefined
      try {
        baselineInstanceId = getApiInstanceId(await api.getHealth())
      } catch {
        // The protected restart request remains authoritative. Older or
        // temporarily unavailable APIs fall back to observing an outage.
      }

      const response = await api.restartApi()
      const requestedInstanceId = getApiInstanceId(response.data)
      return {
        response,
        baselineInstanceId: requestedInstanceId ?? baselineInstanceId,
      }
    },
  });
}

export function useCeleryStats() {
  return useQuery({
    queryKey: ["celery-stats"],
    queryFn: () => api.getCeleryStats(),
    refetchInterval: 20_000,
  });
}

export function useChromePool() {
  return useQuery({
    queryKey: ["chrome-pool"],
    queryFn: () => api.getChromePool(),
    refetchInterval: 20_000,
  });
}

export function useControlActions(params?: {
  source_id?: string;
  mode?: string;
  outcome?: string;
  page?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ["control-actions", params],
    queryFn: () => api.listControlActions(params),
  });
}

export function useInfiniteControlActions(params?: {
  source_id?: string;
  mode?: string;
  outcome?: string;
  limit?: number;
}) {
  return useInfiniteQuery({
    queryKey: ["control-actions", "infinite", params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.listControlActions({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta;
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined;
    },
  });
}

// ── Control plane (issue 03 / PR-Control-3.5 / C2) ──────────────────────────────
// Global actuator kill switch — polled like other control-state surfaces so an
// operator sees a runtime-side flip (e.g. from another tab) without a manual
// refresh. The mutation writes the fresh server response straight into the
// query cache instead of invalidating, since the POST response IS the new
// canonical snapshot (KillSwitchRead) — no extra round trip needed.
export function useKillSwitch() {
  return useQuery({
    queryKey: ["control", "kill-switch"],
    queryFn: () => api.getKillSwitch(),
    refetchInterval: 15_000,
  });
}

export function useSetKillSwitch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (engaged: boolean) => api.setKillSwitch(engaged),
    onSuccess: (result) => {
      queryClient.setQueryData(["control", "kill-switch"], result);
    },
  });
}

// Agreement/recovery report over the control_actions ledger — an on-demand
// read like the actions table itself (no refetchInterval); the backend runs
// its lazy outcome-evaluation pass on every read so refetching is how an
// operator gets a newer verdict, not a background poll.
export function useAdvisoryReport() {
  return useQuery({
    queryKey: ["control", "advisory-report"],
    queryFn: () => api.getAdvisoryReport(),
  });
}

// System-level ODP data-plane snapshot — polled at the same cadence as
// useNodes/useWorkers/useSourceControlState so the dashboard reflects a
// down Redis or DLQ backlog without a manual refresh.
export function useOdpState() {
  return useQuery({
    queryKey: ["control", "odp-state"],
    queryFn: () => api.getOdpState(),
    refetchInterval: 15_000,
  });
}

export function useWorkbenchRepositories(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workbench', workspaceId, 'repositories'],
    queryFn: () => api.listWorkbenchRepositories(workspaceId as string),
    enabled: !!workspaceId,
  })
}

export function useWorkbenchRuntimes(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workbench', workspaceId, 'runtimes'],
    queryFn: () => api.listWorkbenchRuntimes(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  })
}

export function useWorkbenchThreads(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workbench', workspaceId, 'threads'],
    queryFn: () => api.listWorkbenchThreads(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 10_000,
  })
}

export function useWorkbenchThread(workspaceId: string | null, threadId: string | null) {
  return useQuery({
    queryKey: ['workbench', workspaceId, 'threads', threadId],
    queryFn: () => api.getWorkbenchThread(workspaceId as string, threadId as string),
    enabled: !!workspaceId && !!threadId,
    refetchInterval: 5_000,
  })
}

export function useWorkbenchEvents(
  workspaceId: string | null,
  threadId: string | null,
  turnId: string | null,
) {
  return useQuery({
    queryKey: ['workbench', workspaceId, 'threads', threadId, 'turns', turnId, 'events'],
    queryFn: () => api.listWorkbenchEvents(workspaceId as string, threadId as string, turnId as string),
    enabled: !!workspaceId && !!threadId && !!turnId,
  })
}

export function useCreateWorkbenchThread() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      data,
    }: {
      workspaceId: string
      data: Parameters<typeof api.createWorkbenchThread>[1]
    }) => api.createWorkbenchThread(workspaceId, data),
    onSuccess: (thread, { workspaceId }) => {
      queryClient.setQueryData(['workbench', workspaceId, 'threads', thread.id], thread)
      void queryClient.invalidateQueries({ queryKey: ['workbench', workspaceId, 'threads'] })
    },
  })
}

export function useCreateWorkbenchTurn() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      threadId,
      data,
    }: {
      workspaceId: string
      threadId: string
      data: Parameters<typeof api.createWorkbenchTurn>[2]
    }) => api.createWorkbenchTurn(workspaceId, threadId, data),
    onSuccess: (_turn, { workspaceId, threadId }) => {
      void queryClient.invalidateQueries({
        queryKey: ['workbench', workspaceId, 'threads', threadId],
      })
      void queryClient.invalidateQueries({ queryKey: ['workbench', workspaceId, 'threads'] })
    },
  })
}

export function useCancelWorkbenchTurn() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, threadId, turnId }: {
      workspaceId: string
      threadId: string
      turnId: string
    }) => api.cancelWorkbenchTurn(workspaceId, threadId, turnId),
    onSuccess: (_turn, { workspaceId, threadId, turnId }) => {
      void queryClient.invalidateQueries({
        queryKey: ['workbench', workspaceId, 'threads', threadId],
      })
      void queryClient.invalidateQueries({
        queryKey: ['workbench', workspaceId, 'threads', threadId, 'turns', turnId, 'events'],
      })
      void queryClient.invalidateQueries({ queryKey: ['workbench', workspaceId, 'threads'] })
    },
  })
}

export function useConfirmWorkbenchProposal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, threadId, proposalId }: {
      workspaceId: string
      threadId: string
      proposalId: string
    }) => api.confirmWorkbenchProposal(workspaceId, threadId, proposalId),
    onSuccess: (_proposal, { workspaceId, threadId }) => {
      void queryClient.invalidateQueries({
        queryKey: ['workbench', workspaceId, 'threads', threadId],
      })
      void queryClient.invalidateQueries({ queryKey: ['workbench', workspaceId, 'threads'] })
    },
  })
}
