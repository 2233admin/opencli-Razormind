'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Bot,
  CircleCheck,
  CirclePause,
  Clock3,
  LoaderCircle,
  Play,
  Settings2,
} from 'lucide-react'
import Link from 'next/link'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  agentWorkHealthQueryKey,
  getAgentWorkHealth,
  performAgentWorkAction,
  type AgentWorkAction,
  type AgentWorkHealthItem,
  type AgentWorkState,
} from '@/lib/api/agent-work-health'
import { cn } from '@/lib/utils'

const statePresentation: Record<
  AgentWorkState,
  { label: string; tone: 'default' | 'secondary' | 'destructive' | 'outline' }
> = {
  not_started: { label: '尚未开始', tone: 'outline' },
  ready: { label: '可继续', tone: 'secondary' },
  queued: { label: '排队中', tone: 'secondary' },
  running: { label: '运行中', tone: 'default' },
  paused: { label: '已暂停', tone: 'outline' },
  completed: { label: '已完成', tone: 'secondary' },
  failed: { label: '运行失败', tone: 'destructive' },
  blocked: { label: '需要处理', tone: 'destructive' },
  inactive: { label: '未启用', tone: 'outline' },
  cancelled: { label: '已结束', tone: 'outline' },
}

function workHref(
  governedWorkspaceId: string,
  navigationWorkspaceId: string,
  projectId: string | null | undefined,
  action: AgentWorkAction,
) {
  if (action.kind === 'configure') {
    const params = new URLSearchParams({ workspace: governedWorkspaceId })
    if (action.operations_agent_id) {
      params.set('agent', action.operations_agent_id)
      params.set('config', 'contract')
    } else if (action.automation_id) {
      params.set('automation', action.automation_id)
      params.set('config', 'binding')
    }
    return `/operations-agents?${params}`
  }
  if (action.kind !== 'open_conversation' || !action.conversation_id) return null
  const params = new URLSearchParams({
    workspace: navigationWorkspaceId,
    agent: '1',
    conversation: action.conversation_id,
  })
  const pathname = projectId
    ? `/studio/projects/${encodeURIComponent(projectId)}`
    : '/studio'
  return `${pathname}?${params}`
}

function WorkStateIcon({ state }: { state: AgentWorkState }) {
  if (state === 'running' || state === 'queued') {
    return <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden />
  }
  if (state === 'paused') return <CirclePause className="size-4 text-amber-600" aria-hidden />
  if (state === 'failed' || state === 'blocked') {
    return <AlertTriangle className="size-4 text-destructive" aria-hidden />
  }
  if (state === 'completed') {
    return <CircleCheck className="size-4 text-emerald-600" aria-hidden />
  }
  return <Clock3 className="size-4 text-muted-foreground" aria-hidden />
}

function WorkItem({
  item,
  governedWorkspaceId,
  navigationWorkspaceId,
  projectId,
  busy,
  onAction,
}: {
  item: AgentWorkHealthItem
  governedWorkspaceId: string
  navigationWorkspaceId: string
  projectId?: string | null
  busy: boolean
  onAction: (action: AgentWorkAction) => void
}) {
  const presentation = statePresentation[item.state]
  return (
    <article className="rounded-lg border bg-background p-3" aria-labelledby={`${item.id}-title`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <WorkStateIcon state={item.state} />
          <div className="min-w-0">
            <h4 id={`${item.id}-title`} className="truncate text-sm font-medium">{item.title}</h4>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{item.message}</p>
          </div>
        </div>
        <Badge variant={presentation.tone}>{presentation.label}</Badge>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{item.kind === 'conversation' ? '持久会话' : item.kind === 'automation' ? '自动化' : 'Agent Run'}</span>
        {item.binding ? <span>Agent v{item.binding.published_version} · Profile v{item.binding.profile_version}</span> : null}
        {item.binding?.automation_revision ? <span>Automation r{item.binding.automation_revision}</span> : null}
        {item.binding?.runtime ? <span className="font-mono">{item.binding.runtime}</span> : null}
      </div>

      {item.state === 'paused' && !item.resume_supported ? (
        <p className="mt-2 rounded-md bg-amber-500/10 px-2.5 py-2 text-xs text-amber-700 dark:text-amber-300">
          此 Runtime 没有可用的按 ID 恢复能力。重新运行会创建新 Run，不会被描述为恢复。
        </p>
      ) : null}

      {item.actions.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {item.actions.map((action) => {
            const href = workHref(governedWorkspaceId, navigationWorkspaceId, projectId, action)
            if (href) {
              return (
                <Link
                  key={`${action.kind}:${action.conversation_id ?? action.automation_id ?? action.operations_agent_id}`}
                  href={href}
                  className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
                >
                  {action.kind === 'configure' ? <Settings2 aria-hidden /> : <Bot aria-hidden />}
                  {action.label}
                </Link>
              )
            }
            return (
              <Button
                key={`${action.kind}:${action.run_id ?? action.automation_id}`}
                type="button"
                size="sm"
                variant={action.kind === 'pause_run' ? 'outline' : 'default'}
                disabled={busy}
                onClick={() => onAction(action)}
              >
                {action.kind === 'pause_run' ? <CirclePause aria-hidden /> : <Play aria-hidden />}
                {action.label}
              </Button>
            )
          })}
        </div>
      ) : null}
    </article>
  )
}

