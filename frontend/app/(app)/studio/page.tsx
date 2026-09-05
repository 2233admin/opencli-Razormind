'use client'

import { Building2, ChevronDown, FileText, FileUp, FolderKanban, MessageCircle, Plus, Search, Sparkles, Trash2, Workflow } from 'lucide-react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { PageContainer } from '@/components/shell/page-container'
import { ProjectCreateForm } from '@/components/studio/project-create-form'
import { ErrorState } from '@/components/shell/data-states'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useBootstrapWorkspaceProject, useCreateProjectWorkflow, useDeleteWorkspaceProject, useGovernedWorkspaces, useMyWorkspaces, useWorkspaceProjects } from '@/lib/api/hooks'
import { formatRelative } from '@/lib/format'
import { PROJECT_APP_CATEGORY_LABELS, projectAppCategoryLabel, projectAppTypeForDifyMode, projectAppTypeLabel, projectMatchesAppType, type ProjectAppTypeFilter } from '@/lib/studio/app-types'
import type { ProjectSummary } from '@/lib/api/types'
import { translateWorkflowDslManaged, type WorkflowImportResult } from '@/lib/workflow/codec'
import { businessProjectName } from '@/lib/workflow/business-node-experience'
import { studioSlug, type StudioTemplateId } from '@/lib/workflow/studio-templates'
import { cn } from '@/lib/utils'
const CAPABILITY_CONTEXT_KEYS = ['provider', 'capability', 'adapter', 'command'] as const

const PROJECT_TYPE_FILTERS = [
  { value: 'all', label: '全部', icon: FolderKanban },
  { value: 'conversation', label: PROJECT_APP_CATEGORY_LABELS.conversation, icon: MessageCircle },
  { value: 'orchestration', label: PROJECT_APP_CATEGORY_LABELS.orchestration, icon: Workflow },
  { value: 'generation', label: PROJECT_APP_CATEGORY_LABELS.generation, icon: FileText },
] as const satisfies ReadonlyArray<{ value: ProjectAppTypeFilter; label: string; icon: typeof FolderKanban }>

