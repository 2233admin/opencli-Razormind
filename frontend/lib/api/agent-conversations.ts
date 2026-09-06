import { apiClient } from './client'
import type { ApiResponse } from './types'

export type AgentConversationStatus = 'active' | 'closed'
export type AgentConversationTurnStatus = 'queued' | 'running' | 'completed' | 'proposal' | 'failed' | 'interrupted'

export type AgentConversationRequestContext = {
  project_id?: string | null
  workflow_id?: string | null
  run_id?: string | null
  source_id?: string | null
  surface?: string | null
}

export type AgentConversationContext = AgentConversationRequestContext & {
  /** Server-stamped marker for a durable session opened from Studio. */
  readonly studio_workspace_id?: string | null
}

export type AgentExecutionTarget = {
  id: string
  kind: 'provider' | 'native'
  label: string
  agent: { id?: string; type: string; name: string }
  runtime: { node_id: string; name: string; capabilities: string[]; resume_by_id: boolean } | null
  provider: { id: string; name: string; provider_type: string } | null
  models: Array<{ id: string; label: string }>
  default_model_id: string | null
  readiness: { status: 'ready' | 'unverified' | 'blocked'; reason_code: string | null; reason: string | null }
  setup_url: '/providers' | '/nodes' | '/operations-agents'
  agent_id?: string | null
}

export type AgentExecutionTargets = {
  workspace_id: string
  targets: AgentExecutionTarget[]
  background_execution?: { status: 'ready' | 'blocked'; reason_code: string | null; reason: string | null }
}

export type AgentExecutionBinding = {
  target_id?: string | null
  provider_id?: string | null
  model_id?: string | null
} | null

export type AgentConversationProposal = {
  tool: string
  args: Record<string, unknown>
  summary: string
  diff: string
  work_item_id?: string | null
  workspace_id?: string | null
  proposal_version?: string | null
}

export type AgentConversationResponse = {
  type: 'message' | 'proposal'
  content?: string | null
  proposal?: AgentConversationProposal | null
}

export type AgentConversation = {
  id: string
  workspace_id: string
  title?: string | null
  status: AgentConversationStatus
  created_by_user_id?: string
  context_binding: AgentConversationContext
  revision: number
  created_at: string
  updated_at: string
  execution_binding?: AgentExecutionBinding
}

export type AgentConversationTurn = {
  id: string
  conversation_id?: string
  workspace_id?: string
  sequence: number
  request_id: string
  user_content: string
  response?: AgentConversationResponse | null
  context_binding: AgentConversationContext
  tool_trace: Array<Record<string, unknown>>
  status: AgentConversationTurnStatus
  error_code?: string | null
  error_message?: string | null
  created_at?: string
  updated_at?: string
  agent_run_id?: string | null
}

export type AgentConversationDetail = AgentConversation & {
  turns: AgentConversationTurn[]
}

export type CreateAgentConversationInput = {
  workspace_id?: string | null
  title?: string | null
  context: AgentConversationRequestContext
  execution_target_id?: string | null
  model_id?: string | null
}

export type SendAgentConversationMessageInput = {
  request_id: string
  content: string
  context?: AgentConversationRequestContext
  execution_mode?: 'synchronous' | 'background'
}

export type AgentConversationListFilters = {
  project_id?: string | null
  workflow_id?: string | null
  run_id?: string | null
}

export type AgentConversationMessageResult = {
  conversation_id: string
  turn: AgentConversationTurn
  run?: { id: string; status: string; continuation: { mode: 'history_replay' | 'history_compacted' | 'runtime_resume'; resumed: boolean } } | null
}

export type AgentConversationEventsResult = {
  run: { id: string; status: string }
  events: Array<{ sequence: number; type: string; payload: Record<string, unknown>; created_at: string }>
  next_sequence: number
  terminal: boolean
}

export const listAgentConversations = (
  workspaceId: string,
  limit = 20,
  filters?: AgentConversationListFilters,
  includeProjectSessions = false,
) =>
  apiClient
    .get<ApiResponse<AgentConversation[]>>('/chat/sessions', {
      params: {
        workspace_id: workspaceId,
        limit,
        ...(includeProjectSessions ? { include_project_sessions: true } : {}),
        ...(filters?.project_id ? { project_id: filters.project_id } : {}),
        ...(filters?.workflow_id ? { workflow_id: filters.workflow_id } : {}),
        ...(filters?.run_id ? { run_id: filters.run_id } : {}),
      },
    })
    .then((response) => response.data.data)

export const createAgentConversation = (input: CreateAgentConversationInput) =>
  apiClient
    .post<ApiResponse<AgentConversation>>('/chat/sessions', input)
    .then((response) => response.data.data)

export const listAgentExecutionTargets = (workspaceId: string, context?: AgentConversationRequestContext) =>
  apiClient
    .get<ApiResponse<AgentExecutionTargets>>('/chat/execution-targets', { params: { workspace_id: workspaceId, ...context } })
    .then((response) => response.data.data)

export const getAgentConversation = (conversationId: string, afterSequence = 0, limit = 50) =>
  apiClient
    .get<ApiResponse<AgentConversationDetail>>(`/chat/sessions/${conversationId}`, {
      params: { after_sequence: afterSequence, limit },
    })
    .then((response) => response.data.data)

export const sendAgentConversationMessage = (
  conversationId: string,
  input: SendAgentConversationMessageInput,
) =>
  apiClient
    .post<ApiResponse<AgentConversationMessageResult>>(
      `/chat/sessions/${conversationId}/messages`,
      input,
    )
    .then((response) => response.data.data)

export const closeAgentConversation = (conversationId: string) =>
  apiClient
    .post<ApiResponse<AgentConversation>>(`/chat/sessions/${conversationId}/close`)
    .then((response) => response.data.data)

export const reopenAgentConversation = (conversationId: string) =>
  apiClient
    .post<ApiResponse<AgentConversation>>(`/chat/sessions/${conversationId}/reopen`)
    .then((response) => response.data.data)

export const listAgentConversationEvents = (
  conversationId: string,
  turnId: string,
  afterSequence = 0,
  limit = 100,
) =>
  apiClient
    .get<ApiResponse<AgentConversationEventsResult>>(`/chat/sessions/${conversationId}/turns/${turnId}/events`, {
      params: { after_sequence: afterSequence, limit },
    })
    .then((response) => response.data.data)