export function AgentWorkHealth({
  workspaceId,
  projectId,
  className,
}: {
  workspaceId: string | null
  projectId?: string | null
  className?: string
}) {
  const queryClient = useQueryClient()
  const health = useQuery({
    queryKey: agentWorkHealthQueryKey(workspaceId ?? '', projectId),
    queryFn: () => getAgentWorkHealth(workspaceId!, projectId),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) => query.state.data?.items.some((item) =>
      item.state === 'queued' || item.state === 'running') ? 5_000 : 30_000,
  })
  const action = useMutation({
    mutationFn: (next: AgentWorkAction) => {
      if (!health.data) throw new Error('工作状态尚未加载')
      return performAgentWorkAction(health.data.workspace_id, next)
    },
    onSuccess: () => {
      toast.success('工作状态已更新')
      void queryClient.invalidateQueries({
        queryKey: agentWorkHealthQueryKey(workspaceId ?? '', projectId),
      })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <Card className={cn('overflow-hidden', className)} aria-labelledby="agent-work-health-title">
      <CardHeader className="border-b pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><Bot className="size-3.5 text-primary" aria-hidden />Agent work health</div>
            <CardTitle id="agent-work-health-title" className="mt-1 text-base">工作状态与修复入口</CardTitle>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">状态来自持久会话、实际 Run 和 Automation 绑定；操作按当前 Workspace 权限返回。</p>
          </div>
          {health.data ? (
            <Badge variant={health.data.counts.needs_attention ? 'destructive' : 'secondary'}>
              {health.data.counts.needs_attention
                ? `${health.data.counts.needs_attention} 项需处理`
                : health.data.counts.total
                  ? '暂无待处理项'
                  : '暂无 Agent 工作'}
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-4">
        {!workspaceId ? <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">选择 Workspace 后查看 Agent 工作状态。</p> : null}
        {workspaceId && health.isLoading ? <p role="status" className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">正在读取工作状态…</p> : null}
        {workspaceId && health.isError ? <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">工作状态暂时不可用：{health.error.message}</p> : null}
        {workspaceId && health.data && !health.data.permissions.can_run ? (
          <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">当前成员为只读视图；运行、暂停或重试需要 Operations Agent 运行权限。</p>
        ) : null}
        {workspaceId && health.data?.items.length === 0 ? <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">当前范围内还没有 Agent 工作、Run 或 Automation。</p> : null}
        {health.data?.items.map((item) => (
          <WorkItem
            key={item.id}
            item={item}
            governedWorkspaceId={health.data.workspace_id}
            navigationWorkspaceId={health.data.studio_workspace_id ?? health.data.workspace_id}
            projectId={projectId}
            busy={action.isPending}
            onAction={(next) => action.mutate(next)}
          />
        ))}
      </CardContent>
    </Card>
  )
}
