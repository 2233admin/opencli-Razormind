import { apiClient } from './client'
import type { ApiResponse } from './types'

export type AgentConversationStatus = 'active' | 'closed'
export type AgentConversationTurnStatus = 'running' | 'completed' | 'proposal' | 'failed'

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
}

export type AgentConversationDetail = AgentConversation & {
  turns: AgentConversationTurn[]
}

export type CreateAgentConversationInput = {
  workspace_id?: string | null
  title?: string | null
  context: AgentConversationRequestContext
}

export type SendAgentConversationMessageInput = {
  request_id: string
  content: string
  context: AgentConversationRequestContext
}

export type AgentConversationListFilters = {
  project_id?: string | null
  workflow_id?: string | null
  run_id?: string | null
}

export type AgentConversationMessageResult = {
  conversation_id: string
  turn: AgentConversationTurn
}

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
    .then((response) => response.data.data)

export const createAgentConversation = (input: CreateAgentConversationInput) =>
  apiClient
    .post<ApiResponse<AgentConversation>>('/chat/sessions', input)
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
