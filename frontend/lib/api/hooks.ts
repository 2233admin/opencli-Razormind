'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import * as api from './endpoints'
import type {
  ApprovalDecision,
  Automation,
  FeedProviderInput,
  ModelDefaultCandidate,
  ModelProviderInput,
  ModelRole,
  OperationsAgentMode,
  ProviderModelDiscoveryInput,
} from './types'

export function useMyWorkspaces() {
  return useQuery({ queryKey: ['workspaces'], queryFn: api.listMyWorkspaces })
}

export function useWorkspaceProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspace-projects', workspaceId],
    queryFn: () => api.listWorkspaceProjects(workspaceId as string),
    enabled: !!workspaceId,
  })
}

export function useGovernedWorkspaces() {
  return useQuery({
    queryKey: ['governance-workspaces'],
    queryFn: api.listGovernedWorkspaces,
  })
}

export function useGovernedWorkspaceProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ['governance-workspace-projects', workspaceId],
    queryFn: () => api.listGovernedWorkspaceProjects(workspaceId as string),
    enabled: !!workspaceId,
  })
}

export function useWorkspaceSources(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspace-sources', workspaceId],
    queryFn: () => api.listWorkspaceSources(workspaceId as string),
    enabled: !!workspaceId,
  })
}

export function useProjectSourceBindings(workspaceId: string | null, projectId: string | null) {
  return useQuery({
    queryKey: ['project-source-bindings', workspaceId, projectId],
    queryFn: () => api.listProjectSourceBindings(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
  })
}

export function useProjectSourceBindingRevisions(
  workspaceId: string | null,
  projectId: string | null,
  bindingId: string | null,
) {
  return useQuery({
    queryKey: ['project-source-binding-revisions', workspaceId, projectId, bindingId],
    queryFn: () => api.listProjectSourceBindingRevisions(
      workspaceId as string,
      projectId as string,
      bindingId as string,
    ),
    enabled: !!workspaceId && !!projectId && !!bindingId,
  })
}

export function useCreateProjectSourceBinding() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      projectId,
      data,
    }: {
      workspaceId: string
      projectId: string
      data: Parameters<typeof api.createProjectSourceBinding>[2]
    }) => api.createProjectSourceBinding(workspaceId, projectId, data),
    onSuccess: (_result, { workspaceId, projectId }) =>
      queryClient.invalidateQueries({
        queryKey: ['project-source-bindings', workspaceId, projectId],
      }),
  })
}

export function useDeleteWorkspaceProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId }: { workspaceId: string; projectId: string }) =>
      api.deleteWorkspaceProject(workspaceId, projectId),
    onSuccess: (_result, { workspaceId, projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-projects', workspaceId] })
      queryClient.removeQueries({ queryKey: ['project-workflows', workspaceId, projectId] })
    },
  })
}

export function useProjectWorkflows(workspaceId: string | null, projectId: string | null) {
  return useQuery({
    queryKey: ['project-workflows', workspaceId, projectId],
    queryFn: () => api.listProjectWorkflows(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
  })
}

export function useProjectRuntimeSummary(workspaceId: string | null, projectId: string | null) {
  return useQuery({
    queryKey: ['project-runtime-summary', workspaceId, projectId],
    queryFn: () => api.getProjectRuntimeSummary(workspaceId as string, projectId as string),
    enabled: !!workspaceId && !!projectId,
    refetchInterval: 15_000,
  })
}

export function useProjectRuntimeLogs(
  workspaceId: string | null,
  projectId: string | null,
  params: { status?: string; search?: string; page: number; limit: number },
) {
  return useQuery({
    queryKey: ['project-runtime-logs', workspaceId, projectId, params],
    queryFn: () => api.listProjectRuntimeLogs(
      workspaceId as string,
      projectId as string,
      params,
    ),
    enabled: !!workspaceId && !!projectId,
    refetchInterval: 10_000,
  })
}

export function useProjectRuntimeTrace(
  workspaceId: string | null,
  projectId: string | null,
  workflowId: string | null,
  runId: string | null,
  params?: { afterSequence?: number; limit?: number },
) {
  return useQuery({
    queryKey: ['project-runtime-trace', workspaceId, projectId, workflowId, runId, params],
    queryFn: () => api.getProjectRuntimeTrace(
      workspaceId as string,
      projectId as string,
      workflowId as string,
      runId as string,
      params,
    ),
    enabled: !!workspaceId && !!projectId && !!workflowId && !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.trace.projection.status
      return status && ['completed', 'failed', 'blocked', 'partial_success'].includes(status)
        ? false
        : 3_000
    },
  })
}

