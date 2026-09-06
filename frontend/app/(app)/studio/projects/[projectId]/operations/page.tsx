'use client'

import { Activity, AlertTriangle, ArrowLeft, Braces, CheckCircle2, Eye, Filter, LoaderCircle, Search, Workflow, XCircle } from 'lucide-react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { use, useDeferredValue, useEffect, useRef, useState, type ReactNode } from 'react'

import AITaskList from '@/components/smoothui/ai-task-list'
import { EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { PageContainer } from '@/components/shell/page-container'
import { ProjectNavigation } from '@/components/studio/project-navigation'
import { RunAnalysisSnapshotPanel } from '@/components/studio/run-analysis-snapshot-panel'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useProjectRuntimeLogs, useProjectRuntimeSummary, useProjectRuntimeTrace, useProjectWorkflows, useWorkspaceProjects } from '@/lib/api/hooks'
import type { ProjectRuntimeLog } from '@/lib/api/types'
import { formatDateTime, formatRelative } from '@/lib/format'
import { buildOperationsNodeTasks } from '@/lib/studio/operations-task-model'
import { cn } from '@/lib/utils'
import { extractGaojixingRecoveryCase } from '@/lib/workflow/live-run-monitor'
import { buildRunUrl, clearRunNavigation, parseRunNavigation, shouldDiscardTraceHistoryEntry, traceCloseAction, type TraceHistoryEntry } from '@/lib/studio/run-navigation'

const PAGE_SIZE = 20
const TRACE_PAGE_SIZE = 50

