'use client'

import { useQueryClient } from '@tanstack/react-query'
import { Bot, Check, CircleAlert, CircleCheck, Clock3, Loader2, Monitor, RotateCcw, Send, ShieldCheck, Sparkles, X } from 'lucide-react'
import { usePathname } from 'next/navigation'
import { FormEvent, KeyboardEvent, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Textarea } from '@/components/ui/textarea'
import { apiClient } from '@/lib/api/client'
import { getApiAuthHeaders } from '@/lib/api/auth-headers'
import { ROUTE_LABELS } from '@/lib/navigation'

type AgentMessage = {
  role: 'user' | 'assistant'
  content: string
}

type AgentProposal = {
  tool: string
  args: Record<string, unknown>
  summary: string
  diff: string
  work_item_id?: string | null
  workspace_id?: string | null
  proposal_version?: string | null
}

type AgentReply = {
  type: 'message' | 'proposal'
  content?: string | null
  proposal?: AgentProposal | null
}

type ActivityState = 'active' | 'complete' | 'attention'

type Activity = {
  label: string
  detail: string
  state: ActivityState
  target?: { type?: string; id?: string | null }
}

type AgentRunEvent = {
  sequence: number
  type: string
  label: string
  detail: string
  state?: 'active' | 'completed' | 'attention' | 'failed'
  target?: { type?: string; id?: string | null }
  recovery?: string
  reply?: AgentReply
}

function activityFromEvent(event: AgentRunEvent): Activity {
  return {
    label: event.label,
    detail: event.recovery ? `${event.detail} ${event.recovery}` : event.detail,
    state: event.state === 'completed' ? 'complete' : event.state === 'failed' || event.state === 'attention' ? 'attention' : 'active',
    target: event.target,
  }
}

function activityForReply(reply: AgentReply): Activity[] {
  if (reply.type === 'proposal' && reply.proposal) {
    return [
      { label: '已定位操作对象', detail: reply.proposal.summary, state: 'complete' },
      { label: '等待你的确认', detail: '这是一次会改变软件状态的操作。确认后才会执行。', state: 'attention' },
    ]
  }
  return [{ label: '已完成处理', detail: '已基于当前可访问的数据生成结果。', state: 'complete' }]
}