export function useProjectRecordGraph(
  workspaceId: string | null,
  projectId: string | null,
  maxNodes: number,
) {
  return useQuery({
    queryKey: ['project-record-graph', workspaceId, projectId, maxNodes],
    queryFn: () =>
      api.getProjectRecordGraph(workspaceId as string, projectId as string, {
        max_nodes: maxNodes,
      }),
    enabled: !!workspaceId && !!projectId,
    staleTime: 30_000,
  })
}

export function useBootstrapWorkspaceProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, data }: { workspaceId: string; data: Parameters<typeof api.bootstrapWorkspaceProject>[1] }) => api.bootstrapWorkspaceProject(workspaceId, data),
    onSuccess: (result, { workspaceId }) => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-projects', workspaceId] })
      queryClient.setQueryData(['project-workflows', workspaceId, result.project.id], [result.primary_workflow])
    },
  })
}

export function useCreateProjectWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId, data }: { workspaceId: string; projectId: string; data: Parameters<typeof api.createProjectWorkflow>[2] }) => api.createProjectWorkflow(workspaceId, projectId, data),
    onSuccess: (_workflow, { workspaceId, projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ['project-workflows', workspaceId, projectId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace-projects', workspaceId] })
    },
  })
}

export function useOperationsInbox(workspaceId: string | null, status?: string) {
  return useQuery({
    queryKey: ['operations-inbox', workspaceId, status],
    queryFn: () => api.listOperationsInbox(workspaceId as string, { status, limit: 100 }),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  })
}

export function useDecideOperationsApproval() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      approvalId,
      decision,
      reason,
    }: {
      workspaceId: string
      approvalId: string
      decision: ApprovalDecision
      reason: string
    }) => api.decideOperationsApproval(workspaceId, approvalId, { decision, reason }),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ['operations-inbox', workspaceId] }),
  })
}

export function useOperationsAgents(workspaceId: string | null) {
  return useQuery({
    queryKey: ['operations-agents', workspaceId],
    queryFn: () => api.listOperationsAgents(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 15_000,
  })
}

export function useOperationsAgentActivity(workspaceId: string | null) {
  return useQuery({
    queryKey: ['operations-agent-activity', workspaceId],
    queryFn: () => api.listOperationsAgentActivity(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: 5_000,
  })
}

export function useOperationsAgentDraft(workspaceId: string | null, agentId: string | null) {
  return useQuery({
    queryKey: ['operations-agent-draft', workspaceId, agentId],
    queryFn: () => api.getOperationsAgentDraft(workspaceId as string, agentId as string),
    enabled: !!workspaceId && !!agentId,
  })
}

export function useOperationsAgentVersions(workspaceId: string | null, agentId: string | null) {
  return useQuery({
    queryKey: ['operations-agent-versions', workspaceId, agentId],
    queryFn: () => api.listOperationsAgentVersions(workspaceId as string, agentId as string),
    enabled: !!workspaceId && !!agentId,
  })
}

export function useUpdateOperationsAgentDraft() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, agentId, data }: { workspaceId: string; agentId: string; data: Parameters<typeof api.updateOperationsAgentDraft>[2] }) =>
      api.updateOperationsAgentDraft(workspaceId, agentId, data),
    onSuccess: (draft, { workspaceId, agentId }) =>
      queryClient.setQueryData(['operations-agent-draft', workspaceId, agentId], draft),
  })
}

