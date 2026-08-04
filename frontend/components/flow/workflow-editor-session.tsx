'use client'

import { useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, Loader2, Plus, RefreshCw, Rocket, Workflow } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ErrorBoundary } from '@/components/error-boundary'
import { WorkflowLifecycleStrip } from '@/components/studio/workflow-lifecycle-strip'
import { loader, Matrix } from '@/components/unlumen-ui/matrix'
import { getProjectWorkflowDraft, publishProjectWorkflow, updateProjectWorkflowDraft, validateProjectWorkflowDraft } from '@/lib/api/endpoints'
import { useCreateProjectWorkflow, useWorkspaceProjects } from '@/lib/api/hooks'
import type { WorkflowAssetSummary } from '@/lib/api/types'
import { useFlowStore } from '@/lib/flow/store'
import { studioGraphForTemplate } from '@/lib/workflow/studio-templates'
import { useWorkflowCapabilities } from '@/lib/workflow/use-workflow-capabilities'
import { parseWorkflowProject, type WorkflowProject, type WorkflowProjectNode } from '@/lib/workflow/schema'
import { IMAGE_ASSET_CATALOG_ID } from '@/lib/workflow/node-catalog'

import { WorkflowEditor } from './workflow-editor'

function persistableProject(project: WorkflowProject): WorkflowProject {
  const persistableNode = (node: WorkflowProjectNode): WorkflowProjectNode => {
    const ui = { ...(node.ui ?? {}) }
    delete ui.runtimeCapability
    return {
      ...node,
      ...(node.ui ? { ui } : {}),
      ...(node.internals
        ? {
            internals: {
              ...node.internals,
              nodes: node.internals.nodes.map((item) => persistableNode(item as WorkflowProjectNode)),
            },
          }
        : {}),
    }
  }
  return { ...project, nodes: project.nodes.map(persistableNode) }
}

const projectFingerprint = (project: WorkflowProject) => JSON.stringify(persistableProject(project))

type WorkflowEditorSessionProps = {
  forceStandalone?: boolean
}

type WorkflowLoadState = 'loading' | 'ready' | 'empty' | 'error'

