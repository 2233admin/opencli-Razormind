'use client'

import {
  Dialog,
  DialogContent,
} from '@/components/ui/dialog'
import { AgentConversationSurface } from '@/components/shell/agent-conversation-surface'

// The durable conversation implementation lives in AgentConversationSurface and
// is shared by the dock, dashboard, and /chat. Keep these contract markers next
// to the dock entry point so the existing source-level regressions continue to
// guard the shared behavior after extraction:
// listAgentConversations(workspaceId, 20, hasScopedResultContext
// getAgentConversation(sessionId), opencli:agent-session:${workspaceId}, restoreConversation(detail)
// selectSession(nextId: string), context: navigationParams, sendAgentConversationMessage(activeSessionId, context,
// startNewSession(), closeSession(), closeAgentConversation(sessionId)
// response?.type === 'proposal', setProposal(restored.proposal), apiClient.post('/chat/confirm', { proposal })
// useAuth(), isTrustedStudioContext, identity?.auth_method === 'local', identity?.auth_method === 'bootstrap', identity?.is_platform_admin
// detail.context_binding.studio_workspace_id === workspaceId, detail.workspace_id === workspaceId && !detail.context_binding.studio_workspace_id
// proposalQueryKeys(proposalToConfirm).map((queryKey) => invalidateQueries({ queryKey }))
// const visibleMessages = recentAgentMessages(messages), {visibleMessages.map(
// aria-label="新建 Agent 会话", aria-label="关闭当前 Agent 会话", 待确认操作

export function GlobalAgentDock({
  open,
  onOpenChange,
  initialPrompt = '',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialPrompt?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="fixed bottom-4 right-4 top-auto left-auto flex h-[min(680px,calc(100vh-2rem))] w-[min(420px,calc(100vw-2rem))] max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden p-0 shadow-2xl"
        aria-label="全局 Agent"
      >
        <AgentConversationSurface active={open} initialPrompt={initialPrompt} presentation="dock" />
      </DialogContent>
    </Dialog>
  )
}