export function usePublishOperationsAgentVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, agentId, reason }: { workspaceId: string; agentId: string; reason: string }) =>
      api.publishOperationsAgentVersion(workspaceId, agentId, reason),
    onSuccess: (_version, { workspaceId, agentId }) => {
      void queryClient.invalidateQueries({ queryKey: ['operations-agent-versions', workspaceId, agentId] })
      void queryClient.invalidateQueries({ queryKey: ['operations-agents', workspaceId] })
    },
  })
}

export function useStartOperationsAgentRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      agentId,
      data,
    }: {
      workspaceId: string
      agentId: string
      data: Parameters<typeof api.startOperationsAgentRun>[2]
    }) => api.startOperationsAgentRun(workspaceId, agentId, data),
    onSuccess: (_run, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ['operations-agent-activity', workspaceId] }),
  })
}

export function useAutomations(workspaceId: string | null) {
  return useQuery({ queryKey: ['automations', workspaceId], queryFn: () => api.listAutomations(workspaceId as string), enabled: !!workspaceId, refetchInterval: 15_000 })
}

export function useCreateAutomation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, data }: { workspaceId: string; data: Omit<Automation, 'id' | 'workspace_id' | 'created_by_user_id' | 'created_at' | 'updated_at'> }) => api.createAutomation(workspaceId, data),
    onSuccess: (_result, { workspaceId }) => queryClient.invalidateQueries({ queryKey: ['automations', workspaceId] }),
  })
}

export function usePatchAutomation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, automationId, data }: { workspaceId: string; automationId: string; data: Partial<Automation> }) => api.patchAutomation(workspaceId, automationId, data),
    onSuccess: (_result, { workspaceId }) => queryClient.invalidateQueries({ queryKey: ['automations', workspaceId] }),
  })
}

export function usePatchOperationsAgent() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, agentId, disabled }: { workspaceId: string; agentId: string; disabled: boolean }) =>
      api.patchOperationsAgent(workspaceId, agentId, disabled),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ['operations-agents', workspaceId] }),
  })
}

export function useAssignOperationsAgentProfile() {
  const queryClient = useQueryClient()
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
      workspaceId: string
      agentId: string
      mode: OperationsAgentMode
      toolScope: string[]
      resourceScope: string[]
      actionScope: string[]
      reason: string
    }) => api.assignOperationsAgentProfile(workspaceId, agentId, {
      mode,
      tool_scope: toolScope,
      resource_scope: resourceScope,
      action_scope: actionScope,
      reason,
    }),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: ['operations-agents', workspaceId] }),
  })
}

export function useDashboardStats() {
  return useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: () => api.getDashboardStats(),
    refetchInterval: 30_000,
  })
}

export function useDashboardActivity(days = 14) {
  return useQuery({
    queryKey: ['dashboard', 'activity', days],
    queryFn: () => api.getDashboardActivity({ days }),
  })
}

export function useOpinionMonitor() {
  return useQuery({
    queryKey: ['dashboard', 'opinion-monitor'],
    queryFn: () => api.getOpinionMonitor({ range: '7d', limit: 8 }),
    refetchInterval: 30_000,
  })
}

export function useSources(params?: { page?: number; limit?: number; enabled?: boolean }) {
  return useQuery({
    queryKey: ['sources', params],
    queryFn: () => api.listSources(params),
  })
}

export function useTasks(params?: { source_id?: string; status?: string; page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['tasks', params],
    queryFn: () => api.listTasks(params),
  })
}