export function WorkflowEditorSession({ forceStandalone = false }: WorkflowEditorSessionProps = {}) {
  const params = useSearchParams()
  const router = useRouter()
  const queryClient = useQueryClient()
  const workspaceId = forceStandalone ? null : params.get('workspace')
  const projectId = forceStandalone ? null : params.get('project')
  const requestedWorkflowId = forceStandalone ? null : params.get('workflow')
  const workspaceProjects = useWorkspaceProjects(workspaceId)
  const project = workspaceProjects.data?.find((item) => item.id === projectId)
  const resolvedWorkflowId = requestedWorkflowId ?? project?.primary_workflow_id
  const primaryWorkflowPending = !requestedWorkflowId && workspaceProjects.isLoading
  const workflowProject = useFlowStore((state) => state.workflowProject)
  const importWorkflowProject = useFlowStore((state) => state.importWorkflowProject)
  const updateWorkflowNodeParams = useFlowStore((state) => state.updateWorkflowNodeParams)
  const [workflowId, setWorkflowId] = useState<string | null>(null)
  const [loadState, setLoadState] = useState<WorkflowLoadState>('loading')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [creationError, setCreationError] = useState<string | null>(null)
  const [documentState, setDocumentState] = useState<'loading' | 'saving' | 'saved' | 'error' | 'conflict'>('loading')
  const [savedRevision, setSavedRevision] = useState<number | null>(null)
  const [validationRunId, setValidationRunId] = useState<string | null>(null)
  const [releaseState, setReleaseState] = useState<'idle' | 'validating' | 'validated' | 'publishing' | 'published' | 'blocked'>('idle')
  const [releaseBlocker, setReleaseBlocker] = useState<string | null>(null)
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null)
  const [validationScope, setValidationScope] = useState<{ active: number; parked: number } | null>(null)
  const loaded = useRef(false)
  const revision = useRef<number | null>(null)
  const pendingGraph = useRef<typeof workflowProject | null>(null)
  const lastSavedFingerprint = useRef<string | null>(null)
  const saveQueuePromise = useRef<Promise<void> | null>(null)
  const saveBlocked = useRef(false)
  const createWorkflow = useCreateProjectWorkflow()
  const consumedImageReturn = useRef<string | null>(null)
  const { error: capabilityError, loading: capabilityLoading } = useWorkflowCapabilities(true)
  const standalone = forceStandalone
  const missingProjectContext = !standalone && (!workspaceId || !projectId)
  const projectHref = workspaceId && projectId ? `/studio/projects/${projectId}?workspace=${workspaceId}` : '/studio'

  const saveDraft = useCallback(
    (graph: typeof workflowProject) => {
      if (!workspaceId || !projectId || !workflowId || revision.current === null || saveBlocked.current) return Promise.reject(new Error('草稿尚未就绪'))
      const persistableGraph = persistableProject(graph)
      if (lastSavedFingerprint.current === projectFingerprint(persistableGraph)) return Promise.resolve()
      pendingGraph.current = persistableGraph
      setDocumentState('saving')
      if (!saveQueuePromise.current) {
        saveQueuePromise.current = (async () => {
          while (pendingGraph.current) {
            const nextGraph = pendingGraph.current
            pendingGraph.current = null
            try {
              const draft = await updateProjectWorkflowDraft(workspaceId, projectId, workflowId, nextGraph, revision.current!)
              revision.current = draft.revision
              setSavedRevision(draft.revision)
              lastSavedFingerprint.current = projectFingerprint(nextGraph)
            } catch (reason) {
              pendingGraph.current = null
              const status = (reason as Error & { status?: number }).status
              saveBlocked.current = status === 409
              setDocumentState(status === 409 ? 'conflict' : 'error')
              throw reason
            }
          }
          setDocumentState('saved')
        })().finally(() => {
          saveQueuePromise.current = null
        })
      }
      return saveQueuePromise.current
    },
    [projectId, workflowId, workspaceId],
  )

  useEffect(() => {
    if (!workspaceId || !projectId) return
    if (primaryWorkflowPending) return
    let active = true
    loaded.current = false
    revision.current = null
    setSavedRevision(null)
    pendingGraph.current = null
    lastSavedFingerprint.current = null
    saveBlocked.current = false
    setWorkflowId(null)
    setLoadState('loading')
    setLoadError(null)
    setCreationError(null)
    setDocumentState('loading')
    setLoadError(null)
    ;(async () => {
      try {
        if (!requestedWorkflowId && workspaceProjects.isError) {
          throw workspaceProjects.error ?? new Error('项目资料加载失败')
        }
        if (!requestedWorkflowId && !project) {
          throw new Error('当前工作区中找不到这个项目。')
        }
        if (!resolvedWorkflowId) {
          if (!active) return
          setLoadState('empty')
          return
        }
        const draft = await getProjectWorkflowDraft(workspaceId, projectId, resolvedWorkflowId)
        if (!active) return
        const graph = parseWorkflowProject(draft.graph)
        importWorkflowProject(graph)
        revision.current = draft.revision
        setSavedRevision(draft.revision)
        lastSavedFingerprint.current = projectFingerprint(graph)
        setWorkflowId(resolvedWorkflowId)
        loaded.current = true
        setDocumentState('saved')
        setLoadState('ready')
      } catch (reason) {
        if (!active) return
        const message = reason instanceof Error ? reason.message : '工作流加载失败'
        console.error('[WorkflowEditorSession] failed to load workflow draft', {
          workspaceId,
          projectId,
          workflowId: resolvedWorkflowId,
          reason,
        })
        setLoadError(message)
        setLoadState('error')
        setDocumentState('error')
        toast.error(message)
      }
    })()
    return () => {
      active = false
    }
  }, [importWorkflowProject, primaryWorkflowPending, project, projectId, requestedWorkflowId, resolvedWorkflowId, workspaceId, workspaceProjects.error, workspaceProjects.isError])

  useEffect(() => {
    if (!loaded.current || !workspaceId || !projectId || !workflowId) return
    const timer = window.setTimeout(() => {
      saveDraft(workflowProject).catch((reason: Error) => toast.error(`自动保存失败：${reason.message}`))
    }, 800)
    return () => window.clearTimeout(timer)
  }, [projectId, saveDraft, workflowId, workflowProject, workspaceId])

  useEffect(() => {
    if (!loaded.current) return
    const imageNodeId = params.get('imageNode')
    const imageDocumentId = params.get('imageDocument')
    const imageAssetIds = (params.get('imageAssets') ?? '')
      .split(',')
      .map((assetId) => assetId.trim())
      .filter(Boolean)
    if (!imageNodeId || (!imageDocumentId && imageAssetIds.length === 0)) return
    const returnKey = `${imageNodeId}:${imageDocumentId ?? ''}:${imageAssetIds.join(',')}`
    if (consumedImageReturn.current === returnKey) return
    const imageNode = workflowProject.nodes.find((node) => node.id === imageNodeId)
    if (!imageNode) {
      toast.error('图像工作台返回了不存在的工作流节点')
      return
    }

    consumedImageReturn.current = returnKey
    if (imageNode.ui?.catalogId === IMAGE_ASSET_CATALOG_ID && imageAssetIds.length > 0) {
      updateWorkflowNodeParams(imageNodeId, { assetIds: imageAssetIds })
    } else if (imageDocumentId) {
      updateWorkflowNodeParams(imageNodeId, { canvasDocumentId: imageDocumentId })
    }
    const searchParams = new URLSearchParams(params.toString())
    searchParams.delete('imageNode')
    searchParams.delete('imageDocument')
    searchParams.delete('imageAssets')
    router.replace(`/studio/workflow?${searchParams.toString()}`, { scroll: false })
  }, [params, router, updateWorkflowNodeParams, workflowProject.nodes])

  useEffect(() => {
    if (loaded.current) {
      setReleaseState('idle')
      setValidationRunId(null)
      setReleaseBlocker(null)
      setPublishedVersion(null)
      setValidationScope(null)
    }
  }, [workflowProject])

  async function validateDraft() {
    if (!workspaceId || !projectId || !workflowId) return
    if (capabilityLoading) {
      toast.info('运行能力目录仍在加载，请稍候再验证')
      return
    }
    if (capabilityError) {
      toast.error('运行能力目录不可用，当前 Draft 不能验证或发布')
      return
    }
    setReleaseState('validating')
    setReleaseBlocker(null)
    setValidationScope(null)
    try {
      await saveDraft(workflowProject)
      const run = await validateProjectWorkflowDraft(workspaceId, projectId, workflowId)
      const validationRun = run as typeof run & {
        warnings?: Array<{ code?: string; nodeId?: string | null; node_id?: string | null }>
      }
      const rawWarnings = Array.isArray(validationRun.warnings) ? validationRun.warnings : []
      const parkedNodeIds = new Set(
        rawWarnings
          .filter((warning) => warning?.code === 'parked_node')
          .map((warning) => warning?.nodeId ?? warning?.node_id)
          .filter((value): value is string => typeof value === 'string' && value.length > 0),
      )
      const canvasNodeCount = workflowProject.nodes.length
      const parkedCount = parkedNodeIds.size
      const activeCount = Math.max(0, canvasNodeCount - parkedCount)
      setValidationScope({ active: activeCount, parked: parkedCount })
      if (!run.valid || run.status !== 'completed') {
        const details = run.errors.slice(0, 3).map((error) => error.message).filter(Boolean)
        throw new Error(details.length ? `验证失败：${details.join('；')}` : `验证 Run 状态：${run.status}`)
      }
      setValidationRunId(run.runId)
      setReleaseState('validated')
      toast.success('验证 Run 已通过，可以发布')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '验证失败'
      setReleaseBlocker(message)
      setReleaseState('blocked')
      toast.error(message)
    }
  }

  async function publishDraft() {
    if (!workspaceId || !projectId || !workflowId) return
    if (revision.current === null || !validationRunId) {
      toast.error('请先完成当前 revision 的验证 Run')
      return
    }
    setReleaseState('publishing')
    try {
      const version = await publishProjectWorkflow(workspaceId, projectId, workflowId, {
        reason: '通过工作区画布发布',
        expectedRevision: revision.current,
        validationRunId,
      })
      setPublishedVersion(version.version)
      setReleaseBlocker(null)
      setReleaseState('published')
      toast.success(`Workflow Version ${version.version} 已发布`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '发布失败'
      setReleaseBlocker(message)
      setReleaseState('blocked')
      toast.error(message)
    }
  }

  async function createBlankWorkflow() {
    if (!workspaceId || !projectId || !project) return
    const name = project.name
    setCreationError(null)
    try {
      const workflow = await createWorkflow.mutateAsync({
        workspaceId,
        projectId,
        data: {
          name,
          description: '从空白画布创建',
          graph: studioGraphForTemplate('blank', name),
        },
      })
      queryClient.setQueryData<WorkflowAssetSummary[]>(
        ['project-workflows', workspaceId, projectId],
        (current) => current?.some((item) => item.id === workflow.id) ? current : [...(current ?? []), workflow],
      )
      void queryClient.invalidateQueries({ queryKey: ['workspace-projects', workspaceId] })
      toast.success('工作流已创建')
      router.replace(`/studio/workflow?workspace=${workspaceId}&project=${projectId}&workflow=${workflow.id}`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '工作流创建失败'
      setCreationError(message)
      toast.error(message)
    }
  }

  return (
    <div className="relative h-full w-full overflow-hidden">
      {standalone ? (
        <ErrorBoundary label="WorkflowEditor">
          <WorkflowEditor />
        </ErrorBoundary>
      ) : missingProjectContext ? (
        <div className="grid h-full place-items-center px-4">
          <div className="flex max-w-lg flex-col items-center gap-4 text-center">
            <div className="grid size-11 place-items-center rounded-md border bg-muted/30 text-destructive">
              <AlertTriangle className="size-5" aria-hidden />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold">无法打开工作流</h2>
              <p className="text-sm leading-6 text-muted-foreground" role="alert">当前地址缺少工作区或项目参数，请从 Studio 重新选择项目。</p>
            </div>
            <Button className="min-h-11" variant="outline" nativeButton={false} render={<Link href="/studio" />}>
              <ArrowLeft className="size-4" />
              返回 Studio
            </Button>
          </div>
        </div>
      ) : loadState === 'loading' ? (
        <div className="grid h-full place-items-center bg-muted/10" aria-busy="true">
          <div className="flex flex-col items-center gap-4 text-sm text-muted-foreground" role="status">
            <Matrix
              rows={7}
              cols={7}
              frames={loader}
              fps={10}
              size={5}
              gap={2}
              palette={{
                on: 'var(--color-primary)',
                off: 'var(--color-muted-foreground)',
              }}
              ariaLabel="正在加载工作流"
            />
            <span>正在加载工作流…</span>
          </div>
        </div>
      ) : loadState === 'empty' ? (
        <div className="grid h-full place-items-center px-4">
          <div className="flex max-w-lg flex-col items-center gap-4 text-center">
            <div className="grid size-11 place-items-center rounded-md border bg-muted/30 text-muted-foreground">
              <Workflow className="size-5" aria-hidden />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold">项目还没有工作流</h2>
              <p className="text-sm leading-6 text-muted-foreground">项目“{project?.name}”尚未设置主工作流。创建后会直接打开第一份 Workflow Draft。</p>
              {creationError ? <p className="text-sm leading-6 text-destructive" role="alert">创建失败：{creationError}</p> : null}
            </div>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button className="min-h-11" onClick={() => void createBlankWorkflow()} disabled={createWorkflow.isPending}>
                {createWorkflow.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                创建工作流
              </Button>
              <Button className="min-h-11" variant="outline" nativeButton={false} render={<Link href={projectHref} />}>
                <ArrowLeft className="size-4" />
                返回项目
              </Button>
            </div>
          </div>
        </div>
      ) : loadState === 'error' ? (
        <div className="grid h-full place-items-center px-4">
          <div className="flex max-w-lg flex-col items-center gap-4 text-center">
            <div className="grid size-11 place-items-center rounded-md border bg-destructive/10 text-destructive">
              <AlertTriangle className="size-5" aria-hidden />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-semibold">工作流加载失败</h2>
              <p className="break-words text-sm leading-6 text-destructive" role="alert">{loadError ?? '工作流加载失败'}</p>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Button className="min-h-11" variant="outline" onClick={() => window.location.reload()}>
                <RefreshCw className="size-4" />
                重新加载
              </Button>
              <Button className="min-h-11" variant="outline" nativeButton={false} render={<Link href={projectHref} />}>
                <ArrowLeft className="size-4" />
                返回项目
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <ErrorBoundary label="WorkflowEditor">
          <WorkflowEditor documentState={documentState} />
        </ErrorBoundary>
      )}
      {workspaceId && projectId && workflowId ? (
        <div className="absolute inset-x-3 bottom-3 z-40 ml-auto flex max-w-4xl flex-col gap-2 rounded-md border bg-background/90 p-2 backdrop-blur-xl">
          <WorkflowLifecycleStrip state={documentState === 'error' || documentState === 'conflict' || capabilityError ? 'blocked' : releaseState === 'idle' ? 'draft' : releaseState} revision={savedRevision} publishedVersion={publishedVersion} blockerText={documentState === 'conflict' ? '草稿已在其他位置更新，请重新加载。' : documentState === 'error' ? '草稿保存失败。' : capabilityError ? '运行能力目录不可用，暂时无法验证。' : (releaseBlocker ?? undefined)} />
          {validationScope ? (
            <div className="flex items-center justify-between gap-2 px-1.5 text-2xs text-muted-foreground" role="status" data-testid="workflow-validation-scope-summary">
              <span>{`活动节点 ${validationScope.active} · 未接入节点 ${validationScope.parked}`}</span>
              <span className="text-3xs opacity-70">未接入节点仅提示，不会阻止发布</span>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="mr-auto flex items-center gap-1.5 px-1.5 text-xs text-muted-foreground" role="status">
              {documentState === 'loading' || documentState === 'saving' ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {documentState === 'saved' ? <CheckCircle2 className="size-3.5 text-success" /> : null}
              {documentState === 'error' || documentState === 'conflict' ? <AlertTriangle className="size-3.5 text-warning" /> : null}
              {
                {
                  loading: '加载中',
                  saving: '保存中',
                  saved: `已保存 · revision ${savedRevision ?? '—'}`,
                  error: '保存失败',
                  conflict: '保存冲突',
                }[documentState]
              }
            </div>
            {documentState === 'conflict' ? (
              <Button className="min-h-11 sm:min-h-7" size="sm" variant="outline" onClick={() => window.location.reload()}>
                <RefreshCw className="size-3.5" />
                重新加载
              </Button>
            ) : null}
            {documentState === 'error' ? (
              <Button className="min-h-11 sm:min-h-7" size="sm" variant="outline" onClick={() => void saveDraft(workflowProject)}>
                <RefreshCw className="size-3.5" />
                重试保存
              </Button>
            ) : null}
            <Button className="min-h-11 sm:min-h-7" size="sm" variant="outline" onClick={validateDraft} disabled={capabilityLoading || Boolean(capabilityError) || releaseState === 'validating' || releaseState === 'publishing'} title={capabilityLoading ? '正在加载运行能力目录' : capabilityError ? '运行能力目录不可用' : undefined}>
              {capabilityLoading || releaseState === 'validating' ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCircle2 className="size-3.5" />}
              验证
            </Button>
            <Button className="min-h-11 sm:min-h-7" size="sm" onClick={publishDraft} disabled={releaseState !== 'validated'}>
              {releaseState === 'publishing' ? <Loader2 className="size-3.5 animate-spin" /> : <Rocket className="size-3.5" />}
              发布
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
