'use client'

import { Bot, CircleCheck, Clock3, MessageSquarePlus, Search, X } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { AgentWorkHealth } from '@/components/studio/agent-work-health'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAgentConversations } from '@/lib/api/hooks'
import type { AgentConversation } from '@/lib/api/agent-conversations'
import { formatRelative } from '@/lib/format'
import { cn } from '@/lib/utils'

type SessionFilter = 'all' | 'active' | 'closed'

const MAX_PINNED_SESSIONS = 5

function sessionLabel(session: AgentConversation) {
  return session.title?.trim() || `工作会话 ${session.id.slice(0, 8)}`
}

/**
 * A project-local library of server persisted Agent sessions. The dock remains
 * the single composer; this surface makes its durable sessions discoverable and
 * gives project work a stable, reusable entry point.
 */
export function ProjectAgentWorkspace({
  workspaceId,
  projectId,
  workflowId,
}: {
  workspaceId: string | null
  projectId: string
  workflowId?: string | null
}) {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const conversations = useAgentConversations(workspaceId, {
    projectId,
    workflowId,
  })
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<SessionFilter>('all')
  const [pinnedIds, setPinnedIds] = useState<string[]>([])
  const storageKey = workspaceId ? `opencli:project-agent-tabs:${workspaceId}:${projectId}` : null

  useEffect(() => {
    if (!storageKey) return
    try {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) ?? '[]')
      setPinnedIds(Array.isArray(saved) ? saved.filter((value): value is string => typeof value === 'string') : [])
    } catch {
      setPinnedIds([])
    }
  }, [storageKey])

  const projectSessions = useMemo(() => (conversations.data ?? [])
    .filter((session) => session.context_binding.project_id === projectId)
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)), [conversations.data, projectId])
  const sessionsById = useMemo(() => new Map(projectSessions.map((session) => [session.id, session])), [projectSessions])
  const visibleSessions = useMemo(() => {
    const query = search.trim().toLowerCase()
    return projectSessions.filter((session) => {
      if (filter !== 'all' && session.status !== filter) return false
      return !query || `${sessionLabel(session)} ${session.id} ${session.context_binding.workflow_id ?? ''}`.toLowerCase().includes(query)
    })
  }, [filter, projectSessions, search])
  const pinnedSessions = pinnedIds.map((id) => sessionsById.get(id)).filter((value): value is AgentConversation => Boolean(value))

  function persistPinned(next: string[]) {
    const unique = [...new Set(next)].slice(0, MAX_PINNED_SESSIONS)
    setPinnedIds(unique)
    if (storageKey) window.localStorage.setItem(storageKey, JSON.stringify(unique))
  }

  function openAgent(conversationId?: string) {
    const params = new URLSearchParams(searchParams.toString())
    if (workspaceId) params.set('workspace', workspaceId)
    params.set('project', projectId)
    if (workflowId) params.set('workflow', workflowId)
    params.set('agent', '1')
    if (conversationId) {
      params.set('conversation', conversationId)
      persistPinned([conversationId, ...pinnedIds])
    } else {
      params.delete('conversation')
    }
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const activeCount = projectSessions.filter((session) => session.status === 'active').length

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(20rem,0.9fr)]">
    <Card className="overflow-hidden border-primary/20 bg-gradient-to-br from-primary/[0.045] via-background to-background" aria-labelledby="project-agent-workspace-title">
      <CardHeader className="border-b bg-background/70 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><Bot className="size-3.5 text-primary" aria-hidden />Project Agent Workspace</div>
            <CardTitle id="project-agent-workspace-title" className="mt-1 text-base">项目工作会话</CardTitle>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">把项目内的 Agent 工作保存在同一个上下文里：随时恢复、继续执行，并从这里打开对应的持久会话。</p>
          </div>
          <Button type="button" size="sm" onClick={() => openAgent()} disabled={!workspaceId} className="min-h-10">
            <MessageSquarePlus className="size-4" />开始新会话
          </Button>
        </div>
        {pinnedSessions.length ? (
          <div className="mt-3 flex min-w-0 items-center gap-2" aria-label="已打开的项目工作标签">
            <span className="shrink-0 text-xs text-muted-foreground">工作标签</span>
            <div className="flex min-w-0 gap-1 overflow-x-auto pb-1">
              {pinnedSessions.map((session) => (
                <div key={session.id} className="group flex shrink-0 items-center rounded-md border bg-background shadow-sm transition-colors hover:bg-muted/60">
                  <button type="button" onClick={() => openAgent(session.id)} className="max-w-44 truncate px-2.5 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    {sessionLabel(session)}
                  </button>
                  <button type="button" aria-label={`关闭工作标签：${sessionLabel(session)}`} onClick={() => persistPinned(pinnedIds.filter((id) => id !== session.id))} className="mr-1 rounded p-1 text-muted-foreground opacity-70 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100">
                    <X className="size-3" aria-hidden />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" aria-hidden />
            <Input value={search} onChange={(event) => setSearch(event.target.value)} className="h-9 pl-8 text-xs" placeholder="搜索工作会话或工作流" aria-label="搜索项目 Agent 会话" />
          </div>
          <Tabs value={filter} onValueChange={(value) => setFilter(value as SessionFilter)}>
            <TabsList variant="line" aria-label="项目会话状态筛选">
              <TabsTrigger value="all">全部 {projectSessions.length}</TabsTrigger>
              <TabsTrigger value="active">进行中 {activeCount}</TabsTrigger>
              <TabsTrigger value="closed">已结束 {projectSessions.length - activeCount}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        {!workspaceId ? <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">选择 Workspace 后才能读取这个项目的持久工作会话。</div> : null}
        {workspaceId && conversations.isLoading ? <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">正在读取项目工作会话…</div> : null}
        {workspaceId && conversations.isError ? <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">会话库暂时不可用：{conversations.error?.message ?? '请稍后重试'}</div> : null}
        {workspaceId && !conversations.isLoading && !conversations.isError && visibleSessions.length === 0 ? (
          <div className="rounded-md border border-dashed p-5 text-sm text-muted-foreground">
            {projectSessions.length ? '没有符合当前搜索或状态的会话。' : '这个项目还没有 Agent 工作会话。开始一次工作后，会话会保留在这里。'}
          </div>
        ) : null}
        {visibleSessions.map((session) => {
          const active = session.status === 'active'
          return (
            <button key={session.id} type="button" onClick={() => openAgent(session.id)} className="group w-full rounded-md border bg-background p-3 text-left transition-[border-color,background-color,transform] duration-200 hover:-translate-y-px hover:border-primary/40 hover:bg-primary/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{sessionLabel(session)}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1">{active ? <Clock3 className="size-3 text-primary" aria-hidden /> : <CircleCheck className="size-3 text-success" aria-hidden />}{active ? '进行中，可继续' : '已结束，可回看'}</span>
                    {session.context_binding.workflow_id ? <span className="font-mono">工作流 {session.context_binding.workflow_id.slice(0, 8)}</span> : <span>项目级上下文</span>}
                  </div>
                </div>
                <span className={cn('shrink-0 text-xs', active ? 'text-primary' : 'text-muted-foreground')}>{formatRelative(session.updated_at)}</span>
              </div>
            </button>
          )
        })}
      </CardContent>
    </Card>
    <AgentWorkHealth workspaceId={workspaceId} projectId={projectId} />
    </div>
  )
}
