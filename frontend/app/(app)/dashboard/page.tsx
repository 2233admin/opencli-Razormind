'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import {
  Activity,
  Bot,
  AlertTriangle,
  ArrowDownToLine,
  ArrowRight,
  BellRing,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Database,
  GitBranch,
  Play,
  Radio,
  Send,
  Server,
  Tags,
} from 'lucide-react'

import {
  useAgents,
  useDashboardActivity,
  useDashboardStats,
  useNotificationLogs,
  useNotificationRules,
  useOpinionMonitor,
  useSchedules,
  useWorkers,
} from '@/lib/api/hooks'
import type { OpinionMonitor, WorkerNode } from '@/lib/api/types'
import type { FailureItem, StreamTask, ThroughputPoint, WorkerView } from '@/lib/demo/monitor'
import { formatNumber, formatRelative } from '@/lib/format'
import { notificationChannelLabel } from '@/lib/notification-channels'
import { cn } from '@/lib/utils'
import { AgentTaskRow, ToolChip } from '@/components/agent-native/agent-primitives'
import type { AgentTaskStatus } from '@/components/agent-native/agent-primitives'
import { AgentToolReference } from '@/components/agent/agent-tool-reference'
import { AgentConversationSurface } from '@/components/shell/agent-conversation-surface'
import { MatrixClock } from '@/components/monitor/matrix-clock'
import { FailureFeed, TaskStream } from '@/components/monitor/task-stream'
import { ThroughputChart } from '@/components/monitor/throughput-chart'
import { OperationalAnalytics } from '@/components/monitor/operational-analytics'
import { WorkerAllocation } from '@/components/monitor/worker-allocation'
import { FancyTestimonialsSlider, type Testimonial } from '@/components/eldoraui/testimonal-slider'
import { BACKEND_HINT, ErrorState, LoadingState } from '@/components/shell/data-states'
import { PageContainer } from '@/components/shell/page-container'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

function KpiCard({ title, value, sub, icon: Icon }: { title: string; value: string; sub?: string; icon: typeof Activity }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent>
        <div className="font-mono text-2xl tabular-nums">{value}</div>
        {sub ? <p className="mt-1 text-xs text-muted-foreground">{sub}</p> : null}
      </CardContent>
    </Card>
  )
}

function ActionLink({ href, title, description, icon: Icon }: { href: string; title: string; description: string; icon: typeof Activity }) {
  return (
    <Link
      href={href}
      className="group flex items-center gap-3 rounded-lg border border-border/70 bg-background/60 p-3 transition-colors hover:border-primary/30 hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
        <Icon className="size-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{description}</span>
      </span>
      <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" aria-hidden />
    </Link>
  )
}

function percent(value: number, total: number) {
  return total > 0 ? Math.round((value / total) * 100) : 0
}

function normalizedSuccessRate(value: number) {
  return Math.round(value <= 1 ? value * 100 : value)
}

