'use client'

import {
  ArrowUpRight,
  Copy,
  Download,
  FileText,
  LoaderCircle,
  MessageCircle,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'

import { InboxConversationThread, InboxConversationUnavailable } from '@/components/inbox/inbox-conversation-thread'
import { ReportContentView } from '@/components/artifacts/report-content-view'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { getProjectArtifact, listProjectArtifacts, type ProjectArtifactDetail, type ProjectArtifactSummary } from '@/lib/api/project-artifacts'
import { serializeCsvCell } from '@/lib/csv'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

type ProjectArtifactsPanelProps = {
  workspaceId: string | null
  projectId: string
  workflowId?: string | null
  runId?: string | null
}

function shortId(value: string | null | undefined) {
  if (!value) return '—'
  return value.length > 18 ? `${value.slice(0, 12)}…` : value
}

function artifactKindLabel(kind: string) {
  if (kind === 'evidence_batch') return '采集结果'
  if (kind === 'report') return '分析报告'
  return '项目产物'
}

function downloadText(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function exportArtifacts(artifacts: ProjectArtifactSummary[], format: 'csv' | 'json') {
  const safeRows = artifacts.map((artifact) => ({
    id: artifact.id,
    artifact_id: artifact.artifact_id,
    title: artifact.title,
    kind: artifact.kind,
    media_type: artifact.media_type,
    content_hash: artifact.content_hash,
    workspace_id: artifact.workspace_id,
    project_id: artifact.project_id,
    workflow_id: artifact.workflow_id,
    run_id: artifact.run_id,
    session_id: artifact.session_id ?? '',
    conversation_id: artifact.conversation_id ?? '',
    source: artifact.source ?? '',
    simulated: String(artifact.simulated),
    created_at: artifact.created_at,
    updated_at: artifact.updated_at,
  }))
  const filename = 'project-artifacts'
  if (format === 'json') {
    downloadText(`${filename}.json`, JSON.stringify(safeRows, null, 2), 'application/json;charset=utf-8')
    return
  }
  const headers = Object.keys(safeRows[0] ?? {})
  const csv = [headers, ...safeRows.map((row) => headers.map((header) => row[header as keyof typeof row]))]
    .map((row) => row.map((value) => serializeCsvCell(value)).join(','))
    .join('\r\n')
  downloadText(`${filename}.csv`, `\uFEFF${csv}`, 'text/csv;charset=utf-8')
}

function sourceHref(artifact: ProjectArtifactSummary) {
  const params = new URLSearchParams({ workspace: artifact.workspace_id })
  if (artifact.workflow_id) params.set('workflow', artifact.workflow_id)
  if (artifact.run_id) params.set('run', artifact.run_id)
  return `/studio/projects/${encodeURIComponent(artifact.project_id)}/data?${params.toString()}`
}

function reportPreview(detail: ProjectArtifactDetail) {
  const mediaType = detail.media_type.toLowerCase().split(';', 1)[0]
  const expectsText = mediaType.startsWith('text/') || mediaType.includes('html') || mediaType.includes('markdown')
  if (!expectsText) return { content: detail.content, mediaType: detail.media_type }
  const bodyKeys = ['content', 'body', 'markdown', 'html', 'text'] as const
  const body = bodyKeys
    .map((key) => detail.content[key])
    .find((value): value is string => typeof value === 'string')
  return body
    ? { content: body, mediaType: detail.media_type }
    : { content: detail.content, mediaType: 'application/json' }
}

export function ProjectArtifactsPanel({
  workspaceId,
  projectId,
  workflowId,
  runId,
}: ProjectArtifactsPanelProps) {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const selectedId = searchParams.get('artifact')
  const query = useQuery({
    queryKey: ['project-artifacts', workspaceId, projectId, workflowId ?? null, runId ?? null],
    queryFn: () => listProjectArtifacts(workspaceId!, projectId, { workflowId, runId, limit: 100 }),
    enabled: Boolean(workspaceId),
  })
  const artifacts = query.data ?? []
  const selected = artifacts.find((artifact) => artifact.id === selectedId) ?? null
  const detail = useQuery({
    queryKey: ['project-artifact', workspaceId, projectId, selectedId, workflowId ?? null, runId ?? null],
    queryFn: () => getProjectArtifact(workspaceId!, projectId, selectedId!, { workflowId, runId }),
    enabled: Boolean(workspaceId && selected),
  })
  const current = detail.data ?? null
  const preview = current ? reportPreview(current) : null
  const artifactListSettled = query.isSuccess && !query.isFetching

  useEffect(() => {
    if (artifactListSettled && selectedId && !selected) {
      const params = new URLSearchParams(searchParams.toString())
      params.delete('artifact')
      router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ''}`, { scroll: false })
    }
  }, [artifactListSettled, pathname, router, searchParams, selected, selectedId])

  function selectArtifact(id: string | null) {
    const params = new URLSearchParams(searchParams.toString())
    if (id) params.set('artifact', id)
    else params.delete('artifact')
    router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ''}`, { scroll: false })
  }

  function openConversation(artifact: ProjectArtifactSummary) {
    if (!artifact.conversation_id) return
    const params = new URLSearchParams({
      workspace: artifact.workspace_id,
      project: artifact.project_id,
      agent: '1',
      conversation: artifact.conversation_id,
    })
    if (artifact.workflow_id) params.set('workflow', artifact.workflow_id)
    if (artifact.run_id) params.set('run', artifact.run_id)
    router.push(`/studio/projects/${encodeURIComponent(artifact.project_id)}?${params.toString()}`)
  }

  return (
    <Card className="overflow-hidden" aria-labelledby="project-artifacts-title" data-testid="project-artifacts-panel">
      <CardHeader className="border-b bg-muted/15 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><FileText className="size-3.5 text-primary" aria-hidden />持久化产物</div>
            <CardTitle id="project-artifacts-title" className="mt-1 text-base">项目产物</CardTitle>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">内容来自当前项目与运行的授权持久化输出；来源按服务端投影展示。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => void query.refetch()} disabled={!workspaceId || query.isFetching}>
              <RefreshCw className={cn('size-3.5', query.isFetching && 'animate-spin')} />刷新
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => exportArtifacts(artifacts, 'csv')} disabled={!artifacts.length} title="导出当前已加载的项目产物">
              <Download className="size-3.5" />导出当前 CSV
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={() => exportArtifacts(artifacts, 'json')} disabled={!artifacts.length} title="导出当前已加载的项目产物">
              <Copy className="size-3.5" />导出当前 JSON
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="rounded-full border bg-background px-2 py-1">{workflowId ? `工作流 ${shortId(workflowId)}` : '全部工作流'}</span>
          <span className="rounded-full border bg-background px-2 py-1">{runId ? `运行 ${shortId(runId)}` : '全部运行'}</span>
          <span>{artifacts.length} 个已加载结果 · 导出当前显示{artifacts.length === 100 ? '（列表上限 100）' : ''}</span>
        </div>
      </CardHeader>

      <CardContent className="p-4">
        {!workspaceId ? <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">选择 Workspace 后才能读取项目产物。</p> : null}
        {workspaceId && query.isLoading ? <p role="status" className="flex items-center gap-2 rounded-md border border-dashed p-4 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在读取项目产物…</p> : null}
        {workspaceId && query.isError ? <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"><p>项目产物暂时无法读取。</p><p className="mt-1 text-xs">{query.error instanceof Error ? query.error.message : '请稍后重试。'}</p></div> : null}
        {workspaceId && !query.isLoading && !query.isError && artifacts.length === 0 ? <div className="rounded-md border border-dashed p-5 text-sm text-muted-foreground"><p>当前运行范围还没有可查看的持久化产物。</p><p className="mt-1 text-xs">完成工作流或产生 evidence batch 后，结果会出现在这里。</p></div> : null}
        {artifacts.length ? (
          <div className="grid gap-2">
            {artifacts.map((artifact) => (
              <button
                key={artifact.id}
                type="button"
                onClick={() => selectArtifact(artifact.id)}
                className="w-full rounded-lg border bg-background p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                data-testid={`project-artifact-${artifact.id}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{artifact.title}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span>{artifactKindLabel(artifact.kind)}</span>
                      <span>{artifact.media_type}</span>
                      <span className="font-mono">run {shortId(artifact.run_id)}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatDateTime(artifact.updated_at)}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span className="font-mono">{shortId(artifact.content_hash)}</span>
                  {artifact.source ? <span>来源：{artifact.source}</span> : null}
                  {artifact.simulated ? <span className="text-amber-600">模拟</span> : <span className="text-emerald-700">已持久化</span>}
                </div>
              </button>
            ))}
          </div>
        ) : null}
      </CardContent>

      <Sheet open={Boolean(selectedId && selected)} onOpenChange={(open) => !open && selectArtifact(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl data-[side=right]:w-full data-[side=right]:sm:max-w-2xl" data-testid="project-artifact-detail">
          <SheetHeader>
            <SheetTitle>{selected?.title ?? '项目产物'}</SheetTitle>
            <SheetDescription>{selected ? `${artifactKindLabel(selected.kind)} · ${selected.media_type} · run ${selected.run_id}` : '读取持久化产物详情'}</SheetDescription>
          </SheetHeader>
          <div className="space-y-4 px-4 pb-6">
            {detail.isLoading ? <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在读取正文…</p> : null}
            {detail.isError ? <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">正文读取失败：{detail.error instanceof Error ? detail.error.message : '请稍后重试。'}</p> : null}
            {current ? <ReportContentView content={preview?.content} mediaType={preview?.mediaType} state="ready" /> : null}
            {current ? (
              <section className="rounded-lg border bg-muted/15 p-3" aria-labelledby="artifact-source-heading">
                <h3 id="artifact-source-heading" className="flex items-center gap-2 text-xs font-medium"><ShieldAlert className="size-3.5 text-primary" aria-hidden />来源与范围</h3>
                <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                  <div><dt className="text-muted-foreground">项目</dt><dd className="mt-0.5 break-all font-mono">{current.project_id}</dd></div>
                  <div><dt className="text-muted-foreground">工作流</dt><dd className="mt-0.5 break-all font-mono">{current.workflow_id}</dd></div>
                  <div><dt className="text-muted-foreground">运行</dt><dd className="mt-0.5 break-all font-mono">{current.run_id}</dd></div>
                  <div><dt className="text-muted-foreground">内容哈希</dt><dd className="mt-0.5 break-all font-mono">{current.content_hash}</dd></div>
                </dl>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Link href={sourceHref(current)} className={buttonVariants({ variant: 'outline', size: 'sm' })}><ArrowUpRight className="size-3.5" />查看此运行数据</Link>
                  {current.conversation_id ? <Button type="button" size="sm" onClick={() => openConversation(current)}><MessageCircle className="size-3.5" />打开原会话</Button> : null}
                </div>
                {current.conversation_id ? (
                  <div className="mt-3"><InboxConversationThread conversationId={current.conversation_id} context={{ project_id: current.project_id, workflow_id: current.workflow_id, run_id: current.run_id, surface: 'project_artifact' }} /></div>
                ) : <div className="mt-3"><InboxConversationUnavailable reason="missing" /></div>}
              </section>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </Card>
  )
}