export default function StudioPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const workspaces = useMyWorkspaces()
  const governedWorkspaces = useGovernedWorkspaces()
  const [workspaceId, setWorkspaceId] = useState<string | null>(null)
  const searchQuery = searchParams.toString()
  const capabilityContext = useMemo(() => {
    const query = new URLSearchParams(searchQuery)
    const valid = (key: string) => {
      const value = query.get(key)?.trim()
      return value && value.length <= 128 && /^[A-Za-z0-9._:/-]+$/.test(value) ? value : undefined
    }
    const context = { provider: valid('provider'), capability: valid('capability'), adapter: valid('adapter'), command: valid('command') }
    return Object.values(context).some(Boolean) ? context : null
  }, [searchQuery])
  const capabilityQuery = capabilityContext
    ? new URLSearchParams(Object.entries(capabilityContext).filter((entry): entry is [string, string] => Boolean(entry[1]))).toString()
    : ''
  const capabilityClearParams = new URLSearchParams(searchQuery)
  CAPABILITY_CONTEXT_KEYS.forEach((key) => capabilityClearParams.delete(key))
  const capabilityClearHref = `/studio${capabilityClearParams.toString() ? `?${capabilityClearParams.toString()}` : ''}`
  const [search, setSearch] = useState('')
  const [type, setType] = useState<ProjectAppTypeFilter>('all')
  const [sort, setSort] = useState('updated-desc')
  const [createTemplate, setCreateTemplate] = useState<StudioTemplateId | null>(null)
  const [pendingImport, setPendingImport] = useState<Extract<WorkflowImportResult, { ok: true }> | null>(null)
  const [importProjectId, setImportProjectId] = useState('')
  const [importProjectName, setImportProjectName] = useState('')
  const [contextMenu, setContextMenu] = useState<{ project: ProjectSummary; x: number; y: number } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null)
  const importInputRef = useRef<HTMLInputElement>(null)
  const createIntentHandled = useRef(false)
  const projects = useWorkspaceProjects(workspaceId)
  const bootstrapProject = useBootstrapWorkspaceProject()
  const createWorkflow = useCreateProjectWorkflow()
  const deleteProject = useDeleteWorkspaceProject()

  useEffect(() => {
    if (!workspaces.data?.length) return
    const requestedWorkspaceId = new URLSearchParams(searchQuery).get('workspace')
    const requestedWorkspace = workspaces.data.find((workspace) => workspace.id === requestedWorkspaceId)
    if (requestedWorkspace) {
      setWorkspaceId(requestedWorkspace.id)
      return
    }
    if (workspaceId && !requestedWorkspaceId) return
    const governedWorkspace = governedWorkspaces.data?.find((workspace) => workspace.id === requestedWorkspaceId)
    if (!requestedWorkspace && requestedWorkspaceId && governedWorkspaces.isLoading) return
    const mappedWorkspace = governedWorkspace
      ? workspaces.data.find((workspace) =>
        workspace.slug === governedWorkspace.slug
        || workspace.slug === `governed-${governedWorkspace.slug}`,
      )
      : undefined
    const preferredWorkspaceId = window.localStorage.getItem('opencli:studio-workspace')
    const preferredWorkspace = workspaces.data.find((workspace) => workspace.id === preferredWorkspaceId)
    setWorkspaceId(
      (governedWorkspace ? governedWorkspace.id : undefined)
        ?? mappedWorkspace?.id
        ?? preferredWorkspace?.id
        ?? (workspaces.data.length === 1 ? workspaces.data[0].id : null),
    )
  }, [searchQuery, workspaceId, workspaces.data, governedWorkspaces.data, governedWorkspaces.isLoading])

  useEffect(() => {
    if (workspaceId && workspaces.data?.some((workspace) => workspace.id === workspaceId)) {
      window.localStorage.setItem('opencli:studio-workspace', workspaceId)
      const query = new URLSearchParams(searchQuery)
      if (!query.has('workspace')) {
        query.set('workspace', workspaceId)
        router.replace(`/studio?${query}`, { scroll: false })
      }
    }
  }, [router, searchQuery, workspaceId, workspaces.data])

  useEffect(() => {
    if (!workspaceId || createIntentHandled.current) return
    if (new URLSearchParams(window.location.search).get('create') === 'workflow') {
      createIntentHandled.current = true
      setCreateTemplate('opencli-live-pipeline')
      const url = new URL(window.location.href)
      url.searchParams.delete('create')
      window.history.replaceState(window.history.state, '', url)
    }
  }, [workspaceId])

  const visibleProjects = useMemo(() => {
    const query = search.trim().toLowerCase()
    const result = (projects.data ?? []).filter((project) => {
      const haystack = `${businessProjectName(project.name)} ${project.description ?? ''} ${project.slug}`.toLowerCase()
      return (!query || haystack.includes(query)) && projectMatchesAppType(project, type)
    })
    return result.sort((left, right) => {
      if (sort === 'name') return businessProjectName(left.name).localeCompare(businessProjectName(right.name), 'zh-CN')
      const field = sort === 'created-asc' ? 'created_at' : 'updated_at'
      const direction = sort === 'created-asc' ? 1 : -1
      return left[field].localeCompare(right[field]) * direction
    })
  }, [projects.data, search, sort, type])
  const selectedWorkspace = workspaces.data?.find((workspace) => workspace.id === workspaceId)


  async function importDsl(file: File) {
    if (!workspaceId) return
    const translated = await translateWorkflowDslManaged(await file.text())
    if (!translated.ok) {
      toast.error(translated.error)
      return
    }
    if (translated.format === 'dify' && translated.report?.source === 'dify' && translated.report.runtimeSource === 'browser-fallback') {
      toast.warning('Graphon 检查不可用：已生成不可执行的结构预览')
    }
    const name = translated.project.name || file.name.replace(/\.[^.]+$/, '')
    setPendingImport(translated)
    setImportProjectId(projects.data?.[0]?.id ?? '__new__')
    setImportProjectName(name)
    if (importInputRef.current) importInputRef.current.value = ''
  }

  async function submitImport() {
    if (!workspaceId || !pendingImport || !importProjectId) return
    try {
      const name = pendingImport.project.name
      let projectId = importProjectId
      let workflowId: string
      if (importProjectId === '__new__') {
        const result = await bootstrapProject.mutateAsync({
            workspaceId,
            data: {
              project: {
                name: importProjectName.trim(),
                slug: `${studioSlug(importProjectName)}-${Date.now().toString(36)}`,
                description: `${pendingImport.format} DSL 导入`,
                app_type: projectAppTypeForDifyMode(pendingImport.format === 'dify' && pendingImport.report?.source === 'dify' ? pendingImport.report.appMode : undefined),
              },
              workflow: { name, description: `${pendingImport.format} 兼容工作流`, graph: pendingImport.project },
            },
          })
        projectId = result.project.id
        workflowId = result.primary_workflow.id
      } else {
        const workflow = await createWorkflow.mutateAsync({
          projectId,
          workspaceId,
          data: { name, description: `${pendingImport.format} 兼容工作流`, graph: pendingImport.project },
        })
        workflowId = workflow.id
      }
      toast.success(`已创建 ${pendingImport.format} WorkflowDraft`)
      setPendingImport(null)
      router.push(`/studio/workflow?workspace=${workspaceId}&project=${projectId}&workflow=${workflowId}${capabilityQuery ? `&${capabilityQuery}` : ''}`)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : 'DSL 导入失败')
    }
  }

  async function confirmDeleteProject() {
    if (!workspaceId || !deleteTarget) return
    try {
      await deleteProject.mutateAsync({ workspaceId, projectId: deleteTarget.id })
      toast.success(`已删除项目“${businessProjectName(deleteTarget.name)}”`)
      setDeleteTarget(null)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : '删除失败')
    }
  }

  return (
    <PageContainer
      title="项目"
      eyebrow="Workspace · Projects"
      description="从真实工作区进入项目，在正式节点系统中编排、验证和发布工作流。"
      className="max-w-none"
      actions={
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button className="min-h-11" disabled={!workspaceId} />}><Plus className="size-4" />创建<ChevronDown className="size-3.5" /></DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem onClick={() => setCreateTemplate('blank')}><Plus className="size-4" />创建空白工作流</DropdownMenuItem>
            <DropdownMenuItem onClick={() => router.push(`/studio/new${workspaceId ? `?workspace=${workspaceId}` : ''}`)}><Sparkles className="size-4" />描述需求创建</DropdownMenuItem>
            <DropdownMenuItem onClick={() => router.push(`/plugins?type=template&workspace=${workspaceId}${capabilityQuery ? `&${capabilityQuery}` : ''}`)}>从模板创建</DropdownMenuItem>
            <DropdownMenuItem onClick={() => importInputRef.current?.click()}><FileUp className="size-4" />导入 DSL</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
    >
      <input ref={importInputRef} type="file" accept=".json,.yml,.yaml,application/json,text/yaml" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importDsl(file) }} />
      <div className="space-y-3 border-b pb-4" aria-label="项目浏览工具栏">
        {!workspaceId && (workspaces.data?.length ?? 0) > 1 ? (
          <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs" role="status">请选择已授权 Workspace 后再创建或查看项目。系统不会自动选择其他 Workspace。</div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {(workspaces.data?.length ?? 0) > 1 ? (
            <Select value={workspaceId ?? ''} onValueChange={(value) => {
              if (!value) return
              const query = new URLSearchParams(searchQuery)
              query.set('workspace', value)
              router.replace(`/studio?${query}`, { scroll: false })
            }}>
              <SelectTrigger className="min-h-11 min-w-48 rounded-lg border-0 bg-muted/60 shadow-none" aria-label="切换工作区">
                <Building2 className="size-3.5 text-muted-foreground" aria-hidden />
                <SelectValue>{selectedWorkspace?.name ?? '选择工作区'}</SelectValue>
              </SelectTrigger>
              <SelectContent>{(workspaces.data ?? []).map((workspace) => <SelectItem key={workspace.id} value={workspace.id}>{workspace.name}</SelectItem>)}</SelectContent>
            </Select>
          ) : selectedWorkspace ? (
            <div className="flex min-h-11 items-center gap-2 px-1 text-xs" aria-label="当前工作区">
              <Building2 className="size-3.5 text-muted-foreground" aria-hidden />
              <span className="font-medium">{selectedWorkspace.name}</span>
            </div>
          ) : null}
          <div className="relative ml-auto min-w-52 flex-1 sm:max-w-80">
            <Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" aria-hidden />
            <Input value={search} onChange={(event) => setSearch(event.target.value)} className="min-h-11 rounded-lg pl-9" placeholder="搜索名称、描述或标识" />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" aria-label="项目分类筛选">
          <span className="px-1 text-2xs text-muted-foreground">项目分类</span>
          {PROJECT_TYPE_FILTERS.map(({ value, label, icon: Icon }) => (
            <Button key={value} type="button" size="sm" variant={type === value ? 'secondary' : 'ghost'} aria-pressed={type === value} className="min-h-11 rounded-lg px-3 text-xs" onClick={() => setType(value)}>
              <Icon className="size-3.5" aria-hidden />{label}
            </Button>
          ))}
          <Select value={sort} onValueChange={(value) => setSort(value ?? 'updated-desc')}>
            <SelectTrigger className="min-h-11 rounded-lg border-0 bg-transparent text-xs shadow-none sm:ml-auto"><SelectValue>{SORT_LABELS[sort]}</SelectValue></SelectTrigger>
            <SelectContent><SelectItem value="updated-desc">最近修改</SelectItem><SelectItem value="created-asc">最早创建</SelectItem><SelectItem value="name">名称</SelectItem></SelectContent>
          </Select>
        </div>
      </div>
      {capabilityContext ? (
        <section className="flex items-center justify-between gap-3 rounded-xl border border-primary/20 bg-primary/5 p-3 text-sm" aria-label="能力上下文">
          <div><div className="font-medium">能力上下文</div><div className="mt-1 text-xs text-muted-foreground">{[capabilityContext.provider, capabilityContext.capability, capabilityContext.adapter, capabilityContext.command].filter(Boolean).join(' · ')}。目录 readiness 不等于 run-scoped admission。</div></div>
          <Link href={capabilityClearHref} prefetch={false} className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }))} aria-label="移除能力上下文">移除</Link>
        </section>
      ) : null}
      {workspaces.isError || projects.isError ? (
        <div className="space-y-3">
          <ErrorState
            message={(workspaces.error ?? projects.error)?.message}
            hint="确认后端已启动并完成身份验证后重试。"
          />
          <Button variant="outline" onClick={() => void (workspaces.isError ? workspaces.refetch() : projects.refetch())}>重试</Button>
        </div>
      ) : projects.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-40 animate-pulse rounded-xl bg-muted" />)}</div>
      ) : visibleProjects.length ? (
        <section aria-label="项目列表">
          <div className="mb-3 text-xs font-medium">{visibleProjects.length} 个项目</div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {visibleProjects.map((project) => (
            <Link
              key={project.id}
              href={`/studio/projects/${project.id}?workspace=${workspaceId}`}
              onContextMenu={(event) => {
                event.preventDefault()
                setContextMenu({ project, x: event.clientX, y: event.clientY })
              }}
              className="group min-w-0 rounded-xl border border-border/80 bg-card/30 p-3.5 transition-[border-color,background-color,transform] hover:-translate-y-0.5 hover:border-foreground/25 hover:bg-card/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              <div className="flex items-start justify-between gap-3"><div className="grid size-9 place-items-center rounded-lg bg-muted text-muted-foreground transition-colors group-hover:text-foreground"><FolderKanban className="size-4" aria-hidden /></div><Badge variant={project.archived ? 'secondary' : 'outline'}>{project.archived ? '已归档' : project.slug}</Badge></div>
              <div className="mt-4 flex items-center gap-2"><span className="eyebrow-mono">{projectAppCategoryLabel(project.app_type)} · {projectAppTypeLabel(project.app_type)}</span><span className="truncate font-mono text-3xs text-muted-foreground">{project.slug}</span></div>
              <h2 className="mt-1 truncate text-sm font-semibold">{businessProjectName(project.name)}</h2>
              <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">{project.description || '数据节点项目'}</p>
              <div className="mt-3 flex items-center justify-between border-t border-border/70 pt-2.5 text-3xs text-muted-foreground"><span>创建者 我</span><span>{formatRelative(project.updated_at)}</span></div>
            </Link>
          ))}
          </div>
        </section>
      ) : (
        <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed bg-muted/10 px-4">
          <div className="w-full max-w-xl text-center">
            <div className="mx-auto grid size-11 place-items-center rounded-xl border bg-background"><FolderKanban className="size-5 text-muted-foreground" /></div>
            <h2 className="mt-4 text-sm font-medium">创建你的第一个项目</h2>
            <p className="mt-1 text-xs text-muted-foreground">创建空白工作流、从模板开始，或者导入现有工作流。Agent 始终在顶部可用。</p>
            <div className="mt-5 grid gap-2 text-left">
              <CreateChoice title="创建空白工作流" description="从需求节点开始，自由添加业务能力。" onClick={workspaceId ? () => setCreateTemplate('blank') : undefined} icon={Plus} />
              <CreateChoice title="描述需求创建" description="先说明持续目标，再保存同一份 Project Draft。" href={workspaceId ? `/studio/new?workspace=${workspaceId}` : '/studio/new'} icon={Sparkles} />
              <CreateChoice title="从应用模板创建" description="选择预设的数据链路，最快体验 OpenCLI。" href={workspaceId ? `/plugins?type=template&workspace=${workspaceId}` : '/plugins?type=template'} icon={Sparkles} />
              <div className="my-0.5 flex items-center gap-3 text-3xs text-muted-foreground before:h-px before:flex-1 before:bg-border after:h-px after:flex-1 after:bg-border">或</div>
              <CreateChoice title="导入 DSL 文件" description="兼容迁移 Dify、n8n 和 OpenCLI 工作流。" onClick={workspaceId ? () => importInputRef.current?.click() : undefined} icon={FileUp} />
            </div>
          </div>
        </div>
      )}

      {contextMenu ? (
        <>
          <button type="button" aria-label="关闭项目右键菜单" className="fixed inset-0 z-40 cursor-default" onClick={() => setContextMenu(null)} onContextMenu={(event) => { event.preventDefault(); setContextMenu(null) }} />
          <div role="menu" aria-label={`${businessProjectName(contextMenu.project.name)} 项目操作`} className="fixed z-50 min-w-44 rounded-lg border bg-popover p-1 shadow-xl" style={{ left: Math.min(contextMenu.x, window.innerWidth - 190), top: Math.min(contextMenu.y, window.innerHeight - 70) }}>
            <button type="button" role="menuitem" className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-xs text-destructive hover:bg-destructive/10" onClick={() => { setDeleteTarget(contextMenu.project); setContextMenu(null) }}>
              <Trash2 className="size-3.5" aria-hidden />删除项目
            </button>
          </div>
        </>
      ) : null}

      <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && !deleteProject.isPending && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>删除项目“{deleteTarget ? businessProjectName(deleteTarget.name) : ''}”？</DialogTitle><DialogDescription>项目下的工作流、草稿、版本和运行记录都会一并删除，此操作无法撤销。</DialogDescription></DialogHeader>
          <DialogFooter><Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleteProject.isPending}>取消</Button><Button variant="destructive" onClick={() => void confirmDeleteProject()} disabled={deleteProject.isPending}>{deleteProject.isPending ? '正在删除…' : '确认删除'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={createTemplate !== null} onOpenChange={(open) => !open && setCreateTemplate(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>创建项目</DialogTitle><DialogDescription>项目和第一条工作流会同时保存到当前工作区。</DialogDescription></DialogHeader>
          {workspaceId && createTemplate ? <ProjectCreateForm workspaceId={workspaceId} template={createTemplate} initialName={createTemplate === 'opencli-live-pipeline' ? 'OpenCLI 实时采集清洗发送' : '未命名项目'} onCancel={() => setCreateTemplate(null)} onCreated={(result) => { setCreateTemplate(null); toast.success('项目与工作流已创建'); router.push(`/studio/workflow?workspace=${workspaceId}&project=${result.project.id}&workflow=${result.primary_workflow.id}${capabilityQuery ? `&${capabilityQuery}` : ''}`) }} /> : null}
        </DialogContent>
      </Dialog>
      <Dialog open={pendingImport !== null} onOpenChange={(open) => !open && setPendingImport(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>导入为 WorkflowDraft</DialogTitle>
            <DialogDescription>选择现有 Project，或明确新建一个 Project 后保存并打开画布。</DialogDescription>
          </DialogHeader>
          {pendingImport ? <ImportCompatibilitySummary imported={pendingImport} /> : null}
          <label className="space-y-2 text-sm">
            <span>目标 Project</span>
            <Select value={importProjectId} onValueChange={(value) => setImportProjectId(value ?? '')}>
              <SelectTrigger className="w-full">
                <SelectValue>
                  {(value: string | null) => {
                    if (!value) return '选择 Project'
                    if (value === '__new__') return '＋ 新建 Project'
                    const project = projects.data?.find((item) => item.id === value)
                    return project ? businessProjectName(project.name) : value
                  }}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(projects.data ?? []).map((project) => <SelectItem key={project.id} value={project.id}>{businessProjectName(project.name)}</SelectItem>)}
                <SelectItem value="__new__">＋ 新建 Project</SelectItem>
              </SelectContent>
            </Select>
          </label>
          {importProjectId === '__new__' ? (
            <label className="space-y-2 text-sm"><span>新 Project 名称</span><Input value={importProjectName} onChange={(event) => setImportProjectName(event.target.value)} /></label>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingImport(null)}>取消</Button>
            <Button onClick={submitImport} disabled={!importProjectId || (importProjectId === '__new__' && !importProjectName.trim()) || bootstrapProject.isPending || createWorkflow.isPending}>创建 Draft 并打开</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageContainer>
  )
}

function ImportCompatibilitySummary({ imported }: { imported: Extract<WorkflowImportResult, { ok: true }> }) {
  const report = imported.report
  const unsupported = report && ('unsupportedConnectionCount' in report ? report.unsupportedConnectionCount : report.unsupportedEdgeCount)
  const difyReport = report?.source === 'dify' ? report : undefined
  return (
    <div className="rounded-xl border bg-muted/30 p-3 text-xs">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium">兼容报告</span>
        <div className="flex items-center gap-2">
          {difyReport ? <Badge variant={difyReport.executable ? 'secondary' : 'outline'}>{difyReport.executable ? '可执行' : '仅预览'}</Badge> : null}
          <Badge variant="outline">{imported.format}</Badge>
        </div>
      </div>
      <p className="mt-2 text-muted-foreground">
        {report ? `${report.nodeCount} 个节点 · ${report.edgeCount} 条连线 · ${report.adapterCount} 个适配器` : 'Canonical WorkflowProject，无需翻译。'}
      </p>
      {difyReport ? (
        <p className="mt-2 text-muted-foreground">
          {difyReport.runtimeSource === 'backend'
            ? `Graphon ${difyReport.inspection?.engine.version ?? ''} 后端检查`
            : '浏览器降级预览（不参与执行）'}
        </p>
      ) : null}
      {unsupported ? <p className="mt-2 text-warning">有 {unsupported} 条连接无法映射，已保留其余可兼容内容。</p> : <p className="mt-2 text-success">未发现缺失连接。</p>}
      {difyReport?.blockers.map((blocker) => (
        <p key={`${blocker.code}-${blocker.nodeId ?? ''}`} className="mt-2 text-warning">
          {blocker.nodeId ? `${blocker.nodeId}：` : ''}{blocker.message}
        </p>
      ))}
      {difyReport?.backendError ? <p className="mt-2 text-destructive">后端检查失败：{difyReport.backendError}</p> : null}
    </div>
  )
}

const SORT_LABELS: Record<string, string> = { 'updated-desc': '最近修改', 'created-asc': '最早创建', name: '名称' }

function CreateChoice({ title, description, href, onClick, icon: Icon }: { title: string; description: string; href?: string; onClick?: () => void; icon: typeof Plus }) {
  const content = <><div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted"><Icon className="size-4" /></div><div><div className="text-sm font-medium">{title}</div><div className="mt-0.5 text-xs text-muted-foreground">{description}</div></div></>
  if (href) return <Link href={href} className="flex items-center gap-3 rounded-xl border bg-card p-3 transition-colors hover:bg-accent">{content}</Link>
  if (onClick) return <button type="button" onClick={onClick} className="flex items-center gap-3 rounded-xl border bg-card p-3 text-left transition-colors hover:bg-accent">{content}</button>
  return <div className="flex items-center gap-3 rounded-xl border bg-card p-3 opacity-50">{content}</div>
}
