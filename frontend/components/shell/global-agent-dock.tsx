'use client'

import { useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { Bot, Check, Loader2, Plus, Search, Send, ShieldCheck, X } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useAuth } from '@/components/auth/auth-provider'
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
import { useGovernedWorkspaces } from '@/lib/api/hooks'
import { buildRunUrl } from '@/lib/studio/run-navigation'

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

function matchesRequestedContext(detail: AgentConversationDetail, context: AgentConversationContext) {
  return (!context.project_id || detail.context_binding.project_id === context.project_id)
    && (!context.workflow_id || detail.context_binding.workflow_id === context.workflow_id)
    && (!context.run_id || detail.context_binding.run_id === context.run_id)
}

function matchesRequestedWorkspace(
  detail: AgentConversationDetail | AgentConversation,
  workspaceId: string,
  isStudioBridge: boolean,
) {
  return isStudioBridge
    ? detail.context_binding.studio_workspace_id === workspaceId
    : detail.workspace_id === workspaceId && !detail.context_binding.studio_workspace_id
}

function workflowDraftHref(
  workspaceId: string,
  projectId: string,
  workflowId: string,
  conversationId?: string | null,
) {
  const query = new URLSearchParams({
    workspace: workspaceId,
    project: projectId,
    workflow: workflowId,
  })
  if (conversationId) query.set('conversation', conversationId)
  return `/studio/workflow?${query.toString()}`
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
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const { identity } = useAuth()
  const navigationQuery = searchParams.toString()
  const navigationParams = useMemo(() => new URLSearchParams(navigationQuery), [navigationQuery])
  const authorizedWorkspaces = useGovernedWorkspaces()
  const requestedWorkspaceId = navigationParams.get('workspace')
  const requestedConversationId = navigationParams.get('conversation')
  const [preferredWorkspaceId, setPreferredWorkspaceId] = useState<string | null>(null)
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null)
  const validPreferredWorkspaceId = authorizedWorkspaces.data?.some((workspace) => workspace.id === preferredWorkspaceId) ? preferredWorkspaceId : null
  const workspaceId = requestedWorkspaceId
    ?? selectedWorkspaceId
    ?? (authorizedWorkspaces.data?.length === 1 ? authorizedWorkspaces.data[0].id : null)
    ?? validPreferredWorkspaceId
  const authorizedWorkspace = workspaceId
    ? authorizedWorkspaces.data?.find((workspace) => workspace.id === workspaceId)
    : undefined
  const context: AgentConversationContext = useMemo(() => ({
    surface: ROUTE_LABELS[pathname] ?? pathname,
    project_id: navigationParams.get('project')
      ?? pathname.match(/^\/studio\/projects\/([^/]+)/)?.[1]
      ?? null,
    workflow_id: navigationParams.get('workflow'),
    run_id: navigationParams.get('run'),
    source_id: navigationParams.get('source')
      ?? pathname.match(/^\/sources\/([^/]+)/)?.[1]
      ?? null,
  }), [navigationParams, pathname])
  const hasScopedResultContext = Boolean(context.project_id || context.workflow_id || context.run_id)
  const isTrustedStudioContext = Boolean(
    requestedWorkspaceId
      && hasScopedResultContext
      && (identity?.auth_method === 'local' || identity?.auth_method === 'bootstrap')
      && identity?.is_platform_admin,
  )
  const studioStorageScopeReady = Boolean(
    isTrustedStudioContext
      && authorizedWorkspaces.isSuccess
      && authorizedWorkspaces.data?.length === 1,
  )
  const workspaceUsable = Boolean(authorizedWorkspace || studioStorageScopeReady)
  const workspaceScopeError = requestedWorkspaceId && authorizedWorkspaces.isSuccess && !workspaceUsable
    ? isTrustedStudioContext
      ? '当前项目 Agent 需要唯一的受管工作区，暂时无法保存会话。'
      : '该 Workspace 不在你的授权范围内。请选择一个可用 Workspace 后再继续。'
    : null
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
  const [sessionSearch, setSessionSearch] = useState('')
  const [completedResultHref, setCompletedResultHref] = useState<string | null>(null)
  const skipConversationLoadRef = useRef<string | null>(null)
  const activeWorkspaceRef = useRef<string | null>(null)
  const requestGenerationRef = useRef(0)

  useEffect(() => {
    setPreferredWorkspaceId(window.localStorage.getItem('opencli:agent-workspace'))
  }, [])

  useEffect(() => {
    if (!workspaceId || !workspaceUsable) return
    window.localStorage.setItem('opencli:agent-workspace', workspaceId)
  }, [workspaceId, workspaceUsable])

  useEffect(() => {
    activeWorkspaceRef.current = workspaceId
  }, [workspaceId])

  useEffect(() => {
    requestGenerationRef.current += 1
    setSending(false)
    setConfirming(false)
    setClosing(false)
  }, [workspaceId, context.project_id, context.workflow_id, context.run_id])

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
    setCompletedResultHref(null)
    setError(null)
    if (!workspaceId || !storageKey || !workspaceUsable || workspaceScopeError) return

    let cancelled = false
    setLoadingSessions(true)
    void listAgentConversations(workspaceId, 20, hasScopedResultContext ? {
      project_id: context.project_id,
      workflow_id: context.workflow_id,
      run_id: context.run_id,
    } : undefined)
      .then((nextSessions) => {
        if (cancelled) return
        const storedId = window.localStorage.getItem(storageKey)
        const requestedSession = requestedConversationId ? nextSessions.find((session) => session.id === requestedConversationId) : undefined
        const storedSession = storedId ? nextSessions.find((session) => session.id === storedId) : undefined
        const storedMatchesContext = storedSession && (!hasScopedResultContext || (
          (!context.project_id || storedSession.context_binding.project_id === context.project_id)
          && (!context.workflow_id || storedSession.context_binding.workflow_id === context.workflow_id)
          && (!context.run_id || storedSession.context_binding.run_id === context.run_id)
        ))
        const selectedId = requestedConversationId
          ? requestedSession?.id ?? null
          : storedMatchesContext ? storedSession.id : null
        setSessions(nextSessions)
        setLoadedWorkspaceId(workspaceId)
        setSessionId(selectedId)
        if (requestedConversationId && !requestedSession) {
          void getAgentConversation(requestedConversationId)
            .then((detail) => {
              if (cancelled || activeWorkspaceRef.current !== workspaceId) return
              if (!matchesRequestedWorkspace(detail, workspaceId, isTrustedStudioContext) || !matchesRequestedContext(detail, context)) {
                setError('指定的 Agent 会话与当前 Workspace 或结果上下文不匹配。没有打开其他会话。')
                return
              }
              setSessions((current) => [detail, ...current.filter((session) => session.id !== detail.id)])
              setSessionId(detail.id)
              window.localStorage.setItem(storageKey, detail.id)
            })
            .catch(() => {
              if (!cancelled && activeWorkspaceRef.current === workspaceId) setError('指定的 Agent 会话不存在，或你无权访问。没有打开其他会话。')
            })
        } else if (requestedConversationId && requestedSession && !matchesRequestedWorkspace(requestedSession, workspaceId, isTrustedStudioContext)) {
          setSessionId(null)
          setError('指定的 Agent 会话不属于当前 Workspace。没有打开其他会话。')
        } else if (selectedId) window.localStorage.setItem(storageKey, selectedId)
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
  }, [context, hasScopedResultContext, isTrustedStudioContext, open, requestedConversationId, storageKey, workspaceId, workspaceScopeError, workspaceUsable])

  useEffect(() => {
    if (!open || !workspaceId || !sessionId || loadedWorkspaceId !== workspaceId || !workspaceUsable || workspaceScopeError) return
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
        if (cancelled || activeWorkspaceRef.current !== workspaceId) return
        if (!matchesRequestedWorkspace(detail, workspaceId, isTrustedStudioContext) || (requestedConversationId && !matchesRequestedContext(detail, context))) {
          setSessionId(null)
          setError('指定的 Agent 会话与当前 Workspace 或结果上下文不匹配，无法恢复。')
          return
        }
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
  }, [context, isTrustedStudioContext, loadedWorkspaceId, open, requestedConversationId, sessionId, workspaceId, workspaceScopeError, workspaceUsable])

  function startNewSession() {
    requestGenerationRef.current += 1
    setSessionId(null)
    setMessages([])
    setProposal(null)
    setError(null)
    setInput('')
    if (storageKey) window.localStorage.removeItem(storageKey)
  }

  function selectWorkspace(nextId: string) {
    if (!authorizedWorkspaces.data?.some((workspace) => workspace.id === nextId)) return
    const nextParams = new URLSearchParams()
    nextParams.set('workspace', nextId)
    nextParams.set('agent', '1')
    router.replace(`/studio?${nextParams.toString()}`)
    setSelectedWorkspaceId(nextId)
    requestGenerationRef.current += 1
    setSessionId(null)
    setMessages([])
    setProposal(null)
    setError(null)
  }

  function selectSession(nextId: string) {
    requestGenerationRef.current += 1
    setError(null)
    setSessionId(nextId || null)
    if (storageKey && nextId) window.localStorage.setItem(storageKey, nextId)
    else if (storageKey) window.localStorage.removeItem(storageKey)
  }

  async function closeSession() {
    if (!sessionId || closing) return
    const requestGeneration = requestGenerationRef.current
    setClosing(true)
    setError(null)
    try {
      await closeAgentConversation(sessionId)
      if (requestGenerationRef.current !== requestGeneration) return
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
      if (requestGenerationRef.current !== requestGeneration) return
      setError(reason instanceof Error ? reason.message : '关闭会话失败')
    } finally {
      if (requestGenerationRef.current === requestGeneration) setClosing(false)
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    const content = input.trim()
    if (!content || sending || proposal) return
    if (!workspaceId || !workspaceUsable || workspaceScopeError) {
      setError('当前 Workspace 不明确，无法保存 Agent 会话。请先选择一个 Workspace。')
      return
    }

    setInput('')
    setError(null)
    setSending(true)
    const requestGeneration = requestGenerationRef.current
    try {
      const requestWorkspaceId = workspaceId
      let activeSessionId = sessionId
      const messageContext = hasScopedResultContext ? context : selectedSession?.context_binding ?? context
      if (!activeSessionId) {
        const created = await createAgentConversation({
          workspace_id: workspaceId,
          title: 'Global Agent session',
          context: messageContext,
        })
        if (activeWorkspaceRef.current !== requestWorkspaceId || requestGenerationRef.current !== requestGeneration) return
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
        context: messageContext,
      })
      if (activeWorkspaceRef.current !== requestWorkspaceId || requestGenerationRef.current !== requestGeneration) return
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
      if (requestGenerationRef.current !== requestGeneration) return
      setError(reason instanceof Error ? reason.message : 'Agent 暂时不可用')
    } finally {
      if (requestGenerationRef.current === requestGeneration) setSending(false)
    }
  }

  async function confirmProposal() {
    if (!proposal || confirming) return
    setError(null)
    setConfirming(true)
    const proposalToConfirm = proposal
    const requestGeneration = requestGenerationRef.current
    try {
      const requestWorkspaceId = workspaceId
      const confirmation = await apiClient.post('/chat/confirm', { proposal })
      if (activeWorkspaceRef.current !== requestWorkspaceId || requestGenerationRef.current !== requestGeneration) return
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: `已执行：${proposal.summary}` },
      ])
      const result = confirmation.data?.data as Record<string, unknown> | undefined
      const resultProjectId = typeof result?.project_id === 'string' ? result.project_id : typeof proposal.args.project_id === 'string' ? proposal.args.project_id : null
      const resultWorkflowId = typeof result?.workflow_id === 'string' ? result.workflow_id : typeof proposal.args.workflow_id === 'string' ? proposal.args.workflow_id : null
      const resultWorkspaceId = typeof result?.workspace_id === 'string' ? result.workspace_id : proposal.workspace_id ?? workspaceId
      if (resultWorkspaceId && resultProjectId && resultWorkflowId && (proposal.tool === 'create_project' || proposal.tool === 'update_workflow_draft')) {
        await queryClient.invalidateQueries({ queryKey: ['workspace-projects', resultWorkspaceId] })
        await queryClient.invalidateQueries({ queryKey: ['project-workflows', resultWorkspaceId, resultProjectId] })
        if (requestGenerationRef.current !== requestGeneration) return
        setCompletedResultHref(workflowDraftHref(
          resultWorkspaceId,
          resultProjectId,
          resultWorkflowId,
          sessionId,
        ))
      }
      setProposal(null)
      await Promise.all(proposalQueryKeys(proposalToConfirm).map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ))
      if (sessionId && requestGenerationRef.current === requestGeneration) {
        const updated = await getAgentConversation(sessionId)
        if (requestGenerationRef.current === requestGeneration) {
          setSessions((current) => [updated, ...current.filter((session) => session.id !== updated.id)])
        }
      }
    } catch (reason) {
      if (requestGenerationRef.current !== requestGeneration) return
      const status = reason instanceof Error && 'status' in reason ? reason.status : undefined
      const message = reason instanceof Error ? reason.message : '操作执行失败'
      setError(
        status === 409
          ? `提案已失效或目标已变化：${message}。请拒绝后重新发起。`
          : message,
      )
    } finally {
      if (requestGenerationRef.current === requestGeneration) setConfirming(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void sendMessage()
  }

  const visibleMessages = recentAgentMessages(messages)
  const selectedSession = sessions.find((session) => session.id === sessionId)
  const visibleSessions = sessions.filter((session) => {
    const query = sessionSearch.trim().toLowerCase()
    return !query || `${session.title ?? ''} ${session.id} ${session.context_binding.project_id ?? ''} ${session.context_binding.workflow_id ?? ''}`.toLowerCase().includes(query)
  })
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
          {authorizedWorkspaces.data && (authorizedWorkspaces.data.length > 1 || workspaceScopeError) ? (
            <label className="mt-2 block text-xs font-medium">
              Workspace
              <select
                value={workspaceId ?? ''}
                onChange={(event) => selectWorkspace(event.target.value)}
                className="mt-1 min-h-11 w-full rounded-xs border bg-background px-2 text-sm font-normal"
                aria-label="选择 Agent Workspace"
              >
                <option value="">选择 Workspace</option>
                {authorizedWorkspaces.data.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
              </select>
            </label>
          ) : null}
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <label className="sr-only" htmlFor="agent-session-search">搜索 Agent 会话</label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-2 size-3.5 text-muted-foreground" aria-hidden />
                <input id="agent-session-search" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder="搜索会话、项目或工作流" className="min-h-9 w-full rounded-xs border bg-background py-1 pl-7 pr-2 text-xs" />
              </div>
              <select
                value={sessionId ?? ''}
                onChange={(event) => selectSession(event.target.value)}
                disabled={loadingSessions || sending || confirming || closing || !workspaceId}
                aria-label="选择 Agent 会话"
                className="mt-1 min-h-9 w-full rounded-xs border bg-background px-2 text-xs"
              >
                <option value="">新会话</option>
                {visibleSessions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title || `会话 ${session.id.slice(0, 8)}`} · {session.context_binding.project_id ? `项目 ${session.context_binding.project_id.slice(0, 8)}` : '无项目'}（{session.status === 'active' ? '进行中' : '已关闭'}）
                  </option>
                ))}
              </select>
            </div>
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
            {workspaceScopeError ? <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-xs" role="alert">{workspaceScopeError}</div> : null}
            {!workspaceId && !workspaceScopeError ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-4 text-xs" role="alert">
                当前 Workspace 不明确。请在这里选择一个已授权 Workspace；不会自动跨范围打开或创建会话。
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
            {selectedSession?.context_binding && (selectedSession.context_binding.project_id || selectedSession.context_binding.workflow_id || selectedSession.context_binding.run_id) ? (
              <nav className="flex flex-wrap gap-2 border-t pt-3 text-xs" aria-label="会话上下文链接">
                {selectedSession.context_binding.project_id ? <Link className="underline underline-offset-4" href={`/studio/projects/${selectedSession.context_binding.project_id}?workspace=${workspaceId}`}>项目</Link> : null}
                {selectedSession.context_binding.workflow_id && selectedSession.context_binding.project_id && workspaceId ? <Link className="underline underline-offset-4" href={workflowDraftHref(workspaceId, selectedSession.context_binding.project_id, selectedSession.context_binding.workflow_id, selectedSession.id)}>工作流草稿</Link> : null}
                {selectedSession.context_binding.run_id ? <Link className="underline underline-offset-4" href={buildRunUrl('operations', { workspace: workspaceId ?? undefined, project: selectedSession.context_binding.project_id ?? undefined, workflow: selectedSession.context_binding.workflow_id ?? undefined, run: selectedSession.context_binding.run_id }) ?? '/studio'}>运行</Link> : null}
              </nav>
            ) : null}
            {completedResultHref ? <Link href={completedResultHref} className="inline-flex min-h-11 items-center text-xs font-medium underline underline-offset-4">打开 Agent 保存的工作流草稿</Link> : null}
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
                    暂不执行
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