export default function ProjectOperationsPage({
  params,
}: {
  params: Promise<{ projectId: string }>
}) {
  const { projectId } = use(params)
  const searchParams = useSearchParams()
  const pathname = usePathname()
  const router = useRouter()
  const searchParamsKey = searchParams.toString()
  const currentHref = searchParamsKey ? `${pathname}?${searchParamsKey}` : pathname
  const navigationContext = parseRunNavigation(searchParams)
  const workspaceId = searchParams.get('workspace')
  const preferredWorkflowId = navigationContext.workflow
  const requestedWorkflowId = navigationContext.workflow
  const requestedRunId = navigationContext.run
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [status, setStatus] = useState('all')
  const [page, setPage] = useState(1)
  const [traceCursorHistory, setTraceCursorHistory] = useState([0])
  const traceHistoryRef = useRef<TraceHistoryEntry | null>(null)
  const projectsQuery = useWorkspaceProjects(workspaceId)
  const workflowsQuery = useProjectWorkflows(workspaceId, projectId)
  const summaryQuery = useProjectRuntimeSummary(workspaceId, projectId)
  const logsQuery = useProjectRuntimeLogs(workspaceId, projectId, {
    ...(status === 'all' ? {} : { status }),
    ...(deferredSearch ? { search: deferredSearch } : {}),
    page,
    limit: PAGE_SIZE,
  })
  const selectedRun = requestedWorkflowId && requestedRunId
    ? { workflowId: requestedWorkflowId, runId: requestedRunId, traceId: navigationContext.trace ?? null }
    : null
  const traceQuery = useProjectRuntimeTrace(
    workspaceId,
    projectId,
    selectedRun?.workflowId ?? null,
    selectedRun?.runId ?? null,
    {
      afterSequence: traceCursorHistory.at(-1) ?? 0,
      limit: TRACE_PAGE_SIZE,
    },
  )
  const project = projectsQuery.data?.find((candidate) => candidate.id === projectId)
  const workflows = workflowsQuery.data ?? []
  const workflowId = preferredWorkflowId ?? project?.primary_workflow_id ?? workflows[0]?.id ?? null
  const summary = summaryQuery.data
  const logs = logsQuery.data?.logs ?? []
  const selectedLog = selectedRun
    ? logs.find((log) => log.workflow_id === selectedRun.workflowId && log.run_id === selectedRun.runId) ?? null
    : null
  const meta = logsQuery.data?.meta
  const pages = Math.max(1, meta?.pages ?? 1)
  const overviewHref = workspaceId ? `/studio/projects/${projectId}?workspace=${workspaceId}` : '/studio'
  const apiHref = workspaceId
    ? `/studio/projects/${projectId}/api?workspace=${workspaceId}${workflowId ? `&workflow=${workflowId}` : ''}`
    : null
  const loading = projectsQuery.isLoading || workflowsQuery.isLoading
  const error = projectsQuery.error || workflowsQuery.error
  const traceEvents = traceQuery.data?.trace.events ?? []
  const traceProjection = traceQuery.data?.trace.projection
  const traceCursor = traceCursorHistory.at(-1) ?? 0
  const traceNextCursor = traceQuery.data?.trace.nextAfterSequence ?? traceCursor
  const traceTotalEvents = traceQuery.data?.trace.projection.eventCount ?? selectedLog?.event_count ?? 0
  const traceHasNextPage = traceNextCursor > traceCursor && traceNextCursor < traceTotalEvents
  const traceStatus = traceQuery.data?.trace.projection.status ?? selectedLog?.status ?? null
  const collectionRecovery = traceStatus === 'waiting' ? extractGaojixingRecoveryCase(traceEvents) : null
  const traceNodeTasks = buildOperationsNodeTasks(
    traceQuery.data?.trace.projection.nodeStates ?? [],
  )
  const traceContext = selectedRun ? { workspace: workspaceId ?? undefined, project: projectId, workflow: selectedRun.workflowId, run: selectedRun.runId, trace: traceProjection?.traceId ?? selectedRun.traceId ?? selectedLog?.trace_id ?? undefined, conversation: navigationContext.conversation } : null
  const snapshotRunStatus = traceProjection?.status ?? selectedLog?.status ?? null
  const snapshotRunStartAt = traceProjection?.startedAt ?? selectedLog?.started_at ?? null
  const snapshotRunEndAt = traceProjection?.updatedAt ?? selectedLog?.updated_at ?? null
  const evidenceHref = traceContext ? buildRunUrl('evidence', traceContext)! : null
  const dataHref = traceContext ? buildRunUrl('data', traceContext)! : null
  const workflowHref = traceContext ? buildRunUrl('workflow', traceContext)! : null

  useEffect(() => {
    setPage(1)
  }, [deferredSearch, status])

  useEffect(() => {
    setTraceCursorHistory([0])
  }, [requestedWorkflowId, requestedRunId])

  useEffect(() => {
    if (shouldDiscardTraceHistoryEntry(currentHref, traceHistoryRef.current)) {
      traceHistoryRef.current = null
    }
  }, [currentHref])

  function openTrace(log: ProjectRuntimeLog) {
    const href = buildRunUrl('operations', {
      workspace: workspaceId ?? undefined,
      project: projectId,
      workflow: log.workflow_id,
      run: log.run_id,
      trace: log.trace_id,
    })
    if (href) {
      traceHistoryRef.current = { sourceHref: currentHref, targetHref: href }
      router.push(href, { scroll: false })
    }
  }

  function closeTrace() {
    const action = traceCloseAction(currentHref, traceHistoryRef.current)
    if (action === 'back') {
      traceHistoryRef.current = null
      router.back()
      return
    }
    traceHistoryRef.current = null
    const query = clearRunNavigation(searchParams)
    router.replace(query.size ? `${pathname}?${query.toString()}` : pathname, { scroll: false })
  }

  return (
    <PageContainer
      title={project ? `${project.name} · 日志监测` : '项目日志监测'}
      eyebrow="Project observability"
      description="执行状态：查看项目内每次 Workflow Run 的发布版本、状态、耗时、输入与节点事件，并沿 trace_id 定位执行完成、部分完成或失败原因。"
      className="max-w-none"
      actions={<div className="flex gap-2">{apiHref ? <Link href={apiHref} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'min-h-11')}><Braces className="size-4" />访问 API</Link> : null}<Link href={overviewHref} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'min-h-11')}><ArrowLeft className="size-4" />返回项目</Link></div>}
    >
      <div className="border-b pb-3">
        <ProjectNavigation active="operations" workspaceId={workspaceId} projectId={projectId} workflowId={workflowId} />
      </div>

      {loading ? <LoadingState rows={6} /> : error ? (
        <ErrorState message={error instanceof Error ? error.message : '日志上下文加载失败'} hint="确认后端、工作区和项目上下文可用。" />
      ) : !project ? (
        <EmptyState title="找不到项目" description="返回 Studio 重新选择工作区和项目。" />
      ) : (
        <>
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="运行健康摘要">
            <Metric label="全部运行" value={summary?.total_runs ?? 0} icon={Activity} />
            <Metric label="完成或部分完成" value={summary?.successful_runs ?? 0} icon={CheckCircle2} />
            <Metric label="失败" value={summary?.failed_runs ?? 0} icon={XCircle} tone="danger" />
            <Metric label="阻塞" value={summary?.blocked_runs ?? 0} icon={AlertTriangle} tone="warning" />
            <Metric label="运行中" value={summary?.running_runs ?? 0} icon={LoaderCircle} />
          </section>

          <section className="overflow-hidden rounded-xl border bg-card">
            <header className="grid gap-3 border-b p-3 lg:grid-cols-[minmax(0,1fr)_13rem_auto] lg:items-center">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input aria-label="搜索运行日志" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 run_id、trace_id 或 workflow_id…" className="pl-9" />
              </div>
              <Select value={status} onValueChange={(value) => setStatus(value ?? 'all')}>
                <SelectTrigger><Filter className="size-4" /><SelectValue>{status === 'all' ? '全部状态' : statusLabel(status)}</SelectValue></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  {['completed', 'partial', 'partial_success', 'running', 'waiting', 'blocked', 'failed'].map((value) => <SelectItem key={value} value={value}>{statusLabel(value)}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-xs text-muted-foreground">每 10 秒刷新 · {meta?.total ?? 0} 条</div>
            </header>

            {logsQuery.isLoading ? <div className="p-5"><LoadingState rows={7} /></div> : logsQuery.isError ? (
              <div className="p-5"><ErrorState message={logsQuery.error?.message ?? '运行日志加载失败'} hint="运行数据仍保存在后端；恢复连接后本页会继续刷新。" /></div>
            ) : logs.length === 0 ? (
              <EmptyState title="当前条件下没有运行日志" description="通过项目 API 或编排页启动一次运行后，这里会出现可追踪的日志。" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>工作流 / 版本</TableHead>
                    <TableHead>运行</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>触发</TableHead>
                    <TableHead>事件</TableHead>
                    <TableHead>耗时</TableHead>
                    <TableHead>开始时间</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((log) => (
                    <TableRow key={log.run_id}>
                      <TableCell><div className="font-medium">{log.workflow_name}</div><div className="mt-1 text-xs text-muted-foreground">{log.workflow_version ? `Published v${log.workflow_version}` : 'Draft / direct run'}</div></TableCell>
                      <TableCell><div className="max-w-48 truncate font-mono text-xs">{log.run_id}</div><div className="mt-1 max-w-48 truncate font-mono text-[10px] text-muted-foreground">trace {log.trace_id}</div></TableCell>
                      <TableCell><StatusBadge status={log.status} /></TableCell>
                      <TableCell><div className="text-xs">{log.trigger}</div><div className="mt-1 text-[10px] text-muted-foreground">{log.response_mode}</div></TableCell>
                      <TableCell className="font-mono text-xs">{log.event_count}<span className="ml-1 text-muted-foreground">/ {log.node_count} nodes</span></TableCell>
                      <TableCell className="font-mono text-xs">{formatDuration(log.duration_ms)}</TableCell>
                      <TableCell><div className="text-xs">{formatRelative(log.started_at)}</div><div className="mt-1 text-[10px] text-muted-foreground">{formatDateTime(log.started_at)}</div></TableCell>
                      <TableCell className="text-right"><Button type="button" size="sm" variant="ghost" onClick={() => openTrace(log)}><Eye className="size-4" />Trace</Button></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            <footer className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground">
              <span>第 {page} / {pages} 页 · 共 {meta?.total ?? 0} 次运行</span>
              <div className="flex gap-2"><Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</Button><Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>下一页</Button></div>
            </footer>
          </section>
        </>
      )}

      <Sheet open={Boolean(selectedRun)} onOpenChange={(open) => {
        if (!open) {
          closeTrace()
        }
      }}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
          {selectedRun ? (
            <>
              <SheetHeader className="border-b">
                <SheetTitle className="pr-10">{selectedLog?.workflow_name ?? selectedRun.workflowId} · 执行状态 Trace</SheetTitle>
                <SheetDescription className="break-all font-mono">{selectedRun.runId}{traceContext?.trace ? ` · ${traceContext.trace}` : ''}</SheetDescription>
                <div className="flex flex-wrap gap-2 text-xs">
                  {workspaceId ? (
                    <>
                      <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={evidenceHref!}>查看项目证据关系</Link>
                      <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={dataHref!}>查看项目数据</Link>
                      <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={workflowHref!}>查看工作流</Link>
                    </>
                  ) : null}
                </div>
              </SheetHeader>
              <div className="space-y-5 px-4 pb-6">
                {collectionRecovery ? (
                  <div role="status" className="rounded-lg border border-warning/30 bg-warning/10 p-4 text-sm">
                    <p className="font-medium">{collectionRecovery.status === 'waiting_verification' ? '豆包采集已暂停，等待页面验证' : '豆包采集已暂停，等待核对原会话'}</p>
                    <p className="mt-2 leading-6 text-muted-foreground">
                      {collectionRecovery.status === 'waiting_verification'
                        ? '请在本次采集使用的浏览器页面完成验证，保留原会话。完成后从工作流运行面板继续采集，不要重新发送问题。'
                        : '本次运行尚未确认完整采集结果。请先检查原会话与运行证据，避免重复提交同一问题。'}
                    </p>
                    {workflowHref ? <Link href={workflowHref} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'mt-3')}>打开本次工作流运行</Link> : null}
                  </div>
                ) : null}
                <div className="grid gap-2 sm:grid-cols-4">
                  <TraceMetric label="状态">{traceStatus ? <StatusBadge status={traceStatus} /> : '加载中…'}</TraceMetric>
                  <TraceMetric label="版本" value={traceQuery.data ? (traceQuery.data.workflow_version ? `Published v${traceQuery.data.workflow_version}` : 'Draft') : '加载中…'} />
                  <TraceMetric label="用户" value={traceQuery.data ? (traceQuery.data.user?.trim() || '未提供') : '加载中…'} />
                  <TraceMetric label="事件" value={traceQuery.data ? String(traceQuery.data.trace.projection.eventCount) : '加载中…'} />
                </div>

                {snapshotRunStatus && snapshotRunStartAt && snapshotRunEndAt ? <RunAnalysisSnapshotPanel
                  workspaceId={workspaceId}
                  projectId={projectId}
                  workflowId={selectedRun.workflowId}
                  runId={selectedRun.runId}
                  runStatus={snapshotRunStatus}
                  runStartAt={snapshotRunStartAt}
                  runEndAt={snapshotRunEndAt}
                /> : null}

                <div>
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium"><Braces className="size-4" />运行输入</div>
                  {traceQuery.isLoading ? <LoadingState rows={3} /> : traceQuery.isError ? <ErrorState message={traceQuery.error?.message ?? 'Trace 加载失败'} hint="确认 URL 中的工作流和运行仍属于当前项目。" /> : <pre className="max-h-56 overflow-auto rounded-lg border bg-muted/25 p-4 font-mono text-xs leading-5">{JSON.stringify(traceQuery.data?.inputs ?? {}, null, 2)}</pre>}
                </div>

                <div>
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium"><Workflow className="size-4" />节点状态与事件</div>
                  {traceQuery.isLoading ? <LoadingState rows={5} /> : traceQuery.isError ? (
                    <ErrorState message={traceQuery.error?.message ?? 'Trace 加载失败'} hint="确认该运行仍属于当前项目和工作流。" />
                  ) : (
                    <div className="space-y-3">
                      {traceNodeTasks.length ? (
                        <AITaskList
                          label={`节点执行状态 · ${traceStatus ?? '未知状态'}`}
                          tasks={traceNodeTasks}
                          className="bg-card"
                        />
                      ) : (
                        <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">这个运行还没有节点状态投影。</div>
                      )}

                      {traceEvents.length ? (
                        <details className="rounded-xl border bg-card">
                          <summary className="cursor-pointer px-3 py-2.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                            当前批次事件详情 · {traceEvents.length}
                          </summary>
                          <div className="space-y-2 border-t p-3">
                            {traceEvents.map((event) => (
                              <article key={event.id} className="grid gap-3 rounded-lg border p-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_8rem]">
                                <div className="font-mono text-xs text-muted-foreground">#{event.sequence}</div>
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-start gap-2">
                                    <StatusBadge status={event.eventType} />
                                    <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{event.nodeId}</span>
                                  </div>
                                  {event.message ? <p className="mt-2 text-xs leading-5 text-muted-foreground">{event.message}</p> : null}
                                  {event.blockReason ? (
                                    <div className="mt-2 rounded border border-warning/30 bg-warning/5 p-2 text-xs text-warning">
                                      {event.blockReason.code} · {event.blockReason.message}
                                    </div>
                                  ) : null}
                                  {Object.keys(event.details ?? {}).length ? (
                                    <details className="mt-2 rounded border bg-muted/25">
                                      <summary className="cursor-pointer px-2 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">原始事件详情</summary>
                                      <pre className="max-h-40 overflow-auto border-t p-2 font-mono text-[10px] leading-4">{JSON.stringify(event.details, null, 2)}</pre>
                                    </details>
                                  ) : null}
                                </div>
                                <time className="text-left text-[10px] text-muted-foreground sm:text-right" dateTime={event.createdAt}>{formatDateTime(event.createdAt)}</time>
                              </article>
                            ))}
                          </div>
                        </details>
                      ) : (
                        <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">当前批次没有持久化事件。</div>
                      )}

                      {traceQuery.data ? (
                        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                          <span>{traceEvents.length ? `事件 #${traceEvents[0]?.sequence}–#${traceEvents.at(-1)?.sequence}` : '当前批次无事件'} · 共 {traceTotalEvents} 条</span>
                          <div className="flex gap-2">
                            <Button type="button" size="sm" variant="outline" disabled={traceCursorHistory.length === 1} onClick={() => setTraceCursorHistory((history) => history.slice(0, -1))}>上一批</Button>
                            <Button type="button" size="sm" variant="outline" disabled={!traceHasNextPage} onClick={() => setTraceCursorHistory((history) => [...history, traceNextCursor])}>下一批</Button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </PageContainer>
  )
}

function Metric({ label, value, icon: Icon, tone }: { label: string; value: number; icon: typeof Activity; tone?: 'success' | 'danger' | 'warning' }) {
  return <Card><CardContent className="flex items-center justify-between p-4"><div><div className="text-xs text-muted-foreground">{label}</div><div className="mt-2 font-mono text-2xl font-semibold">{value.toLocaleString('zh-CN')}</div></div><Icon className={cn('size-5 text-muted-foreground', tone === 'success' && 'text-success', tone === 'danger' && 'text-destructive', tone === 'warning' && 'text-warning')} /></CardContent></Card>
}

function TraceMetric({ label, value, children }: { label: string; value?: string; children?: ReactNode }) {
  return <div className="rounded-lg border p-3"><div className="text-[10px] text-muted-foreground">{label}</div><div className="mt-2 text-xs font-medium">{children ?? value ?? '—'}</div></div>
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === 'completed'
    ? 'border-success/30 bg-success/10 text-success'
    : status === 'partial' || status === 'partial_success'
      ? 'border-warning/30 bg-warning/10 text-warning'
      : status === 'failed'
        ? 'border-destructive/30 bg-destructive/10 text-destructive'
        : status === 'blocked'
          ? 'border-warning/30 bg-warning/10 text-warning'
          : 'border-primary/30 bg-primary/10 text-primary'
  return <Badge variant="outline" className={cn('font-mono text-[10px]', tone)}>{statusLabel(status)}</Badge>
}

function statusLabel(status: string) {
  return { completed: '执行完成', partial: '部分完成', partial_success: '部分完成', failed: '失败', blocked: '阻塞', running: '执行中', waiting: '等待中', queued: '排队中' }[status] ?? status
}


function formatDuration(durationMs: number) {
  if (durationMs < 1_000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(1)} s`
  return `${Math.floor(durationMs / 60_000)}m ${Math.round((durationMs % 60_000) / 1_000)}s`
}
