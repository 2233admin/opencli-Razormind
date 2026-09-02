'use client'

import { Activity, AlertTriangle, ArrowLeft, Braces, CheckCircle2, Eye, Filter, LoaderCircle, Search, Workflow, XCircle } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { use, useDeferredValue, useEffect, useState, type ReactNode } from 'react'

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
import { cn } from '@/lib/utils'

const PAGE_SIZE = 20
const TRACE_PAGE_SIZE = 50

export default function ProjectOperationsPage({
  params,
}: {
  params: Promise<{ projectId: string }>
}) {
  const { projectId } = use(params)
  const searchParams = useSearchParams()
  const workspaceId = searchParams.get('workspace')
  const preferredWorkflowId = searchParams.get('workflow')
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [status, setStatus] = useState('all')
  const [page, setPage] = useState(1)
  const [selectedLog, setSelectedLog] = useState<ProjectRuntimeLog | null>(null)
  const [traceCursorHistory, setTraceCursorHistory] = useState([0])
  const projectsQuery = useWorkspaceProjects(workspaceId)
  const workflowsQuery = useProjectWorkflows(workspaceId, projectId)
  const summaryQuery = useProjectRuntimeSummary(workspaceId, projectId)
  const logsQuery = useProjectRuntimeLogs(workspaceId, projectId, {
    ...(status === 'all' ? {} : { status }),
    ...(deferredSearch ? { search: deferredSearch } : {}),
    page,
    limit: PAGE_SIZE,
  })
  const traceQuery = useProjectRuntimeTrace(
    workspaceId,
    projectId,
    selectedLog?.workflow_id ?? null,
    selectedLog?.run_id ?? null,
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
  const meta = logsQuery.data?.meta
  const pages = Math.max(1, meta?.pages ?? 1)
  const overviewHref = workspaceId ? `/studio/projects/${projectId}?workspace=${workspaceId}` : '/studio'
  const apiHref = workspaceId
    ? `/studio/projects/${projectId}/api?workspace=${workspaceId}${workflowId ? `&workflow=${workflowId}` : ''}`
    : null
  const loading = projectsQuery.isLoading || workflowsQuery.isLoading
  const error = projectsQuery.error || workflowsQuery.error
  const traceEvents = traceQuery.data?.trace.events ?? []
  const traceCursor = traceCursorHistory.at(-1) ?? 0
  const traceNextCursor = traceQuery.data?.trace.nextAfterSequence ?? traceCursor
  const traceTotalEvents = traceQuery.data?.trace.projection.eventCount ?? selectedLog?.event_count ?? 0
  const traceHasNextPage = traceNextCursor > traceCursor && traceNextCursor < traceTotalEvents

  useEffect(() => {
    setPage(1)
  }, [deferredSearch, status])

  return (
    <PageContainer
      eyebrow="Project observability"
      title={project ? `${project.name} · 日志监测` : '项目日志监测'}
      description="查看项目内每次 Workflow Run 的发布版本、状态、耗时、输入与节点事件，并沿 trace_id 定位失败原因。"
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
            <Metric label="成功" value={summary?.successful_runs ?? 0} icon={CheckCircle2} tone="success" />
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
                <SelectTrigger><Filter className="size-4" /><SelectValue>{status === 'all' ? '全部状态' : status}</SelectValue></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="completed">Completed</SelectItem>
                  <SelectItem value="partial_success">Partial success</SelectItem>
                  <SelectItem value="running">Running</SelectItem>
                  <SelectItem value="waiting">Waiting</SelectItem>
                  <SelectItem value="blocked">Blocked</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
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
                      <TableCell className="text-right"><Button type="button" size="sm" variant="ghost" onClick={() => { setTraceCursorHistory([0]); setSelectedLog(log) }}><Eye className="size-4" />Trace</Button></TableCell>
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

      <Sheet open={Boolean(selectedLog)} onOpenChange={(open) => {
        if (!open) {
          setSelectedLog(null)
          setTraceCursorHistory([0])
        }
      }}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
          {selectedLog ? (
            <>
              <SheetHeader className="border-b">
                <SheetTitle className="pr-10">{selectedLog.workflow_name} · Run Trace</SheetTitle>
                <SheetDescription className="break-all font-mono">{selectedLog.run_id} · {selectedLog.trace_id}</SheetDescription>
              </SheetHeader>
              <div className="space-y-5 px-4 pb-6">
                <div className="grid gap-2 sm:grid-cols-4">
                  <TraceMetric label="状态"><StatusBadge status={traceQuery.data?.trace.projection.status ?? selectedLog.status} /></TraceMetric>
                  <TraceMetric label="版本" value={traceQuery.data?.workflow_version ? `Published v${traceQuery.data.workflow_version}` : 'Draft'} />
                  <TraceMetric label="用户" value={traceQuery.data?.user ?? 'operator'} />
                  <TraceMetric label="事件" value={String(traceQuery.data?.trace.projection.eventCount ?? selectedLog.event_count)} />
                </div>

                <RunAnalysisSnapshotPanel
                  workspaceId={workspaceId}
                  projectId={projectId}
                  workflowId={selectedLog.workflow_id}
                  runId={selectedLog.run_id}
                  runStatus={selectedLog.status}
                  runStartAt={selectedLog.started_at}
                  runEndAt={selectedLog.updated_at}
                />

                <div>
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium"><Braces className="size-4" />运行输入</div>
                  <pre className="max-h-56 overflow-auto rounded-lg border bg-muted/25 p-4 font-mono text-xs leading-5">{JSON.stringify(traceQuery.data?.inputs ?? {}, null, 2)}</pre>
                </div>

                <div>
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium"><Workflow className="size-4" />事件时间线</div>
                  {traceQuery.isLoading ? <LoadingState rows={5} /> : traceQuery.isError ? (
                    <ErrorState message={traceQuery.error?.message ?? 'Trace 加载失败'} hint="确认该运行仍属于当前项目和工作流。" />
                  ) : traceEvents.length === 0 ? (
                    <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">这个运行还没有持久化事件。</div>
                  ) : (
                    <div className="space-y-2">
                      {traceEvents.map((event) => (
                        <div key={event.id} className="grid gap-3 rounded-lg border p-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_8rem]">
                          <div className="font-mono text-xs text-muted-foreground">#{event.sequence}</div>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2"><StatusBadge status={event.eventType} /><span className="truncate font-mono text-xs">{event.nodeId}</span></div>
                            {event.message ? <p className="mt-2 text-xs leading-5 text-muted-foreground">{event.message}</p> : null}
                            {Object.keys(event.details ?? {}).length ? <pre className="mt-2 max-h-32 overflow-auto rounded border bg-muted/25 p-2 font-mono text-[10px] leading-4">{JSON.stringify(event.details, null, 2)}</pre> : null}
                          </div>
                          <div className="text-right text-[10px] text-muted-foreground">{formatDateTime(event.createdAt)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  {!traceQuery.isLoading && !traceQuery.isError && traceQuery.data ? (
                    <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                      <span>{traceEvents.length ? `事件 #${traceEvents[0]?.sequence}–#${traceEvents.at(-1)?.sequence}` : '当前批次无事件'} · 共 {traceTotalEvents} 条</span>
                      <div className="flex gap-2">
                        <Button type="button" size="sm" variant="outline" disabled={traceCursorHistory.length === 1} onClick={() => setTraceCursorHistory((history) => history.slice(0, -1))}>上一批</Button>
                        <Button type="button" size="sm" variant="outline" disabled={!traceHasNextPage} onClick={() => setTraceCursorHistory((history) => [...history, traceNextCursor])}>下一批</Button>
                      </div>
                    </div>
                  ) : null}
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
  const tone = status === 'completed' || status === 'partial_success'
    ? 'border-success/30 bg-success/10 text-success'
    : status === 'failed'
      ? 'border-destructive/30 bg-destructive/10 text-destructive'
      : status === 'blocked'
        ? 'border-warning/30 bg-warning/10 text-warning'
        : 'border-primary/30 bg-primary/10 text-primary'
  return <Badge variant="outline" className={cn('font-mono text-[10px]', tone)}>{status}</Badge>
}

function formatDuration(durationMs: number) {
  if (durationMs < 1_000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(1)} s`
  return `${Math.floor(durationMs / 60_000)}m ${Math.round((durationMs % 60_000) / 1_000)}s`
}
