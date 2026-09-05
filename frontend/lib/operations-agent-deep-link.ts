export type OperationsAgentConfigTarget =
  | { kind: 'agent'; id: string; mode: 'contract' }
  | { kind: 'automation'; id: string; mode: 'binding' }

export function resolveOperationsAgentWorkspace(
  requestedWorkspaceId: string | null,
  currentWorkspaceId: string | null,
  accessibleWorkspaceIds: readonly string[],
): string | null {
  if (requestedWorkspaceId && accessibleWorkspaceIds.includes(requestedWorkspaceId)) {
    return requestedWorkspaceId
  }
  if (currentWorkspaceId && accessibleWorkspaceIds.includes(currentWorkspaceId)) {
    return currentWorkspaceId
  }
  return accessibleWorkspaceIds[0] ?? null
}

export function resolveOperationsAgentConfigTarget({
  activeWorkspaceId,
  requestedWorkspaceId,
  requestedAgentId,
  requestedAutomationId,
  configMode,
  accessibleAgentIds,
  accessibleAutomationIds,
}: {
  activeWorkspaceId: string | null
  requestedWorkspaceId: string | null
  requestedAgentId: string | null
  requestedAutomationId: string | null
  configMode: string | null
  accessibleAgentIds: readonly string[]
  accessibleAutomationIds: readonly string[]
}): OperationsAgentConfigTarget | null {
  if (!activeWorkspaceId || requestedWorkspaceId !== activeWorkspaceId) return null
  if (
    configMode === 'contract'
    && requestedAgentId
    && accessibleAgentIds.includes(requestedAgentId)
  ) {
    return { kind: 'agent', id: requestedAgentId, mode: 'contract' }
  }
  if (
    configMode === 'binding'
    && requestedAutomationId
    && accessibleAutomationIds.includes(requestedAutomationId)
  ) {
    return { kind: 'automation', id: requestedAutomationId, mode: 'binding' }
  }
  return null
}
