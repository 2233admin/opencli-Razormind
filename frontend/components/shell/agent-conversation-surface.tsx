'use client'

import { useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { Bot, Check, ChevronDown, Loader2, PanelLeftClose, PanelLeftOpen, Plus, Search, Send, ShieldCheck, Wrench, X } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useAuth } from '@/components/auth/auth-provider'
import { Textarea } from '@/components/ui/textarea'
import {
  closeAgentConversation,
  createAgentConversation,
  getAgentConversation,
  listAgentConversationEvents,
  listAgentExecutionTargets,
  listAgentConversations,
  reopenAgentConversation,
  sendAgentConversationMessage,
  type AgentConversation,
  type AgentConversationContext,
  type AgentConversationDetail,
  type AgentConversationProposal,
} from '@/lib/api/agent-conversations'
import { apiClient } from '@/lib/api/client'
import { proposalQueryKeys, recentAgentMessages } from '@/lib/agent-dock-state'
import { ROUTE_LABELS } from '@/lib/navigation'
import { useGovernedWorkspaces, useProviders } from '@/lib/api/hooks'
import { buildRunUrl } from '@/lib/studio/run-navigation'

type AgentMessage = {
  role: 'user' | 'assistant'
  content: string
  sequence?: number
}

type AgentToolTrace = {
  sequence: number
  entries: Array<Record<string, unknown>>
}

type BackgroundActivity = { turnId: string; sequence: number; type: string; payload: Record<string, unknown> }

type AgentProposal = AgentConversationProposal

type AgentReply = {
  type: 'message' | 'proposal'
  content?: string | null
  proposal?: AgentProposal | null
}

function restoreConversation(detail: AgentConversationDetail) {
  const restoredMessages: AgentMessage[] = []
  const restoredToolTraces: AgentToolTrace[] = []
  let restoredProposal: AgentProposal | null = null
  let restoredError: string | null = null

  for (const turn of [...detail.turns].sort((left, right) => left.sequence - right.sequence)) {
    if (turn.user_content) {
      restoredMessages.push({ role: 'user', content: turn.user_content, sequence: turn.sequence })
    }
    const response = turn.response as AgentReply | null | undefined
    if (response?.type === 'proposal' && response.proposal) {
      restoredProposal = response.proposal
    } else if (response?.type === 'message') {
      const content = response.content?.trim()
      if (content) restoredMessages.push({ role: 'assistant', content, sequence: turn.sequence })
    }
    if (turn.status === 'failed' && turn.error_message) {
      restoredError = turn.error_message
    }
    if (turn.status === 'interrupted') restoredError = turn.error_message ?? '该次执行已中断。'
    if (turn.tool_trace.length > 0) {
      restoredToolTraces.push({ sequence: turn.sequence, entries: turn.tool_trace })
    }
  }

  return { messages: restoredMessages, toolTraces: restoredToolTraces, proposal: restoredProposal, error: restoredError }
}