export function useInfiniteTasks(
  params?: { source_id?: string; status?: string; limit?: number },
) {
  return useInfiniteQuery({
    queryKey: ['tasks', 'infinite', params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.listTasks({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined
    },
  })
}

export function useRecords(params?: {
  source_id?: string
  project_id?: string
  status?: string
  search?: string
  page?: number
  limit?: number
}) {
  return useQuery({
    queryKey: ['records', params],
    queryFn: () => api.listRecords(params),
  })
}

export function usePresets() {
  return useQuery({
    queryKey: ['presets'],
    queryFn: () => api.listPresets(),
    staleTime: 5 * 60_000,
  })
}

export function useBrowserActPacks() {
  return useQuery({
    queryKey: ['browser-act-packs'],
    queryFn: () => api.listBrowserActPacks(),
    staleTime: 5 * 60_000,
  })
}

export function usePlans(params?: { draft?: boolean; page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['plans', params],
    queryFn: () => api.listPlans(params),
  })
}

export function usePlan(id: string | null) {
  return useQuery({
    queryKey: ['plans', id],
    queryFn: () => api.getPlan(id as string),
    enabled: !!id,
  })
}

export function useSource(id: string | null) {
  return useQuery({
    queryKey: ['sources', id],
    queryFn: () => api.getSource(id as string),
    enabled: !!id,
  })
}

export function useSourceControlState(id: string | null) {
  return useQuery({
    queryKey: ['sources', id, 'control-state'],
    queryFn: () => api.getSourceControlState(id as string),
    enabled: !!id,
    refetchInterval: 15_000,
  })
}

export function useSourceMeasurements(id: string | null, params?: { page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['sources', id, 'measurements', params],
    queryFn: () => api.listSourceMeasurements(id as string, params),
    enabled: !!id,
  })
}

export function useSchedules(params?: { source_id?: string; enabled?: boolean }) {
  return useQuery({
    queryKey: ['schedules', params],
    queryFn: () => api.listSchedules(params),
    refetchInterval: 30_000,
  })
}

export function useAgents(params?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['agents', params],
    queryFn: () => api.listAgents(params),
  })
}

export function useSkills(params?: { domain?: string; enabled?: boolean; page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['skills', params],
    queryFn: () => api.listSkills(params),
  })
}

export function useNotificationRules() {
  return useQuery({
    queryKey: ['notification-rules'],
    queryFn: () => api.listNotificationRules(),
  })
}

export function useNotificationLogs(params?: { rule_id?: string; page?: number; limit?: number }) {
  return useQuery({
    queryKey: ['notification-logs', params],
    queryFn: () => api.listNotificationLogs(params),
  })
}

export function useInfiniteNotificationLogs(
  params?: { rule_id?: string; limit?: number },
) {
  return useInfiniteQuery({
    queryKey: ['notification-logs', 'infinite', params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.listNotificationLogs({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined
    },
  })
}

export function useProviders() {
  return useQuery({
    queryKey: ['providers'],
    queryFn: () => api.listProviders(),
  })
}

export function useProviderModels(providerId: string | null) {
  return useQuery({
    queryKey: ['providers', providerId, 'models'],
    queryFn: () => api.listProviderModels(providerId as string),
    enabled: !!providerId,
  })
}

export function useModelDefaults() {
  return useQuery({
    queryKey: ['model-defaults'],
    queryFn: () => api.listModelDefaults(),
  })
}

export function useCreateProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: ModelProviderInput) => api.createProvider(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useDiscoverProviderModels() {
  return useMutation({
    mutationFn: (data: ProviderModelDiscoveryInput) => api.discoverProviderModels(data),
  })
}

export function useUpdateProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ModelProviderInput }) => api.updateProvider(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['providers'] }),
  })
}

export function useDeleteProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deleteProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['providers'] }),
  })
}

// Result is intentionally NOT cached in react-query state long-term by the
// caller — GET /providers never returns the last test outcome, so callers
// keep it in local component state keyed by provider id for the page session.
export function useTestProvider() {
  return useMutation({
    mutationFn: (id: string) => api.testProvider(id),
  })
}

export function useFeedProviders() {
  return useQuery({
    queryKey: ['feed-providers'],
    queryFn: () => api.listFeedProviders(),
  })
}

