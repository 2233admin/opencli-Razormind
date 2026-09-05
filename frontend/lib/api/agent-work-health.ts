import { apiClient } from './client'
import type { ApiResponse } from './types'

export type AgentWorkKind = 'conversation' | 'run' | 'automation'
export type AgentWorkState =
  | 'not_started'
  | 'ready'
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'blocked'
  | 'inactive'
  | 'cancelled'

export type AgentWorkActionKind =
  | 'open_conversation'
  | 'pause_run'
  | 'run_automation'
  | 'retry_automation'
  | 'configure'

export interface AgentWorkAction {
  kind: AgentWorkActionKind
  label: string
  conversation_id: string | null
  operations_agent_id: string | null
  run_id: string | null
  automation_id: string | null
}

export interface AgentWorkBinding {
  operations_agent_id: string
  published_version: number
  profile_version: number
  automation_revision: number | null
  runtime: string | null
  agent_url: string | null
}

export interface AgentWorkLatestRun {
  id: string
  status: string
  trigger_type: string
  trigger_reference: string | null
  scheduled_for: string | null
  created_at: string
  updated_at: string
}

export interface AgentWorkHealthItem {
  id: string
  kind: AgentWorkKind
  title: string
  state: AgentWorkState
  message: string
  reason_code: string | null
  recoverable: boolean
  resume_supported: boolean
  project_id: string | null
  conversation_id: string | null
  operations_agent_id: string | null
  run_id: string | null
  automation_id: string | null
  binding: AgentWorkBinding | null
  latest_run: AgentWorkLatestRun | null
  next_due_at: string | null
  actions: AgentWorkAction[]
  updated_at: string
}

export interface AgentWorkHealth {
  workspace_id: string
  studio_workspace_id: string | null
  project_id: string | null
  generated_at: string
  permissions: { can_run: boolean; can_manage: boolean }
  counts: Record<string, number>
  items: AgentWorkHealthItem[]
}

export const agentWorkHealthQueryKey = (workspaceId: string, projectId?: string | null) =>
  ['agent-work-health', workspaceId, projectId ?? null] as const

export async function getAgentWorkHealth(
  workspaceId: string,
  projectId?: string | null,
): Promise<AgentWorkHealth> {
  const response = await apiClient.get<ApiResponse<AgentWorkHealth>>(
    `/workspaces/${encodeURIComponent(workspaceId)}/operations-agents/work-health`,
    { params: projectId ? { project_id: projectId } : undefined },
  )
  return response.data.data
}

export async function performAgentWorkAction(
  workspaceId: string,
  action: AgentWorkAction,
): Promise<void> {
  if (action.kind === 'pause_run' && action.operations_agent_id && action.run_id) {
    await apiClient.post(
      `/workspaces/${encodeURIComponent(workspaceId)}/operations-agents/${encodeURIComponent(action.operations_agent_id)}/runs/${encodeURIComponent(action.run_id)}/pause`,
    )
    return
  }
  if (
    (action.kind === 'run_automation' || action.kind === 'retry_automation')
    && action.automation_id
  ) {
    await apiClient.post(`/workspaces/${encodeURIComponent(workspaceId)}/automations/${encodeURIComponent(action.automation_id)}/runs`)
    return
  }
  throw new Error(`Action ${action.kind} is navigation-only or has no valid target`)
}
