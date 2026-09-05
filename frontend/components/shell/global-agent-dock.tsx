'use client'

import { useQueryClient } from '@tanstack/react-query'
import { Bot, Check, Loader2, Plus, Send, ShieldCheck, X } from 'lucide-react'
import { usePathname, useSearchParams } from 'next/navigation'
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import {
  closeAgentConversation,
  createAgentConversation,
  getAgentConversation,
  listAgentConversations,
  sendAgentConversationMessage,
  type AgentConversation,
  type AgentConversationContext,
  type AgentConversationDetail,
  type AgentConversationProposal,
} from '@/lib/api/agent-conversations'
import { apiClient } from '@/lib/api/client'
import { proposalQueryKeys, recentAgentMessages } from '@/lib/agent-dock-state'
import { ROUTE_LABELS } from '@/lib/navigation'

type AgentMessage = {
  role: 'user' | 'assistant'
  content: string
}

type AgentProposal = AgentConversationProposal

type AgentReply = {
  type: 'message' | 'proposal'
  content?: string | null
  proposal?: AgentProposal | null
}

function restoreConversation(detail: AgentConversationDetail) {
  const restoredMessages: AgentMessage[] = []
  let restoredProposal: AgentProposal | null = null
  let restoredError: string | null = null

  for (const turn of [...detail.turns].sort((left, right) => left.sequence - right.sequence)) {
    if (turn.user_content) {
      restoredMessages.push({ role: 'user', content: turn.user_content })
    }
    const response = turn.response as AgentReply | null | undefined
    if (response?.type === 'proposal' && response.proposal) {
      restoredProposal = response.proposal
    } else if (response?.type === 'message') {
      const content = response.content?.trim()
      if (content) restoredMessages.push({ role: 'assistant', content })
    }
    if (turn.status === 'failed' && turn.error_message) {
      restoredError = turn.error_message
    }
  }

  return { messages: restoredMessages, proposal: restoredProposal, error: restoredError }
}


