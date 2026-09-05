export const AGENT_CONVERSATION_ROUND_LIMIT = 12
const AGENT_CONVERSATION_MESSAGE_LIMIT = AGENT_CONVERSATION_ROUND_LIMIT * 2

type AgentMessageLike = {
  role: 'user' | 'assistant'
}

type AgentProposalLike = {
  tool: string
  args: Record<string, unknown>
  workspace_id?: string | null
}

export function recentAgentMessages<T extends AgentMessageLike>(messages: readonly T[]): T[] {
  const userMessageIndexes = messages
    .map((message, index) => message.role === 'user' ? index : -1)
    .filter((index) => index >= 0)
  const firstVisibleUserIndex = userMessageIndexes.at(-AGENT_CONVERSATION_ROUND_LIMIT)
  const hardWindowStart = Math.max(0, messages.length - AGENT_CONVERSATION_MESSAGE_LIMIT)

  return messages.slice(Math.max(firstVisibleUserIndex ?? 0, hardWindowStart))
}

const QUERY_KEYS_BY_PROPOSAL_TOOL: Readonly<Record<string, readonly (readonly unknown[])[]>> = {
  toggle_source: [['sources'], ['dashboard', 'stats']],
  trigger_task: [['tasks'], ['dashboard', 'stats']],
  update_schedule: [['schedules']],
  update_provider: [['providers']],
  create_project: [['workspace-projects']],
  update_workflow_draft: [['workspace-projects'], ['project-workflows']],
}

export function proposalQueryKeys(proposal: AgentProposalLike): readonly (readonly unknown[])[] {
  const queryKeys: (readonly unknown[])[] = []
  const targetQueryKeys = QUERY_KEYS_BY_PROPOSAL_TOOL[proposal.tool]
  if (targetQueryKeys) queryKeys.push(...targetQueryKeys)
  if (proposal.workspace_id) queryKeys.push(['operations-inbox', proposal.workspace_id])
  if (proposal.workspace_id && (proposal.tool === 'create_project' || proposal.tool === 'update_workflow_draft')) {
    queryKeys.push(['workspace-projects', proposal.workspace_id])
    const projectId = typeof proposal.args.project_id === 'string' ? proposal.args.project_id : undefined
    if (projectId) queryKeys.push(['project-workflows', proposal.workspace_id, projectId])
  }
  queryKeys.push(['control-actions'])
  return queryKeys
}