function traceLabel(entry: Record<string, unknown>, index: number) {
  const value = entry.tool ?? entry.name ?? entry.action ?? entry.type
  return typeof value === 'string' && value.trim() ? value : `工具步骤 ${index + 1}`
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
  includeProjectSessions = false,
) {
  return isStudioBridge
    ? detail.context_binding.studio_workspace_id === workspaceId
    : detail.workspace_id === workspaceId && (includeProjectSessions || !detail.context_binding.studio_workspace_id)
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


export type AgentConversationSurfaceProps = {
  active?: boolean
  initialPrompt?: string
  presentation?: 'page' | 'dashboard' | 'dock'
  onActiveConversationChange?: (conversation: AgentConversationDetail | AgentConversation | null) => void
}

export function AgentConversationSurface({
  active = true,
  initialPrompt = '',
  presentation = 'page',
  onActiveConversationChange,
}: AgentConversationSurfaceProps) {
  const open = active
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const { identity } = useAuth()
  const providersQuery = useProviders()
  const providers = providersQuery.data?.data ?? []
  const modelConfigured = providers.some((provider) => provider.enabled && Boolean(provider.default_model))
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
      ? '当前项目 Agent 需要唯一的授权工作区，暂时无法保存会话。'
      : '该工作区不在你的授权范围内。请选择一个可用工作区后再继续。'
    : null
  const storageKey = workspaceId ? `opencli:agent-session:${workspaceId}` : null
  const executionTargetsQuery = useQuery({
    queryKey: ['agent-execution-targets', workspaceId, context.project_id, context.workflow_id, context.run_id],
    queryFn: () => listAgentExecutionTargets(workspaceId as string, context),
    enabled: presentation === 'page' && Boolean(workspaceId && workspaceUsable),
  })
  const [sessions, setSessions] = useState<AgentConversation[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [toolTraces, setToolTraces] = useState<AgentToolTrace[]>([])
  const [input, setInput] = useState('')
  const [proposal, setProposal] = useState<AgentProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [closing, setClosing] = useState(false)
  const [sessionSearch, setSessionSearch] = useState('')
  const [executionTargetId, setExecutionTargetId] = useState<string>('')
  const [modelId, setModelId] = useState<string>('')
  const [executionMode, setExecutionMode] = useState<'synchronous' | 'background'>('synchronous')
  const [railOpen, setRailOpen] = useState(true)
  const [backgroundTurn, setBackgroundTurn] = useState<{ conversationId: string; turnId: string; cursor: number } | null>(null)
  const [backgroundActivities, setBackgroundActivities] = useState<BackgroundActivity[]>([])
  const [completedResultHref, setCompletedResultHref] = useState<string | null>(null)
  const skipConversationLoadRef = useRef<string | null>(null)
  const activeWorkspaceRef = useRef<string | null>(null)
  const requestGenerationRef = useRef(0)

  const executionTargets = useMemo(() => executionTargetsQuery.data?.targets ?? [], [executionTargetsQuery.data?.targets])
  const selectedExecutionTarget = executionTargets.find((target) => target.id === executionTargetId) ?? null
  const activeExecutionBinding = sessions.find((session) => session.id === sessionId)?.execution_binding ?? null
  const boundExecutionTarget = activeExecutionBinding?.target_id
    ? executionTargets.find((target) => target.id === activeExecutionBinding.target_id) ?? null
    : null

  useEffect(() => {
    setPreferredWorkspaceId(window.localStorage.getItem('opencli:agent-workspace'))
  }, [])

  useEffect(() => {
    if (!workspaceId || !workspaceUsable) return
    window.localStorage.setItem('opencli:agent-workspace', workspaceId)
  }, [workspaceId, workspaceUsable])

  useEffect(() => {
    setExecutionTargetId('')
    setModelId('')
  }, [workspaceId])

  useEffect(() => {
    if (executionTargetId || !executionTargets.length) return
    const ready = executionTargets.find((target) => target.readiness.status === 'ready')
    const fallback = ready ?? executionTargets[0]
    if (fallback) {
      setExecutionTargetId(fallback.id)
      setModelId(fallback.default_model_id ?? fallback.models[0]?.id ?? '')
    }
  }, [executionTargetId, executionTargets])

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
    setToolTraces([])
    setBackgroundTurn(null)
    setBackgroundActivities([])
    setProposal(null)
    setCompletedResultHref(null)
    setError(null)
    onActiveConversationChange?.(null)
    if (!workspaceId || !storageKey || !workspaceUsable || workspaceScopeError) return

    let cancelled = false
    setLoadingSessions(true)
    void listAgentConversations(workspaceId, 20, hasScopedResultContext ? {
      project_id: context.project_id,
      workflow_id: context.workflow_id,
      run_id: context.run_id,
    } : undefined, presentation === 'page')
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
              if (!matchesRequestedWorkspace(detail, workspaceId, isTrustedStudioContext, presentation === 'page') || !matchesRequestedContext(detail, context)) {
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
        } else if (requestedConversationId && requestedSession && !matchesRequestedWorkspace(requestedSession, workspaceId, isTrustedStudioContext, presentation === 'page')) {
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
  }, [context, hasScopedResultContext, isTrustedStudioContext, onActiveConversationChange, open, presentation, requestedConversationId, storageKey, workspaceId, workspaceScopeError, workspaceUsable])

  useEffect(() => {
    if (!open || !workspaceId || !sessionId || loadedWorkspaceId !== workspaceId || !workspaceUsable || workspaceScopeError) return
    if (skipConversationLoadRef.current === sessionId) {
      skipConversationLoadRef.current = null
      return
    }
    let cancelled = false
    setLoadingConversation(true)
    setMessages([])
    setToolTraces([])
    setBackgroundTurn(null)
    setBackgroundActivities([])
    setProposal(null)
    setError(null)
    void getAgentConversation(sessionId)
      .then((detail) => {
        if (cancelled || activeWorkspaceRef.current !== workspaceId) return
        if (!matchesRequestedWorkspace(detail, workspaceId, isTrustedStudioContext, presentation === 'page') || (requestedConversationId && !matchesRequestedContext(detail, context))) {
          setSessionId(null)
          setError('指定的 Agent 会话与当前 Workspace 或结果上下文不匹配，无法恢复。')
          return
        }
        const restored = restoreConversation(detail)
        setMessages(restored.messages)
        setToolTraces(restored.toolTraces)
        setProposal(restored.proposal)
        setError(restored.error)
        onActiveConversationChange?.(detail)
        const runningTurn = detail.turns.find((turn) => (turn.status === 'queued' || turn.status === 'running') && turn.agent_run_id)
        setBackgroundTurn(runningTurn ? { conversationId: detail.id, turnId: runningTurn.id, cursor: 0 } : null)
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
  }, [context, isTrustedStudioContext, loadedWorkspaceId, onActiveConversationChange, open, presentation, requestedConversationId, sessionId, workspaceId, workspaceScopeError, workspaceUsable])

  useEffect(() => {
    if (!backgroundTurn) return
    let cancelled = false
    const generation = requestGenerationRef.current
    const activeTurn = backgroundTurn
    let cursor = activeTurn.cursor
    let timer: number | undefined
    let failures = 0
    const poll = async () => {
      try {
        const snapshot = await listAgentConversationEvents(activeTurn.conversationId, activeTurn.turnId, cursor)
        if (cancelled || requestGenerationRef.current !== generation || sessionId !== activeTurn.conversationId) return
        cursor = Math.max(cursor, snapshot.next_sequence)
        if (failures > 0) setError(null)
        failures = 0
        setBackgroundActivities((current) => {
          const retained = current.filter((activity) => activity.turnId !== activeTurn.turnId)
          const known = new Set(current.filter((activity) => activity.turnId === activeTurn.turnId).map((activity) => activity.sequence))
          const additions = snapshot.events
            .filter((event) => !known.has(event.sequence))
            .map((event) => ({ turnId: activeTurn.turnId, sequence: event.sequence, type: event.type, payload: event.payload }))
          return [...retained, ...current.filter((activity) => activity.turnId === activeTurn.turnId), ...additions]
            .sort((left, right) => left.sequence - right.sequence)
        })
        if (snapshot.terminal) {
          const detail = await getAgentConversation(activeTurn.conversationId)
          if (cancelled || requestGenerationRef.current !== generation || sessionId !== activeTurn.conversationId) return
          const restored = restoreConversation(detail)
          setMessages(restored.messages)
          setToolTraces(restored.toolTraces)
          setProposal(restored.proposal)
          setError(restored.error)
          setSessions((current) => [detail, ...current.filter((session) => session.id !== detail.id)])
          onActiveConversationChange?.(detail)
          setBackgroundActivities([])
          setBackgroundTurn(null)
          return
        }
        timer = window.setTimeout(() => void poll(), 1200)
      } catch (reason) {
        if (cancelled || requestGenerationRef.current !== generation) return
        setError(reason instanceof Error ? reason.message : '后台执行状态读取失败')
        const status = reason instanceof Error && 'status' in reason ? reason.status : undefined
        if (status === 401 || status === 403 || status === 404) return
        failures += 1
        timer = window.setTimeout(() => void poll(), Math.min(1200 * 2 ** failures, 10000))
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [backgroundTurn, onActiveConversationChange, sessionId])

  function startNewSession() {
    requestGenerationRef.current += 1
    setSessionId(null)
    setMessages([])
    setToolTraces([])
    setBackgroundTurn(null)
    setBackgroundActivities([])
    setProposal(null)
    setError(null)
    setInput('')
    if (storageKey) window.localStorage.removeItem(storageKey)
    onActiveConversationChange?.(null)
  }

  function selectWorkspace(nextId: string) {
    if (!authorizedWorkspaces.data?.some((workspace) => workspace.id === nextId)) return
    const nextParams = new URLSearchParams(navigationParams.toString())
    nextParams.set('workspace', nextId)
    nextParams.delete('conversation')
    if (presentation === 'dock') {
      nextParams.set('agent', '1')
    } else {
      nextParams.delete('agent')
    }
    const targetPath = presentation === 'page' ? '/chat' : presentation === 'dashboard' ? '/dashboard' : pathname || '/studio'
    router.replace(`${targetPath}?${nextParams.toString()}`)
    setSelectedWorkspaceId(nextId)
    requestGenerationRef.current += 1
    setSessionId(null)
    setMessages([])
    setToolTraces([])
    setBackgroundTurn(null)
    setBackgroundActivities([])
    setProposal(null)
    setError(null)
    onActiveConversationChange?.(null)
  }

  function selectSession(nextId: string) {
    requestGenerationRef.current += 1
    setError(null)
    setSessionId(nextId || null)
    setBackgroundTurn(null)
    setBackgroundActivities([])
    onActiveConversationChange?.(sessions.find((session) => session.id === nextId) ?? null)
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

  async function reopenSession() {
    if (!sessionId || closing || selectedSession?.status !== 'closed') return
    const requestGeneration = requestGenerationRef.current
    setClosing(true)
    setError(null)
    try {
      const reopened = await reopenAgentConversation(sessionId)
      if (requestGenerationRef.current !== requestGeneration) return
      setSessions((current) => current.map((session) => session.id === reopened.id ? reopened : session))
    } catch (reason) {
      if (requestGenerationRef.current === requestGeneration) setError(reason instanceof Error ? reason.message : '重新打开会话失败')
    } finally {
      if (requestGenerationRef.current === requestGeneration) setClosing(false)
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    const content = input.trim()
    if (!content || sending || proposal) return
    if (!workspaceId || !workspaceUsable || workspaceScopeError) {
      setError('当前工作区不明确，无法保存 Agent 会话。请先选择一个工作区。')
      return
    }
    if (presentation !== 'page' && providersQuery.isSuccess && !modelConfigured) {
      setError('尚未配置可用模型连接。请先打开模型与连接完成配置。')
      return
    }
    if (presentation === 'page' && !sessionId && (!selectedExecutionTarget || selectedExecutionTarget.readiness.status === 'blocked')) {
      setError(selectedExecutionTarget?.readiness.reason ?? '请先选择状态就绪的执行配置。')
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
          title: content.slice(0, 80),
          context: messageContext,
          execution_target_id: presentation === 'page' ? executionTargetId || null : null,
          model_id: presentation === 'page' ? modelId || null : null,
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
        context: presentation === 'page' ? undefined : messageContext,
        execution_mode: presentation === 'page' ? executionMode : 'synchronous',
      })
      if (activeWorkspaceRef.current !== requestWorkspaceId || requestGenerationRef.current !== requestGeneration) return
      setMessages((current) => [...current, { role: 'user', content, sequence: result.turn.sequence }])
      if (result.turn.status === 'failed') {
        setError(result.turn.error_message ?? 'Agent 暂时不可用')
        return
      }
      const reply = result.turn.response as AgentReply | null | undefined
      if (result.turn.tool_trace.length > 0) {
        setToolTraces((current) => [...current, { sequence: result.turn.sequence, entries: result.turn.tool_trace }])
      }
      if (result.run && executionMode === 'background') {
        setBackgroundTurn({ conversationId: result.conversation_id, turnId: result.turn.id, cursor: 0 })
        return
      }
      if (reply?.type === 'proposal' && reply.proposal) {
        setProposal(reply.proposal)
      } else {
        setMessages((current) => [
          ...current,
          { role: 'assistant', content: reply?.content?.trim() || '没有返回内容。', sequence: result.turn.sequence },
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
      const resultWorkspaceId = typeof result?.studio_workspace_id === 'string' ? result.studio_workspace_id : typeof result?.workspace_id === 'string' ? result.workspace_id : workspaceId
      if (resultWorkspaceId && resultProjectId && resultWorkflowId && ['create_project', 'update_workflow_draft', 'validate_workflow_draft', 'publish_workflow', 'run_managed_doubao_question'].includes(proposal.tool)) {
        await queryClient.invalidateQueries({ queryKey: ['workspace-projects', resultWorkspaceId] })
        await queryClient.invalidateQueries({ queryKey: ['project-workflows', resultWorkspaceId, resultProjectId] })
        if (requestGenerationRef.current !== requestGeneration) return
        const resultRunId = typeof result?.run_id === 'string' ? result.run_id : null
        setCompletedResultHref(resultRunId ? buildRunUrl('operations', {
          workspace: resultWorkspaceId,
          project: resultProjectId,
          workflow: resultWorkflowId,
          run: resultRunId,
          conversation: sessionId ?? undefined,
        }) : workflowDraftHref(resultWorkspaceId, resultProjectId, resultWorkflowId, sessionId))
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
  const canClose = Boolean(selectedSession?.status === 'active' && !sending && !confirming && !closing && !backgroundTurn)
  const canReopen = Boolean(selectedSession?.status === 'closed' && !sending && !confirming && !closing)

  return (
    <div
      className={presentation === 'dock'
        ? 'flex h-full min-h-0 flex-col overflow-hidden'
        : presentation === 'dashboard'
          ? 'flex min-h-[600px] flex-col overflow-hidden rounded-xl border bg-card shadow-sm'
        : railOpen ? 'grid min-h-[680px] overflow-hidden rounded-xl border bg-card shadow-sm lg:grid-cols-[240px_minmax(0,1fr)]' : 'grid min-h-[680px] overflow-hidden rounded-xl border bg-card shadow-sm'}
      aria-label={presentation === 'dock' ? '全局 Agent' : 'Ask Alice 主对话'}
    >
        <div className={presentation === 'page' ? `border-b p-3 lg:border-b-0 lg:border-r ${railOpen ? '' : 'hidden lg:hidden'}` : 'border-b px-4 py-3'}>
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Bot className="size-4 text-primary" aria-hidden />
            {presentation === 'dock' ? '全局 Agent' : presentation === 'page' ? '会话' : 'Ask Alice'}
          </h2>
          {presentation !== 'page' ? <p className="mt-1 text-xs leading-5 text-muted-foreground">
            当前上下文：{ROUTE_LABELS[pathname] ?? pathname}。读取可直接执行，写入操作先生成确认提案。
            未明确工作区时，仅在后端能解析出唯一授权范围时允许确认写操作。
          </p> : null}
          {authorizedWorkspaces.data && (presentation !== 'dock' || authorizedWorkspaces.data.length > 1 || workspaceScopeError) ? (
            <label className="mt-2 block text-xs font-medium">
              当前工作区
              <select
                value={workspaceId ?? ''}
                onChange={(event) => selectWorkspace(event.target.value)}
                className="mt-1 min-h-11 w-full rounded-xs border bg-background px-2 text-sm font-normal"
                aria-label="选择 Agent Workspace"
              >
                <option value="">选择工作区</option>
                {workspaceId && !authorizedWorkspace && isTrustedStudioContext ? <option value={workspaceId}>当前项目工作区</option> : null}
                {authorizedWorkspaces.data.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
              </select>
            </label>
          ) : null}
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <label className="sr-only" htmlFor="agent-session-search">搜索 Agent 会话</label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-2 size-3.5 text-muted-foreground" aria-hidden />
                <input id="agent-session-search" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder="搜索会话、项目或工作流" className="min-h-9 w-full rounded-xs border bg-background py-1 pl-7 pr-2 text-xs" />
              </div>
              {presentation === 'page' ? (
                <div className="mt-1 max-h-28 space-y-1 overflow-y-auto rounded-xs border bg-background p-1" aria-label="选择 Agent 会话">
                  {visibleSessions.length === 0 ? <p className="px-2 py-1 text-xs text-muted-foreground">还没有会话，发送第一句即可开始。</p> : visibleSessions.map((session) => (
                    <button
                      key={session.id}
                      type="button"
                      onClick={() => selectSession(session.id)}
                      disabled={sending || confirming || closing}
                      className={`flex min-h-9 w-full items-center justify-between gap-2 rounded px-2 text-left text-xs hover:bg-muted disabled:opacity-50 ${session.id === sessionId ? 'bg-muted font-medium' : ''}`}
                    >
                      <span className="min-w-0 truncate">{session.title || `会话 ${session.id.slice(0, 8)}`}</span>
                      <span className="shrink-0 text-3xs text-muted-foreground">{session.status === 'active' ? '进行中' : '已关闭'}</span>
                    </button>
                  ))}
                </div>
              ) : (
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
              )}
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
            {canReopen ? <Button type="button" variant="outline" size="sm" onClick={() => void reopenSession()} disabled={closing}>重新打开</Button> : null}
          </div>
        </div>

        <div className={presentation === 'page' ? 'flex min-h-0 min-w-0 flex-col' : 'flex min-h-0 flex-1 flex-col'}>
          {presentation === 'page' ? <div className="flex min-h-14 items-center gap-2 border-b px-3">
            <Button type="button" variant="ghost" size="icon-sm" onClick={() => setRailOpen((value) => !value)} aria-label="切换会话侧栏">
              {railOpen ? <PanelLeftClose aria-hidden /> : <PanelLeftOpen aria-hidden />}
            </Button>
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold">{selectedSession?.title || '新会话'}</div><div className="truncate text-xs text-muted-foreground">{authorizedWorkspace?.name ?? '选择 Workspace'} · {boundExecutionTarget?.label ?? selectedExecutionTarget?.label ?? '选择执行配置'}</div></div>
            {!selectedSession ? <div className="flex items-center gap-1"><select value={executionTargetId} onChange={(event) => { const target = executionTargets.find((item) => item.id === event.target.value); setExecutionTargetId(event.target.value); setModelId(target?.default_model_id ?? target?.models[0]?.id ?? '') }} className="min-h-9 max-w-44 rounded border bg-background px-2 text-xs" aria-label="执行配置"><option value="">选择执行配置</option>{executionTargets.map((target) => <option key={target.id} value={target.id}>{target.label} · {target.readiness.status === 'ready' ? '就绪' : target.readiness.status === 'blocked' ? '不可用' : '待验证'}</option>)}</select>{selectedExecutionTarget?.models.length ? <select value={modelId} onChange={(event) => setModelId(event.target.value)} className="min-h-9 max-w-32 rounded border bg-background px-2 text-xs" aria-label="模型">{selectedExecutionTarget.models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}</select> : null}<select value={executionMode} onChange={(event) => setExecutionMode(event.target.value as 'synchronous' | 'background')} className="min-h-9 rounded border bg-background px-2 text-xs" aria-label="执行方式"><option value="synchronous">同步</option><option value="background">后台</option></select></div> : null}
          </div> : null}
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-4" aria-live="polite">
            {workspaceScopeError ? <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-xs" role="alert">{workspaceScopeError}</div> : null}
            {authorizedWorkspaces.isLoading ? (
              <div className="flex items-center gap-2 rounded-md border border-border/70 bg-muted/20 p-3 text-xs text-muted-foreground" role="status">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                正在读取你可以使用的工作区…
              </div>
            ) : null}
            {authorizedWorkspaces.isError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs" role="alert">
                工作区列表读取失败，请刷新后重试；没有确认授权范围前不会打开或创建会话。
              </div>
            ) : null}
            {!workspaceId && !workspaceScopeError ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-4 text-xs" role="alert">
                当前工作区不明确。请在这里选择一个已授权工作区；不会自动跨范围打开或创建会话。
              </div>
            ) : null}
            {presentation !== 'page' && providersQuery.isSuccess && !modelConfigured ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" role="status">
                <div className="font-medium text-foreground">尚未配置可用模型</div>
                <p className="mt-1 text-muted-foreground">配置启用的模型连接和默认模型后，Ask Alice 才能处理消息。</p>
                <Link href="/providers" className="mt-2 inline-flex min-h-9 items-center underline underline-offset-4">打开模型与连接</Link>
              </div>
            ) : null}
            {presentation === 'page' && !selectedSession && executionTargetsQuery.isSuccess && selectedExecutionTarget?.readiness.status === 'blocked' ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs" role="status">
                <div className="font-medium">执行配置尚未就绪</div>
                <p className="mt-1 text-muted-foreground">{selectedExecutionTarget?.readiness.reason}</p>
                <Link href={selectedExecutionTarget?.setup_url ?? '/providers'} className="mt-2 inline-flex min-h-9 items-center underline underline-offset-4">打开配置</Link>
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
              >
                <div className={message.role === 'user'
                  ? 'ml-8 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground'
                  : 'mr-8 rounded-md border bg-muted/30 px-3 py-2 text-sm'}>
                  {message.content}
                </div>
                {message.role === 'assistant' ? toolTraces.filter((trace) => trace.sequence === message.sequence).map((trace) => (
                  <details key={`tool-trace-${trace.sequence}`} className="mr-8 mt-2 rounded-md border bg-muted/20 text-xs">
                    <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-3 font-medium marker:content-none"><Wrench className="size-3.5 text-primary" aria-hidden />已执行 {trace.entries.length} 项工具操作<ChevronDown className="ml-auto size-3.5 text-muted-foreground" aria-hidden /></summary>
                    <div className="space-y-2 border-t px-3 py-2">{trace.entries.map((entry, traceIndex) => <details key={`${trace.sequence}-${traceIndex}`} className="rounded border bg-background/70"><summary className="cursor-pointer px-2 py-1.5 font-medium">{traceLabel(entry, traceIndex)}</summary><pre className="max-h-48 overflow-auto border-t p-2 text-3xs text-muted-foreground">{JSON.stringify(entry, null, 2)}</pre></details>)}</div>
                  </details>
                )) : null}
              </div>
            ))}
            {selectedSession?.context_binding && (selectedSession.context_binding.project_id || selectedSession.context_binding.workflow_id || selectedSession.context_binding.run_id) ? (
              <nav className="flex flex-wrap gap-2 border-t pt-3 text-xs" aria-label="会话上下文链接">
                {selectedSession.context_binding.project_id ? <Link className="underline underline-offset-4" href={`/studio/projects/${selectedSession.context_binding.project_id}?workspace=${selectedSession.context_binding.studio_workspace_id ?? workspaceId}`}>项目</Link> : null}
                {selectedSession.context_binding.workflow_id && selectedSession.context_binding.project_id && (selectedSession.context_binding.studio_workspace_id ?? workspaceId) ? <Link className="underline underline-offset-4" href={workflowDraftHref(selectedSession.context_binding.studio_workspace_id ?? workspaceId!, selectedSession.context_binding.project_id, selectedSession.context_binding.workflow_id, selectedSession.id)}>工作流草稿</Link> : null}
                {selectedSession.context_binding.run_id ? <Link className="underline underline-offset-4" href={buildRunUrl('operations', { workspace: selectedSession.context_binding.studio_workspace_id ?? workspaceId ?? undefined, project: selectedSession.context_binding.project_id ?? undefined, workflow: selectedSession.context_binding.workflow_id ?? undefined, run: selectedSession.context_binding.run_id }) ?? '/studio'}>运行</Link> : null}
              </nav>
            ) : null}
            {completedResultHref ? <Link href={completedResultHref} className="inline-flex min-h-11 items-center text-xs font-medium underline underline-offset-4">打开 Agent 保存的工作流草稿</Link> : null}
            {sending ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                Agent 正在处理
              </div>
            ) : null}
            {backgroundTurn ? <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status"><Loader2 className="size-3.5 animate-spin" aria-hidden />后台任务正在运行，完成后会恢复到此会话。</div> : null}
            {backgroundTurn && backgroundActivities.filter((activity) => activity.turnId === backgroundTurn.turnId).length > 0 ? (
              <section className="mr-8 rounded-md border bg-muted/20 p-3 text-xs" aria-label="当前后台活动">
                <div className="font-medium">当前活动</div>
                <ol className="mt-2 space-y-1 text-muted-foreground">
                  {backgroundActivities.filter((activity) => activity.turnId === backgroundTurn.turnId).map((activity) => (
                    <li key={`${activity.turnId}-${activity.sequence}`}>
                      <span>{typeof activity.payload.label === 'string' ? activity.payload.label : '正在处理'}</span>
                      {typeof activity.payload.detail === 'string' ? <p className="mt-0.5 break-words text-muted-foreground">{activity.payload.detail}</p> : null}
                    </li>
                  ))}
                </ol>
              </section>
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
            disabled={sending || confirming || Boolean(backgroundTurn) || selectedSession?.status === 'closed' || (presentation !== 'page' && providersQuery.isSuccess && !modelConfigured)}
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
                || Boolean(backgroundTurn)
                || (presentation !== 'page' && providersQuery.isSuccess && !modelConfigured)
              }
            >
              {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
              发送
            </Button>
          </div>
        </form>
        </div>
    </div>
  )
}