export function GlobalAgentDock({
  open,
  onOpenChange,
  initialPrompt = '',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialPrompt?: string
}) {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const navigationParams = new URLSearchParams(searchParams.toString())
  const workspaceId = navigationParams.get('workspace')
  const context: AgentConversationContext = {
    surface: ROUTE_LABELS[pathname] ?? pathname,
    project_id: navigationParams.get('project')
      ?? pathname.match(/^\/studio\/projects\/([^/]+)/)?.[1]
      ?? null,
    workflow_id: navigationParams.get('workflow'),
    run_id: navigationParams.get('run'),
    source_id: navigationParams.get('source')
      ?? pathname.match(/^\/sources\/([^/]+)/)?.[1]
      ?? null,
  }
  const storageKey = workspaceId ? `opencli:agent-session:${workspaceId}` : null
  const [sessions, setSessions] = useState<AgentConversation[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [input, setInput] = useState('')
  const [proposal, setProposal] = useState<AgentProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [closing, setClosing] = useState(false)
  const skipConversationLoadRef = useRef<string | null>(null)

  useEffect(() => {
    if (open && initialPrompt) setInput(initialPrompt)
  }, [initialPrompt, open])

  useEffect(() => {
    skipConversationLoadRef.current = null
    if (!open) return
    setLoadingSessions(false)
    setLoadingConversation(false)
    setLoadedWorkspaceId(null)
    setSessions([])
    setSessionId(null)
    setMessages([])
    setProposal(null)
    setError(null)
    if (!workspaceId || !storageKey) return

    let cancelled = false
    setLoadingSessions(true)
    void listAgentConversations(workspaceId)
      .then((nextSessions) => {
        if (cancelled) return
        const storedId = window.localStorage.getItem(storageKey)
        const storedSession = storedId
          ? nextSessions.find((session) => session.id === storedId)
          : undefined
        const selectedId = storedSession?.id ?? nextSessions[0]?.id ?? null
        setSessions(nextSessions)
        setLoadedWorkspaceId(workspaceId)
        setSessionId(selectedId)
        if (selectedId) window.localStorage.setItem(storageKey, selectedId)
        else window.localStorage.removeItem(storageKey)
      })
      .catch((reason) => {
        if (!cancelled) {
          setLoadedWorkspaceId(workspaceId)
          setError(reason instanceof Error ? reason.message : '会话列表暂时不可用')
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSessions(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, storageKey, workspaceId])

  useEffect(() => {
    if (!open || !workspaceId || !sessionId || loadedWorkspaceId !== workspaceId) return
    if (skipConversationLoadRef.current === sessionId) {
      skipConversationLoadRef.current = null
      return
    }
    let cancelled = false
    setLoadingConversation(true)
    setMessages([])
    setProposal(null)
    setError(null)
    void getAgentConversation(sessionId)
      .then((detail) => {
        if (cancelled) return
        const restored = restoreConversation(detail)
        setMessages(restored.messages)
        setProposal(restored.proposal)
        setError(restored.error)
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '会话恢复失败')
      })
      .finally(() => {
        if (!cancelled) setLoadingConversation(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadedWorkspaceId, open, sessionId, workspaceId])

  function startNewSession() {
    setSessionId(null)
    setMessages([])
    setProposal(null)
    setError(null)
    setInput('')
    if (storageKey) window.localStorage.removeItem(storageKey)
  }

  function selectSession(nextId: string) {
    setError(null)
    setSessionId(nextId || null)
    if (storageKey && nextId) window.localStorage.setItem(storageKey, nextId)
    else if (storageKey) window.localStorage.removeItem(storageKey)
  }

  async function closeSession() {
    if (!sessionId || closing) return
    setClosing(true)
    setError(null)
    try {
      await closeAgentConversation(sessionId)
      const nextSessions = sessions.map((session) => (
        session.id === sessionId ? { ...session, status: 'closed' as const } : session
      ))
      const nextSelected = nextSessions.find(
        (session) => session.id !== sessionId && session.status === 'active',
      )?.id ?? null
      setSessions(nextSessions)
      setSessionId(nextSelected)
      setMessages([])
      setProposal(null)
      if (storageKey && nextSelected) window.localStorage.setItem(storageKey, nextSelected)
      else if (storageKey) window.localStorage.removeItem(storageKey)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '关闭会话失败')
    } finally {
      setClosing(false)
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    const content = input.trim()
    if (!content || sending || proposal) return
    if (!workspaceId) {
      setError('当前 Workspace 不明确，无法保存 Agent 会话。请先选择一个 Workspace。')
      return
    }

    setInput('')
    setError(null)
    setSending(true)
    try {
      let activeSessionId = sessionId
      if (!activeSessionId) {
        const created = await createAgentConversation({
          workspace_id: workspaceId,
          title: 'Global Agent session',
          context,
        })
        activeSessionId = created.id
        setSessions((current) => [created, ...current.filter((session) => session.id !== created.id)])
        setLoadedWorkspaceId(workspaceId)
        skipConversationLoadRef.current = created.id
        setSessionId(created.id)
        if (storageKey) window.localStorage.setItem(storageKey, created.id)
      }

      const result = await sendAgentConversationMessage(activeSessionId, {
        request_id: crypto.randomUUID(),
        content,
        context,
      })
      setMessages((current) => [...current, { role: 'user', content }])
      if (result.turn.status === 'failed') {
        setError(result.turn.error_message ?? 'Agent 暂时不可用')
        return
      }
      const reply = result.turn.response as AgentReply | null | undefined
      if (reply?.type === 'proposal' && reply.proposal) {
        setProposal(reply.proposal)
      } else {
        setMessages((current) => [
          ...current,
          { role: 'assistant', content: reply?.content?.trim() || '没有返回内容。' },
        ])
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Agent 暂时不可用')
    } finally {
      setSending(false)
    }
  }

  async function confirmProposal() {
    if (!proposal || confirming) return
    setError(null)
    setConfirming(true)
    const proposalToConfirm = proposal
    try {
      await apiClient.post('/chat/confirm', { proposal })
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: `已执行：${proposal.summary}` },
      ])
      setProposal(null)
      await Promise.all(proposalQueryKeys(proposalToConfirm).map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ))
    } catch (reason) {
      const status = reason instanceof Error && 'status' in reason ? reason.status : undefined
      const message = reason instanceof Error ? reason.message : '操作执行失败'
      setError(
        status === 409
          ? `提案已失效或目标已变化：${message}。请拒绝后重新发起。`
          : message,
      )
    } finally {
      setConfirming(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void sendMessage()
  }

  const visibleMessages = recentAgentMessages(messages)
  const selectedSession = sessions.find((session) => session.id === sessionId)
  const canClose = Boolean(selectedSession?.status === 'active' && !sending && !confirming && !closing)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="fixed bottom-4 right-4 top-auto left-auto flex h-[min(680px,calc(100vh-2rem))] w-[min(420px,calc(100vw-2rem))] max-w-none translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden p-0 shadow-2xl"
        aria-label="全局 Agent"
      >
        <DialogHeader className="border-b px-4 py-3">
          <DialogTitle className="flex items-center gap-2">
            <Bot className="size-4 text-primary" aria-hidden />
            全局 Agent
          </DialogTitle>
          <DialogDescription>
            当前上下文：{ROUTE_LABELS[pathname] ?? pathname}。读取可直接执行，写入操作先生成确认提案。
            未明确指定 Workspace 时，仅在后端能解析出唯一授权范围时允许确认写操作。
          </DialogDescription>
          <div className="flex items-center gap-2">
            <select
              value={sessionId ?? ''}
              onChange={(event) => selectSession(event.target.value)}
              disabled={loadingSessions || sending || confirming || closing}
              aria-label="选择 Agent 会话"
              className="min-w-0 flex-1 rounded-xs border bg-background px-2 py-1 text-xs"
            >
              <option value="">新会话</option>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.title || `会话 ${session.id.slice(0, 8)}`}（{session.status === 'active' ? '进行中' : '已关闭'}）
                </option>
              ))}
            </select>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={startNewSession}
              disabled={sending || confirming || closing}
              aria-label="新建 Agent 会话"
            >
              <Plus aria-hidden />
              新建
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => void closeSession()}
              disabled={!canClose}
              aria-label="关闭当前 Agent 会话"
              title="关闭当前会话"
            >
              <X aria-hidden />
            </Button>
          </div>
        </DialogHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-4" aria-live="polite">
            {!workspaceId ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-4 text-xs" role="alert">
                当前 Workspace 不明确，暂不保存会话。请先从一个明确的 Workspace 页面打开 Agent。
              </div>
            ) : null}
            {loadingSessions || loadingConversation ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                {loadingSessions ? '正在加载会话' : '正在恢复会话'}
              </div>
            ) : null}
            {messages.length === 0 && !loadingConversation ? (
              <div className="rounded-md border border-dashed p-4">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <ShieldCheck className="size-4 text-success" aria-hidden />
                  所有页面共用一个操作入口
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  可以查询数据源、调度、任务和模型连接；涉及启停、触发或配置变更时会先展示差异。
                </p>
              </div>
            ) : null}
            {visibleMessages.map((message, index) => (
              <div
                key={`${message.role}-${index}`}
                className={message.role === 'user'
                  ? 'ml-8 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground'
                  : 'mr-8 rounded-md border bg-muted/30 px-3 py-2 text-sm'}
              >
                {message.content}
              </div>
            ))}
            {sending ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                Agent 正在处理
              </div>
            ) : null}
            {proposal ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3">
                <div className="text-sm font-medium">待确认操作</div>
                <p className="mt-1 text-xs text-muted-foreground">{proposal.summary}</p>
                <div className="mt-3 rounded-xs border bg-background/70 p-2 font-mono text-2xs">
                  {proposal.diff}
                </div>
                <div className="mt-2 space-y-1 font-mono text-3xs text-muted-foreground">
                  <div>工作项：{proposal.work_item_id ?? '未生成'}</div>
                  <div>工作区：{proposal.workspace_id ?? '未绑定'}</div>
                  <div>提案版本：{proposal.proposal_version ?? '未生成'}</div>
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={confirming}
                    onClick={() => setProposal(null)}
                  >
                    <X aria-hidden />
                    拒绝
                  </Button>
                  <Button
                    size="sm"
                    disabled={
                      confirming
                      || !proposal.work_item_id
                      || !proposal.workspace_id
                      || !proposal.proposal_version
                    }
                    onClick={() => void confirmProposal()}
                  >
                    {confirming ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
                    确认执行
                  </Button>
                </div>
              </div>
            ) : null}
            {error ? <p className="text-xs text-destructive" role="alert">{error}</p> : null}
          </div>
        </ScrollArea>

        <form className="border-t p-4" onSubmit={(event) => void sendMessage(event)}>
          <Textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="告诉 Agent 你要查询或执行什么…"
            aria-label="给全局 Agent 的消息"
            className="max-h-36 min-h-20 resize-none rounded-xs"
            disabled={sending || confirming || selectedSession?.status === 'closed'}
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="text-3xs text-muted-foreground">Enter 发送 · Shift+Enter 换行</span>
            <Button
              type="submit"
              size="sm"
              disabled={
                !input.trim()
                || sending
                || confirming
                || closing
                || Boolean(proposal)
                || selectedSession?.status === 'closed'
              }
            >
              {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
              发送
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