export function useCreateFeedProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: FeedProviderInput) => api.createFeedProvider(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed-providers'] }),
  })
}

export function useUpdateFeedProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: FeedProviderInput }) =>
      api.updateFeedProvider(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed-providers'] }),
  })
}

export function useDeleteFeedProvider() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deleteFeedProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed-providers'] }),
  })
}

export function useTestFeedProvider() {
  return useMutation({ mutationFn: (id: string) => api.testFeedProvider(id) })
}

export function useFeedProviderCatalog(providerId: string | null) {
  return useQuery({
    queryKey: ['feed-providers', providerId, 'catalog'],
    queryFn: () => api.getFeedProviderCatalog(providerId as string),
    enabled: !!providerId,
  })
}

export function useBuildFeedProviderWorkflowNode() {
  return useMutation({
    mutationFn: ({
      providerId,
      data,
    }: {
      providerId: string
      data: Parameters<typeof api.buildFeedProviderWorkflowNode>[1]
    }) => api.buildFeedProviderWorkflowNode(providerId, data),
  })
}

export function useSyncProviderModels() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.syncProviderModels(id),
    onSuccess: (_result, id) => queryClient.invalidateQueries({ queryKey: ['providers', id, 'models'] }),
  })
}

export function useAddProviderModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      providerId,
      data,
    }: {
      providerId: string
      data: Parameters<typeof api.addProviderModel>[1]
    }) => api.addProviderModel(providerId, data),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({ queryKey: ['providers', providerId, 'models'] }),
  })
}

export function useUpdateProviderModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      providerId,
      modelRowId,
      data,
    }: {
      providerId: string
      modelRowId: string
      data: Parameters<typeof api.updateProviderModel>[2]
    }) => api.updateProviderModel(providerId, modelRowId, data),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({ queryKey: ['providers', providerId, 'models'] }),
  })
}

export function useDeleteProviderModel() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ providerId, modelRowId }: { providerId: string; modelRowId: string }) =>
      api.deleteProviderModel(providerId, modelRowId),
    onSuccess: (_result, { providerId }) =>
      queryClient.invalidateQueries({ queryKey: ['providers', providerId, 'models'] }),
  })
}

export function usePutModelDefault() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ role, candidates }: { role: ModelRole; candidates: ModelDefaultCandidate[] }) =>
      api.putModelDefault(role, candidates),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['model-defaults'] }),
  })
}

export function useNodes() {
  return useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.listNodes(),
    refetchInterval: 20_000,
  })
}

export function useWorkers() {
  return useQuery({
    queryKey: ['workers'],
    queryFn: () => api.listWorkers(),
    refetchInterval: 20_000,
  })
}

export function useControlActions(params?: {
  source_id?: string
  mode?: string
  outcome?: string
  page?: number
  limit?: number
}) {
  return useQuery({
    queryKey: ['control-actions', params],
    queryFn: () => api.listControlActions(params),
  })
}

export function useKillSwitch(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ['kill-switch'],
    queryFn: api.getKillSwitch,
    refetchInterval: options?.refetchInterval,
  })
}

export function useSetKillSwitch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (engaged: boolean) => api.setKillSwitch(engaged),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['kill-switch'] }),
  })
}

export function useAdvisoryReport(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ['advisory-report'],
    queryFn: api.getAdvisoryReport,
    refetchInterval: options?.refetchInterval,
  })
}

export function useOdpState(options?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ['odp-state'],
    queryFn: api.getOdpState,
    refetchInterval: options?.refetchInterval,
  })
}

export function useInfiniteControlActions(params?: {
  source_id?: string
  mode?: string
  outcome?: string
  limit?: number
}) {
  return useInfiniteQuery({
    queryKey: ['control-actions', 'infinite', params],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.listControlActions({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => {
      const meta = lastPage.meta
      return meta && meta.page < meta.pages ? meta.page + 1 : undefined
    },
  })
}