export function GlobalAgentDock({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const pathname = usePathname()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [input, setInput] = useState('')
  const [proposal, setProposal] = useState<AgentProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [goal, setGoal] = useState<string | null>(null)
  const [activities, setActivities] = useState<Activity[]>([])
  const [lastFailedProposal, setLastFailedProposal] = useState<AgentProposal | null>(null)
  const [showLiveSurface, setShowLiveSurface] = useState(false)
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null)
  const [agentRunId, setAgentRunId] = useState<string | null>(null)

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    const content = input.trim()
    if (!content || sending || proposal) return

    const nextMessages = [...messages, { role: 'user' as const, content }]
    setMessages(nextMessages)
    setInput('')
    setError(null)
    setSending(true)
    setGoal(content)
    setActivities([
      { label: '理解你的目标', detail: '正在结合当前页面和选中对象梳理任务。', state: 'complete' },
      { label: '检查可用信息', detail: '正在判断是否需要读取数据或准备操作。', state: 'active' },
    ])
    let activeRunId: string | null = null
    try {
      const searchParams = new URLSearchParams(window.location.search)
      const workspaceId = searchParams.get('workspace')
      const projectId = searchParams.get('project')
        ?? pathname.match(/^\/studio\/projects\/([^/]+)/)?.[1]
        ?? null
      const workflowId = searchParams.get('workflow')
      const sourceId = searchParams.get('source')
        ?? pathname.match(/^\/sources\/([^/]+)/)?.[1]
        ?? null
      const response = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getApiAuthHeaders() },
        body: JSON.stringify({
        messages: nextMessages,
        session_id: agentSessionId,
        context: {
          surface: ROUTE_LABELS[pathname] ?? pathname,
          pathname,
          search: searchParams.toString(),
          workspace_id: workspaceId,
          project_id: projectId,
          workflow_id: workflowId,
          source_id: sourceId,
        },
        }),
      })
      if (!response.ok || !response.body) throw new Error(`Agent 请求失败（${response.status}）`)

      const receivedRunId = response.headers.get('X-Agent-Run-Id')
      const receivedSessionId = response.headers.get('X-Agent-Session-Id')
      if (receivedRunId) {
        activeRunId = receivedRunId
        setAgentRunId(receivedRunId)
      }
      if (receivedSessionId) setAgentSessionId(receivedSessionId)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let reply: AgentReply | null = null
      let streamError: string | null = null
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.trim()) continue
          const runEvent = JSON.parse(line) as AgentRunEvent
          if (runEvent.type === 'reply' && runEvent.reply) {
            reply = runEvent.reply
          } else {
            setActivities((current) => {
              const next = [...current.filter((item) => item.state !== 'active'), activityFromEvent(runEvent)]
              return next.slice(-8)
            })
          }
          if (runEvent.type === 'run.failed') streamError = runEvent.detail
        }
        if (done) break
      }
      if (streamError) throw new Error(streamError)
      if (!reply) throw new Error('Agent 执行结束但没有返回结果')
      if (reply.type === 'proposal' && reply.proposal) {
        setProposal(reply.proposal)
      } else {
        setMessages((current) => [
          ...current,
          { role: 'assistant', content: reply.content?.trim() || '没有返回内容。' },
        ])
      }
      setActivities((current) => [...current.filter((item) => item.state !== 'active'), ...activityForReply(reply)].slice(-8))
    } catch (reason) {
      const recoverableRunId = activeRunId ?? agentRunId
      if (recoverableRunId) {
        try {
          const recovery = await apiClient.get<AgentRunEvent[]>(`/chat/runs/${recoverableRunId}/events`)
          for (const replayed of recovery.data ?? []) {
            if (replayed.type === 'reply' && replayed.reply) {
              if (replayed.reply.type === 'proposal' && replayed.reply.proposal) setProposal(replayed.reply.proposal)
              else setMessages((current) => [...current, { role: 'assistant', content: replayed.reply?.content?.trim() || '' }])
            }
          }
        } catch {
          // Keep the stream error as the primary recovery signal.
        }
      }
      const message = reason instanceof Error ? reason.message : 'Agent 暂时不可用'
      setError(message)
      setActivities([
        { label: '暂时无法完成理解', detail: message, state: 'attention' },
        { label: '恢复方式', detail: '请检查模型连接后重试，或换一种说法继续。', state: 'attention' },
      ])
    } finally {
      setSending(false)
    }
  }

  async function confirmProposal(proposalToConfirm = proposal) {
    if (!proposalToConfirm || confirming) return
    setError(null)
    setConfirming(true)
    setLastFailedProposal(null)
    setActivities([
      { label: '已获得你的确认', detail: proposalToConfirm.summary, state: 'complete' },
      { label: '正在执行操作', detail: '系统正在应用这项变更。', state: 'active' },
    ])
    try {
      await apiClient.post('/chat/confirm', { proposal: proposalToConfirm })
      setMessages((current) => [
        ...current,
        { role: 'assistant', content: `已完成：${proposalToConfirm.summary}` },
      ])
      setProposal(null)
      await queryClient.invalidateQueries()
      setActivities([
        { label: '操作已完成', detail: proposalToConfirm.summary, state: 'complete' },
        { label: '界面已同步', detail: '已刷新相关数据；你现在看到的是最新状态。', state: 'complete' },
      ])
    } catch (reason) {
      const status = reason instanceof Error && 'status' in reason ? reason.status : undefined
      const message = reason instanceof Error ? reason.message : '操作执行失败'
      setError(
        status === 409
          ? `提案已失效或目标已变化：${message}。请拒绝后重新发起。`
          : message,
      )
      setLastFailedProposal(proposalToConfirm)
      setActivities([
        { label: '操作未完成', detail: message, state: 'attention' },
        { label: '可恢复', detail: '检查目标状态后，可重新执行或回到对话调整请求。', state: 'attention' },
      ])
    } finally {
      setConfirming(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void sendMessage()
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md" aria-label="全局 Agent">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2">
            <Bot className="size-4 text-primary" aria-hidden />
            全局 Agent
          </SheetTitle>
          <SheetDescription>
            当前上下文：{ROUTE_LABELS[pathname] ?? pathname}。读取可直接执行，写入操作先生成确认提案。
            未明确指定 Workspace 时，仅在后端能解析出唯一授权范围时允许确认写操作。
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-4" aria-live="polite">
            {messages.length === 0 ? (
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
            {messages.map((message, index) => (
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
            {goal ? (
              <section className="rounded-md border bg-muted/20 p-3" aria-label="Agent 执行进度">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Sparkles className="size-4 text-primary" aria-hidden />
                  正在处理
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">目标：{goal}</p>
                <ol className="mt-3 space-y-2">
                  {activities.map((activity, index) => {
                    const Icon = activity.state === 'complete' ? CircleCheck : activity.state === 'attention' ? CircleAlert : Clock3
                    return (
                      <li key={`${activity.label}-${index}`} className="flex gap-2 text-xs">
                        <Icon className={activity.state === 'complete' ? 'mt-0.5 size-3.5 shrink-0 text-success' : activity.state === 'attention' ? 'mt-0.5 size-3.5 shrink-0 text-warning' : 'mt-0.5 size-3.5 shrink-0 animate-pulse text-primary'} aria-hidden />
                        <div>
                          <p className="font-medium">{activity.label}</p>
                          <p className="mt-0.5 text-muted-foreground">{activity.detail}</p>
                          {activity.target?.type ? (
                            <p className="mt-1 text-3xs text-muted-foreground">
                              对象：{activity.target.type}{activity.target.id ? ` · ${activity.target.id}` : ''}
                            </p>
                          ) : null}
                        </div>
                      </li>
                    )
                  })}
                </ol>
              </section>
            ) : null}
            {goal ? (
              <section className="rounded-md border p-3" aria-label="软件现场">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="flex items-center gap-2 text-sm font-medium">
                      <Monitor className="size-4 text-primary" aria-hidden />
                      软件现场
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">查看内置浏览器正在发生的实际变化。</p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setShowLiveSurface((value) => !value)}>
                    {showLiveSurface ? '收起' : '实时查看'}
                  </Button>
                </div>
                {showLiveSurface ? (
                  <iframe
                    title="内置浏览器实时画面"
                    src={`${window.location.protocol}//${window.location.hostname}:6080/vnc.html?autoconnect=true&resize=scale&view_only=true`}
                    className="mt-3 aspect-video w-full rounded-xs border bg-black"
                    sandbox="allow-scripts allow-same-origin"
                  />
                ) : null}
              </section>
            ) : null}
            {proposal ? (
              <div className="rounded-md border border-warning/40 bg-warning/10 p-3">
                <div className="text-sm font-medium">待确认操作</div>
                <p className="mt-1 text-xs text-muted-foreground">{proposal.summary}</p>
                <div className="mt-3 rounded-xs border bg-background/70 p-2 font-mono text-2xs">
                  已准备好变更，确认后才会应用。
                </div>
                <div className="hidden">
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
            {error ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs" role="alert">
                <p className="text-destructive">{error}</p>
                {lastFailedProposal ? (
                  <Button className="mt-2" variant="outline" size="sm" disabled={confirming} onClick={() => void confirmProposal(lastFailedProposal)}>
                    <RotateCcw aria-hidden />
                    重试执行
                  </Button>
                ) : null}
              </div>
            ) : null}
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
            disabled={sending || confirming}
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="text-3xs text-muted-foreground">Enter 发送 · Shift+Enter 换行</span>
            <Button type="submit" size="sm" disabled={!input.trim() || sending || confirming || Boolean(proposal)}>
              {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
              发送
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  )
}