function countdownLabel(nextRunAt: string, now: number) {
  const target = new Date(nextRunAt).getTime()
  if (Number.isNaN(target)) return '时间无效'

  const remaining = target - now
  if (remaining <= 0) return '即将执行'

  const totalSeconds = Math.floor(remaining / 1000)
  const days = Math.floor(totalSeconds / 86_400)
  const hours = Math.floor((totalSeconds % 86_400) / 3_600)
  const minutes = Math.floor((totalSeconds % 3_600) / 60)
  const seconds = totalSeconds % 60
  const clock = [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')

  return days > 0 ? `${days}天 ${clock}` : clock
}

function NextRunCountdown({ nextRunAt }: { nextRunAt?: string }) {
  const [now, setNow] = useState<number | null>(null)

  useEffect(() => {
    const tick = () => setNow(Date.now())
    tick()
    const interval = window.setInterval(tick, 1_000)
    return () => window.clearInterval(interval)
  }, [nextRunAt])

  if (!nextRunAt) return <span>未设置</span>
  if (now === null) return <span>计算中</span>
  return <span>{countdownLabel(nextRunAt, now)}</span>
}

function notificationStatusLabel(item: OpinionMonitor['recent'][number]) {
  const channels = (item.notification_channels ?? []).map(notificationChannelLabel)
  const channelPrefix = channels.join('、')

  if (item.notification_status === 'sent') {
    return channelPrefix ? `${channelPrefix} 已发送` : '已有发送记录'
  }
  if (item.notification_status === 'failed') {
    return channelPrefix ? `${channelPrefix} 发送失败` : '发送失败'
  }
  return '无发送记录'
}

function SignalFlow({
  sources,
  runs,
  records,
  aiProcessed,
  deliveryChannels,
  failures,
}: {
  sources: { enabled: number; total: number }
  runs: { successRate: number; total: number }
  records: number
  aiProcessed: number
  deliveryChannels: number
  failures: number
}) {
  const stages = [
    { label: '来源', detail: '已启用', value: `${sources.enabled}/${sources.total}`, progress: percent(sources.enabled, sources.total), icon: Database },
    { label: '运行', detail: '成功率', value: `${runs.successRate}%`, progress: runs.total ? runs.successRate : 0, icon: Play },
    { label: '数据', detail: '已采集', value: formatNumber(records), progress: records ? 100 : 0, icon: ArrowDownToLine },
    { label: 'Agent', detail: 'AI 已处理', value: `${percent(aiProcessed, records)}%`, progress: percent(aiProcessed, records), icon: Bot },
    { label: '交付', detail: '已配置渠道', value: formatNumber(deliveryChannels), progress: deliveryChannels ? 100 : 0, icon: Send },
  ]

  return (
    <section className="relative overflow-hidden rounded-xl border bg-card/55 p-4 md:p-5" aria-labelledby="signal-flow-title">
      <div className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(90deg,transparent,rgba(127,127,127,0.08),transparent)]" aria-hidden />
      <div className="relative flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow-mono">Signal flow / 实时链路</p>
          <h2 id="signal-flow-title" className="mt-1 text-lg font-semibold">
            从来源到 Agent，再到交付
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">沿真实业务顺序定位停滞点；交付渠道按用户启用的通知规则统计。</p>
        </div>
        <Badge variant={failures ? 'destructive' : 'outline'}>{failures ? `${failures} 个运行异常` : '链路无阻塞'}</Badge>
      </div>
      <ol className="relative mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {stages.map((stage, index) => {
          const Icon = stage.icon
          return (
            <li key={stage.label} className="group relative rounded-lg border border-border/70 bg-background/65 p-3">
              <div className="flex items-start justify-between gap-3">
                <span className="grid size-8 place-items-center rounded-md bg-muted text-muted-foreground group-hover:text-foreground">
                  <Icon className="size-4" aria-hidden />
                </span>
                <span className="font-mono text-lg tabular-nums">{stage.value}</span>
              </div>
              <div className="mt-4 flex items-end justify-between gap-2">
                <span className="text-sm font-medium">{stage.label}</span>
                <span className="text-[10px] text-muted-foreground">{stage.detail}</span>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary/75 transition-[width]" style={{ width: `${stage.progress}%` }} />
              </div>
              {index < stages.length - 1 ? (
                <span className="absolute -right-2.5 top-1/2 z-10 hidden text-xs text-muted-foreground/50 xl:block" aria-hidden>
                  →
                </span>
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function taskStatus(phase: StreamTask['phase']): AgentTaskStatus {
  if (phase === 'running') return 'running'
  if (phase === 'failed') return 'failed'
  if (phase === 'success') return 'completed'
  return 'queued'
}

function AgentRunQueue({ tasks }: { tasks: StreamTask[] }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bot className="size-4 text-primary" aria-hidden />
            Agent 运行队列
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">把正在执行、已完成和需要关注的工作集中在一条轻量时间线上。</p>
        </div>
        <ToolChip icon={Activity} label="实时任务" detail={`${tasks.length} 条`} tone={tasks.some((task) => task.phase === 'failed') ? 'warning' : 'success'} />
      </CardHeader>
      <CardContent className="space-y-2">
        {tasks.length ? (
          tasks.slice(0, 4).map((task) => (
            <AgentTaskRow
              key={task.id}
              title={task.title}
              detail={`${task.endpoint} · ${task.records ? `${formatNumber(task.records)} 条记录` : '等待数据'}`}
              meta={`${task.workerName || '本地执行器'} · ${formatRelative(new Date(task.startedAt).toISOString())}`}
              status={taskStatus(task.phase)}
              progress={task.phase === 'running' ? 62 : task.phase === 'success' ? 100 : undefined}
            />
          ))
        ) : (
          <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
            当前没有最近运行记录。
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function AgentDeliveryPanel({
  agents,
  delivery,
  notificationChannels,
  agentsLoading,
  logsLoading,
  rulesLoading,
}: {
  agents: Array<{ id: string; name: string; processor_type: string; model?: string; enabled: boolean }>
  delivery: {
    attempts: number
    submitted: number
    awaiting_ack: number
    confirmed: number
    ack_failed: number
    submission_failed: number
    ack_not_required: number
    window: string
  }
  notificationChannels: string[]
  agentsLoading: boolean
  logsLoading: boolean
  rulesLoading: boolean
}) {
  const delivered = delivery.confirmed + delivery.ack_not_required
  const failed = delivery.ack_failed + delivery.submission_failed
  const enabledAgents = agents.filter((agent) => agent.enabled)

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bot className="size-4 text-primary" aria-hidden />
            Agent 与交付
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">谁在处理信号，以及外部渠道报告了什么交付证据。</p>
        </div>
        <Badge variant="outline">
          {rulesLoading ? '同步渠道' : notificationChannels.length ? `${notificationChannels.length} 个渠道` : '未配置渠道'}
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-[1.2fr_0.8fr]">
        <div className="flex flex-wrap gap-1.5 md:col-span-2" aria-label="Agent 交付摘要">
          <ToolChip icon={Bot} label="活动 Agent" detail={agentsLoading ? '同步中' : `${enabledAgents.length} 个`} tone={enabledAgents.length ? 'success' : 'neutral'} />
          <ToolChip icon={Send} label="交付渠道" detail={rulesLoading ? '同步中' : `${notificationChannels.length} 个`} />
          <ToolChip icon={Radio} label="最近送达" detail={logsLoading ? '同步中' : `${delivered} 条`} tone={failed ? 'warning' : 'success'} />
        </div>
        <div className="space-y-2">
          {agentsLoading ? (
            <p className="text-sm text-muted-foreground">正在同步 Agent…</p>
          ) : enabledAgents.length ? (
            enabledAgents.slice(0, 4).map((agent) => (
              <Link key={agent.id} href="/agents" className="flex items-center gap-3 rounded-lg border border-border/70 p-3 transition-colors hover:bg-muted/50">
                <span className="relative grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                  <Bot className="size-4" />
                  <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full border-2 border-background bg-success" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{agent.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {agent.processor_type}
                    {agent.model ? ` · ${agent.model}` : ''}
                  </span>
                </span>
                <span className="font-mono text-[9px] text-success">READY</span>
              </Link>
            ))
          ) : (
            <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">还没有启用 Agent。先到 Agent 团队配置处理能力。</div>
          )}
        </div>
        <div className="rounded-lg border border-border/70 bg-muted/20 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">通知日志</span>
            <Radio className="size-3.5 text-success" aria-hidden />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <div className="font-mono text-2xl tabular-nums">{delivery.submitted}</div>
              <div className="text-[10px] text-muted-foreground">已提交（{delivery.attempts} 次尝试）</div>
            </div>
            <div>
              <div className="font-mono text-2xl tabular-nums text-success">{delivery.confirmed}</div>
              <div className="text-[10px] text-muted-foreground">已确认回执</div>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
            <span>等待回执：{delivery.awaiting_ack}</span>
            <span>无需回执：{delivery.ack_not_required}</span>
            <span>回执失败：{delivery.ack_failed}</span>
            <span>提交失败：{delivery.submission_failed}</span>
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">
            统计窗口：{delivery.window === 'all' ? '全部记录' : delivery.window}；提交成功不等于已确认送达。
            {logsLoading ? '通知日志正在同步…' : ''}
          </p>
          <div className="mt-4 border-t pt-3">
            <div className="text-xs text-muted-foreground">已启用渠道</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {rulesLoading ? (
                <span className="text-xs text-muted-foreground">正在同步…</span>
              ) : notificationChannels.length ? (
                notificationChannels.map((channel) => (
                  <Badge key={channel} variant="secondary">
                    {notificationChannelLabel(channel)}
                  </Badge>
                ))
              ) : (
                <span className="text-xs text-muted-foreground">尚未配置通知规则</span>
              )}
            </div>
            <p className="mt-2 text-[10px] leading-4 text-muted-foreground">发送目标由通知规则决定，不预设为某一个平台。</p>
          </div>
          <Link href="/notifications" className="mt-4 inline-flex items-center gap-1 text-xs text-primary hover:underline">
            查看通知渠道配置
            <ArrowRight className="size-3" />
          </Link>
        </div>
      </CardContent>
    </Card>
  )
}

function OpinionMonitorPanel({ data, isLoading, isError }: { data?: OpinionMonitor; isLoading: boolean; isError: boolean }) {
  const topTags = data?.tags.slice(0, 6) ?? []
  const topSentiment = data?.sentiment.slice(0, 4) ?? []
  const recent = data?.recent ?? []
  const notificationLogCount = (data?.summary.notification_sent ?? 0) + (data?.summary.notification_failed ?? 0)

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <BrainCircuit className="size-4 text-primary" aria-hidden />
            舆情监控
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">采集、AI 标注与通知日志的近 7 天记录</p>
        </div>
        {isError ? (
          <Badge variant="outline">未连接</Badge>
        ) : isLoading ? (
          <Badge variant="outline">同步中</Badge>
        ) : (
          <Badge variant="outline" className="gap-1.5">
            <span className="size-1.5 rounded-full bg-success" aria-hidden />
            30 秒刷新
          </Badge>
        )}
      </CardHeader>
      <CardContent className="grid items-start gap-4 lg:grid-cols-[280px_1fr]">
        <div className="grid content-start gap-3 sm:grid-cols-3 lg:grid-cols-1">
          <div className="rounded-md border p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <ArrowDownToLine className="size-3.5" aria-hidden />
              记录 / AI
            </div>
            <div className="mt-2 font-mono text-xl">
              {formatNumber(data?.summary.records ?? 0)} / {formatNumber(data?.summary.ai_processed ?? 0)}
            </div>
          </div>
          <div className="rounded-md border p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <BellRing className="size-3.5" aria-hidden />
              通知日志
            </div>
            <div className="mt-2 font-mono text-xl">
              {formatNumber(notificationLogCount)}
              <span className="ml-2 text-xs text-muted-foreground">
                成功 {data?.summary.notification_sent ?? 0} · 失败 {data?.summary.notification_failed ?? 0}
              </span>
            </div>
            <p className="mt-1 text-[10px] text-muted-foreground">来自通知日志，非实时回执</p>
          </div>
          <div className="rounded-md border p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Tags className="size-3.5" aria-hidden />
              标签 / 情绪
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {[...topTags, ...topSentiment].slice(0, 7).map((item) => (
                <Badge key={`${item.label}-${item.count}`} variant="secondary">
                  {item.label} · {item.count}
                </Badge>
              ))}
              {!topTags.length && !topSentiment.length ? <span className="text-sm text-muted-foreground">暂无</span> : null}
            </div>
          </div>
        </div>

        <div className="min-w-0 rounded-md border">
          {recent.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">暂无已采集舆情记录</div>
          ) : (
            <div className="divide-y">
              {recent.map((item) => (
                <div key={item.id} className="grid gap-2 p-3 md:grid-cols-[1fr_auto]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-medium">{item.title}</span>
                      <Badge variant={item.notification_status === 'sent' ? 'secondary' : 'outline'}>
                        {notificationStatusLabel(item)}
                      </Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{item.summary || item.source_name}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.tags.slice(0, 4).map((tag) => (
                        <Badge key={tag} variant="outline">
                          {tag}
                        </Badge>
                      ))}
                      <Badge variant="outline">{item.sentiment}</Badge>
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground md:text-right">
                    <div>{item.source_name}</div>
                    <div className="mt-1">{formatRelative(item.created_at)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** Map backend recent runs into the shared stream shape. */
function runsToStream(
  runs: Array<{
    id: string
    task_id: string
    source_name: string
    task_trigger_type: string
    status: string
    records_collected: number
    duration_ms?: number | null
    created_at?: string
  }>,
): StreamTask[] {
  return runs.map((r) => ({
    id: r.id,
    href: `/tasks/${r.task_id}`,
    lane: 'collect' as const,
    title: `${r.source_name} 采集`,
    endpoint: r.source_name,
    workerId: '',
    workerName: r.task_trigger_type,
    phase:
      r.status === 'success' || r.status === 'completed'
        ? ('success' as const)
        : r.status === 'failed'
          ? ('failed' as const)
          : r.status === 'running'
            ? ('running' as const)
            : ('queued' as const),
    records: r.records_collected,
    retries: 0,
    startedAt: r.created_at ? new Date(r.created_at).getTime() : Date.now(),
    durationMs: r.duration_ms ?? null,
  }))
}

export default function DashboardPage() {
  const stats = useDashboardStats()
  const activity = useDashboardActivity()
  const opinion = useOpinionMonitor()
  const workersQuery = useWorkers()
  const agentsQuery = useAgents({ enabled: true })
  const notificationLogsQuery = useNotificationLogs()
  const notificationRulesQuery = useNotificationRules()
  const schedulesQuery = useSchedules({ enabled: true })

  if (stats.isLoading) {
    return (
      <PageContainer eyebrow="Control plane" title="运营工作台" description="先处理异常，再推进正在运行的工作。">
        <LoadingState rows={3} />
      </PageContainer>
    )
  }

  if (stats.isError || !stats.data) {
    return (
      <PageContainer eyebrow="Control plane" title="运营工作台" description="先处理异常，再推进正在运行的工作。">
        <ErrorState message={(stats.error as Error)?.message} hint={BACKEND_HINT} action={<Button onClick={() => stats.refetch()}>重新连接</Button>} />
      </PageContainer>
    )
  }

  const s = stats.data
  const kpis: Array<{ title: string; value: string; sub?: string; icon: typeof Activity }> = [
    {
      title: '采集记录',
      value: formatNumber(s.records.total),
      sub: `AI 处理 ${formatNumber(s.records.ai_processed)}`,
      icon: ArrowDownToLine,
    },
    {
      title: '任务总量',
      value: formatNumber(s.tasks.total),
      sub: `运行中 ${s.tasks.running} · 失败 ${s.tasks.failed}`,
      icon: Send,
    },
    {
      title: '运行成功率',
      value: `${Math.round(s.runs.success_rate ?? 0)}%`,
      sub: `成功 ${s.runs.success} · 失败 ${s.runs.failed}`,
      icon: CheckCircle2,
    },
    {
      title: '数据源',
      value: formatNumber(s.sources.total),
      sub: `启用 ${s.sources.enabled} · 停用 ${s.sources.disabled}`,
      icon: Server,
    },
  ]
  const throughput: ThroughputPoint[] = (activity.data?.daily ?? []).map((d) => ({
    time: d.date.slice(5),
    collected: d.success_runs,
    dispatched: d.new_records,
    failed: d.failed_runs,
  }))
  const workers: WorkerView[] = (workersQuery.data?.data ?? []).map((w: WorkerNode) => {
    const concurrency = typeof w.concurrency === 'number' && w.concurrency > 0 ? w.concurrency : null
    return {
      id: w.id,
      name: w.hostname,
      lane: 'collect' as const,
      region: w.worker_id.slice(0, 8),
      online: w.status === 'online',
      load: concurrency === null ? null : Math.min(100, Math.round((w.active_tasks / concurrency) * 100)),
      queue: concurrency === null ? 0 : Math.max(0, w.active_tasks - concurrency),
      current:
        w.active_tasks > 0
          ? `${concurrency === null ? w.active_tasks : Math.min(w.active_tasks, concurrency)} 个任务运行中`
          : null,
      doneToday: null,
      failedToday: null,
    }
  })
  const stream = runsToStream(s.recent_runs ?? [])
  const failures: FailureItem[] = stream
    .filter((task) => task.phase === 'failed')
    .map((task) => ({
      id: `f-${task.id}`,
      href: task.href,
      lane: task.lane,
      title: task.title,
      workerName: task.workerName,
      error: '查看任务详情获取错误信息',
      retries: task.retries,
      at: task.startedAt,
    }))
  const hasAttention = s.tasks.failed > 0 || failures.length > 0
  const agents = agentsQuery.data?.data ?? []
  const notificationRules = notificationRulesQuery.data?.data ?? []
  const activeDeliveryChannels = Array.from(
    new Set(notificationRules.filter((rule) => rule.enabled).map((rule) => rule.notifier_type)),
  ).sort()
  const nextSchedule = [...(schedulesQuery.data?.data ?? [])]
    .filter((schedule) => schedule.enabled && schedule.next_run_at && !Number.isNaN(new Date(schedule.next_run_at).getTime()))
    .sort((left, right) => new Date(left.next_run_at as string).getTime() - new Date(right.next_run_at as string).getTime())[0]
  const insights: Testimonial[] = [
    {
      quote: hasAttention
        ? `${formatNumber(s.tasks.failed)} 个失败任务正在等待处理，建议先检查最近一次运行记录。`
        : '当前运行链路没有阻塞，可以继续推进工作流和数据源配置。',
      name: '运行态势',
      role: hasAttention ? '需要关注' : '运行正常',
    },
    {
      quote: agents.length ? `${agents.length} 个 Agent 已接入当前控制面，结果会按通知规则继续交付。` : '还没有启用 Agent，接入一个执行能力后即可开始自动化。',
      name: 'Agent 交付',
      role: `${activeDeliveryChannels.length} 个渠道`,
    },
    {
      quote: `累计采集 ${formatNumber(s.records.total)} 条记录，其中 ${formatNumber(s.records.ai_processed)} 条已经完成 AI 处理。`,
      name: '数据链路',
      role: `${normalizedSuccessRate(s.runs.success_rate ?? 0)}% 成功率`,
    },
    {
      quote: nextSchedule ? `下一次调度将在 ${countdownLabel(nextSchedule.next_run_at as string, Date.now())} 后执行。` : '当前没有已启用的下一次调度，可以从自动化与智能体开始配置。',
      name: '下一步',
      role: nextSchedule?.name ?? '调度配置',
    },
  ]
  return (
    <PageContainer
      eyebrow="Control plane"
      title="运营工作台"
      description="先处理异常，再推进正在运行的工作。"
      actions={
        <>
          <MatrixClock />
          <Badge variant="outline" className="gap-1.5">
            <span className="size-1.5 animate-pulse rounded-full bg-success" aria-hidden />
            实时
          </Badge>
        </>
      }
    >
      <section aria-labelledby="ask-alice-title" className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,390px)]">
        <div className="min-w-0">
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <p className="eyebrow-mono">Primary workspace / 主对话</p>
              <h2 id="ask-alice-title" className="mt-1 text-lg font-semibold">Ask Alice</h2>
              <p className="mt-1 text-sm text-muted-foreground">查询当前工作区，创建项目提案，并从这里继续已有会话。</p>
            </div>
            <Link href="/chat" className={buttonVariants({ variant: 'outline', size: 'sm' })}>完整对话 <ArrowRight aria-hidden /></Link>
          </div>
          <AgentConversationSurface presentation="dashboard" />
        </div>
        <AgentToolReference compact />
      </section>

      <section className="grid items-start gap-3 lg:grid-cols-[minmax(0,1.45fr)_minmax(300px,0.75fr)]" aria-labelledby="attention-title">
        <Card
          size="sm"
          className={cn(
            'relative isolate overflow-hidden border-0 py-0 ring-1',
            hasAttention ? 'bg-destructive/[0.055] ring-destructive/25' : 'bg-primary/[0.045] ring-primary/20',
          )}
        >
          <div className={cn('absolute inset-y-0 left-0 w-1', hasAttention ? 'bg-destructive' : 'bg-success')} aria-hidden />
          <CardContent className="grid gap-4 p-4 pl-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:px-5">
            <div className="flex min-w-0 items-start gap-3">
              <span className={cn('grid size-9 shrink-0 place-items-center rounded-lg', hasAttention ? 'bg-destructive/10 text-destructive' : 'bg-success/10 text-success')}>
                {hasAttention ? <AlertTriangle className="size-4" aria-hidden /> : <CheckCircle2 className="size-4" aria-hidden />}
              </span>
              <div className="min-w-0">
                <p className="eyebrow-mono" id="attention-title">
                  需要你处理
                </p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">
                  {hasAttention ? `${formatNumber(s.tasks.failed)} 个失败任务需要检查` : '当前没有阻塞，可以继续推进工作'}
                </h2>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                  {hasAttention ? '先查看失败原因和最近运行记录，再决定重试、调整来源或修改工作流。' : '运行链路没有发现失败任务。你可以创建工作流、接入来源，或检查下一次调度。'}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 md:justify-end">
              <Link
                href={hasAttention ? '/tasks?status=failed' : '/tasks'}
                className={buttonVariants({
                  variant: hasAttention ? 'destructive' : 'default',
                  size: 'sm',
                })}
              >
                {hasAttention ? '查看失败工作项' : '查看工作项'}
                <ArrowRight aria-hidden />
              </Link>
              <Link href="/control/actions" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                控制记录
              </Link>
            </div>
          </CardContent>
        </Card>

        <Card size="sm">
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <p className="eyebrow-mono">现在正在发生</p>
              <CardTitle className="mt-1 text-base">运行态势</CardTitle>
            </div>
            <Badge variant="outline" className="font-normal">30 秒刷新</Badge>
          </CardHeader>
          <CardContent className="grid grid-cols-3 divide-x divide-border/70">
            <div className="pr-3">
              <div className="text-xs text-muted-foreground">正在运行</div>
              <div className="mt-1 font-mono text-xl tabular-nums">{formatNumber(s.tasks.running)}</div>
            </div>
            <div className="px-3">
              <div className="text-xs text-muted-foreground">下次执行</div>
              <div className="mt-1 truncate font-mono text-sm font-medium tabular-nums" title={nextSchedule?.name}>
                <NextRunCountdown nextRunAt={nextSchedule?.next_run_at} />
              </div>
            </div>
            <div className="pl-3">
              <div className="text-xs text-muted-foreground">在线 Worker</div>
              <div className="mt-1 font-mono text-sm tabular-nums">
                {workers.filter((worker) => worker.online).length} / {workers.length}
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="next-action-title">
        <div className="mb-3 flex items-end justify-between gap-3">
          <div>
            <p className="eyebrow-mono">下一步</p>
            <h2 id="next-action-title" className="mt-1 text-lg font-semibold">
              从这里继续工作
            </h2>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <ActionLink href="/studio" title="编排工作流" description="先选择项目，再设计节点和执行链路" icon={GitBranch} />
          <ActionLink href="/sources" title="接入数据源" description="配置采集来源与凭证" icon={Database} />
          <ActionLink href="/schedules" title="检查调度计划" description="确认下一次运行时间与启用状态" icon={Clock3} />
          <ActionLink href="/operations-agents" title="设置自动化与智能体" description="安排任务、选择智能体和配置执行链路" icon={Clock3} />
          <ActionLink href="/tasks" title="检查运行结果" description="查看任务、记录与通知" icon={Activity} />
        </div>
      </section>

      <section aria-labelledby="system-overview-title">
        <div className="mb-3">
          <p className="eyebrow-mono">系统概览</p>
          <h2 id="system-overview-title" className="mt-1 text-lg font-semibold">
            关键指标
          </h2>
        </div>
        <SignalFlow
          sources={s.sources}
          runs={{ successRate: normalizedSuccessRate(s.runs.success_rate ?? 0), total: s.runs.total }}
          records={s.records.total}
          aiProcessed={s.records.ai_processed}
          deliveryChannels={activeDeliveryChannels.length}
          failures={failures.length}
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {kpis.map((k) => (
            <KpiCard key={k.title} {...k} />
          ))}
        </div>
      </section>

      <OperationalAnalytics
        stats={s}
        opinion={opinion.data}
        opinionLoading={opinion.isLoading}
        opinionError={opinion.isError}
      />

      <AgentDeliveryPanel
        agents={agents}
        delivery={s.delivery}
        notificationChannels={activeDeliveryChannels}
        agentsLoading={agentsQuery.isLoading}
        logsLoading={notificationLogsQuery.isLoading}
        rulesLoading={notificationRulesQuery.isLoading}
      />

      <AgentRunQueue tasks={stream} />

      <section className="overflow-hidden rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-500/[0.06] via-card to-blue-500/[0.05] py-5" aria-labelledby="agent-insights-title">
        <div className="mb-2 px-5">
          <p className="eyebrow-mono">Agent insights / 自动轮播</p>
          <h2 id="agent-insights-title" className="mt-1 text-lg font-semibold">把系统状态变成下一步建议</h2>
          <p className="mt-1 text-sm text-muted-foreground">来自 Eldora UI 的 Testimonial Slider 交互，用于浏览运行态势、交付和调度建议。</p>
        </div>
        <FancyTestimonialsSlider testimonials={insights} autorotateTiming={6000} />
      </section>

      <section className="grid gap-4" aria-label="运行与异常">
        <FailureFeed failures={failures} totalFailed={s.tasks.failed} />
        <TaskStream tasks={stream} />
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ThroughputChart data={throughput} daily />
        </div>
        <WorkerAllocation workers={workers} />
      </div>

      <OpinionMonitorPanel data={opinion.data} isLoading={opinion.isLoading} isError={opinion.isError} />
    </PageContainer>
  )
}
