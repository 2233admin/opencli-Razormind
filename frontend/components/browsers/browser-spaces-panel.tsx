'use client'

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { Loader2, Pause, Plus, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { useAuth } from '@/components/auth/auth-provider'

import { useMyWorkspaces } from '@/lib/api/hooks'
import {
  cancelBrowserSpace,
  closeBrowserSpace,
  createBrowserSpace,
  getBrowserSpace,
  listBrowserSpaceEvents,
  listBrowserSpaces,
  listBrowserSpaceInstances,
  submitBrowserSpaceTask,
  type BrowserSpaceCreateRequest,
  type BrowserSpaceEvent,
  type BrowserSpaceOwnerType,
} from '@/lib/api/browser-spaces'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { StatusBadge } from '@/components/shell/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const MAX_ARGS_BYTES = 64 * 1024
const MAX_TIMEOUT_SECONDS = 600

function newRequestId(): string {
  return `browser-space-${crypto.randomUUID()}`
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

function errorCode(error: unknown): string | null {
  const message = errorText(error)
  const match = message.match(/(?:browser_instance_in_use|space_task_in_progress|isolation_unavailable|runtime_[a-z0-9_]+|closed_space)/i)
  return match?.[0] ?? null
}

function formattedJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '结果无法展示'
  }
}

function eventLabel(event: BrowserSpaceEvent): string {
  const labels: Record<BrowserSpaceEvent['kind'], string> = {
    queued: '已排队',
    started: '已开始',
    completed: '已完成',
    failed: '执行失败',
    cancel_requested: '已请求取消',
    cancelled: '已取消',
  }
  return labels[event.kind]
}

export function BrowserSpacesPanel() {
  const { identity } = useAuth()
  const queryClient = useQueryClient()
  const searchParams = useSearchParams()
  const workspaces = useMyWorkspaces()
  const workspaceId = searchParams.get('workspace') ?? workspaces.data?.[0]?.id ?? null
  const instancesQuery = useQuery({
    queryKey: ['browser-space-instances', workspaceId],
    queryFn: () => listBrowserSpaceInstances(workspaceId as string),
    enabled: Boolean(workspaceId),
    refetchInterval: 5_000,
  })
  const spacesQuery = useQuery({
    queryKey: ['browser-spaces', workspaceId],
    queryFn: () => listBrowserSpaces(workspaceId as string),
    enabled: Boolean(workspaceId),
    refetchInterval: 5_000,
  })
  const [selectedSpaceId, setSelectedSpaceId] = useState<string | null>(null)
  const [browserInstanceId, setBrowserInstanceId] = useState('')
  const [bindingId, setBindingId] = useState('')
  const [ownerType, setOwnerType] = useState<BrowserSpaceOwnerType>('operator')
  const [ownerId, setOwnerId] = useState('')
  const [grantedCapabilities, setGrantedCapabilities] = useState('page.metadata')
  const [capability, setCapability] = useState('page.metadata')
  const [requestId, setRequestId] = useState(newRequestId)
  const [argsText, setArgsText] = useState('{}')
  const [timeoutSeconds, setTimeoutSeconds] = useState('60')
  const [confirmAction, setConfirmAction] = useState<'cancel' | 'close' | null>(null)

  const spaces = useMemo(() => spacesQuery.data?.spaces ?? [], [spacesQuery.data])
  const selectedSpace = spaces.find((space) => space.id === selectedSpaceId) ?? spaces[0] ?? null
  const activeSpaceId = selectedSpace?.id ?? null
  const detailQuery = useQuery({
    queryKey: ['browser-space', workspaceId, activeSpaceId],
    queryFn: () => getBrowserSpace(workspaceId as string, activeSpaceId as string),
    enabled: Boolean(workspaceId && activeSpaceId),
    refetchInterval: 2_000,
  })
  const eventsQuery = useQuery({
    queryKey: ['browser-space-events', workspaceId, activeSpaceId],
    queryFn: () => listBrowserSpaceEvents(workspaceId as string, activeSpaceId as string),
    enabled: Boolean(workspaceId && activeSpaceId),
    refetchInterval: 2_000,
  })
  const detail = detailQuery.data ?? selectedSpace
  const activeTask = detailQuery.data?.latest_task ?? detailQuery.data?.active_task ?? null
  const taskInProgress = activeTask?.status === 'queued' || activeTask?.status === 'running'
  const capabilities = useMemo(
    () => grantedCapabilities.split(',').map((item) => item.trim()).filter(Boolean),
    [grantedCapabilities],
  )

  useEffect(() => {
    const subject = identity?.subject
    if (ownerType === 'operator' && subject && (!ownerId || ownerId === 'operator')) {
      setOwnerId(subject)
    }
  }, [identity?.subject, ownerId, ownerType])

  useEffect(() => {
    if (!selectedSpaceId && spaces[0]) setSelectedSpaceId(spaces[0].id)
    if (selectedSpaceId && !spaces.some((space) => space.id === selectedSpaceId)) setSelectedSpaceId(spaces[0]?.id ?? null)
  }, [selectedSpaceId, spaces])

  const defaultCapability = selectedSpace?.granted_capabilities[0] ?? ''
  useEffect(() => {
    setConfirmAction(null)
    setRequestId(newRequestId())
    setCapability(defaultCapability)
  }, [workspaceId, activeSpaceId, defaultCapability])

  const refreshSpaces = () => {
    void queryClient.invalidateQueries({ queryKey: ['browser-space-instances', workspaceId] })
    void queryClient.invalidateQueries({ queryKey: ['browser-spaces', workspaceId] })
    void queryClient.invalidateQueries({ queryKey: ['browser-space', workspaceId] })
    void queryClient.invalidateQueries({ queryKey: ['browser-space-events', workspaceId] })
  }

  const createMutation = useMutation({
    mutationFn: (data: BrowserSpaceCreateRequest) => createBrowserSpace(workspaceId as string, data),
    onSuccess: (space) => {
      toast.success('Browser Space 已创建')
      setSelectedSpaceId(space.id)
      refreshSpaces()
    },
    onError: (error) => toast.error(errorText(error)),
  })
  const submitMutation = useMutation({
    mutationFn: () => {
      if (!workspaceId || !activeSpaceId) throw new Error('请先选择 Browser Space')
      const trimmedCapability = capability.trim()
      if (!trimmedCapability || trimmedCapability.length > 255) throw new Error('Capability 名称必须为 1-255 个字符')
      const parsedArgs: unknown = JSON.parse(argsText)
      if (!parsedArgs || typeof parsedArgs !== 'object' || Array.isArray(parsedArgs)) throw new Error('Args 必须是 JSON 对象')
      if (new TextEncoder().encode(argsText).byteLength > MAX_ARGS_BYTES) throw new Error('Args 超过 64 KiB 限制')
      const parsedTimeout = Number(timeoutSeconds)
      if (!Number.isInteger(parsedTimeout) || parsedTimeout < 1 || parsedTimeout > MAX_TIMEOUT_SECONDS) throw new Error('超时必须为 1-600 秒')
      return submitBrowserSpaceTask(workspaceId, activeSpaceId, {
        request_id: requestId.trim(),
        capability: trimmedCapability,
        args: parsedArgs as Record<string, unknown>,
        timeout_seconds: parsedTimeout,
      })
    },
    onSuccess: () => {
      toast.success('任务已提交')
      setRequestId(newRequestId())
      refreshSpaces()
    },
    onError: (error) => toast.error(errorText(error)),
  })
  const cancelMutation = useMutation({
    mutationFn: () => cancelBrowserSpace(workspaceId as string, activeSpaceId as string),
    onSuccess: () => {
      toast.success('已请求取消任务')
      setConfirmAction(null)
      refreshSpaces()
    },
    onError: (error) => toast.error(errorText(error)),
  })
  const closeMutation = useMutation({
    mutationFn: () => closeBrowserSpace(workspaceId as string, activeSpaceId as string),
    onSuccess: () => {
      toast.success('Browser Space 已关闭')
      setConfirmAction(null)
      refreshSpaces()
    },
    onError: (error) => toast.error(errorText(error)),
  })

  const handleCreate = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const instanceId = browserInstanceId.trim()
    const nextOwnerId = ownerId.trim()
    if (!workspaceId || !instanceId || !nextOwnerId || capabilities.length === 0) {
      toast.error('请填写 BrowserInstance ID、Owner ID 和至少一个 Capability')
      return
    }
    createMutation.mutate({
      browser_instance_id: instanceId,
      binding_id: bindingId.trim() || undefined,
      owner_type: ownerType,
      owner_id: nextOwnerId,
      granted_capabilities: capabilities,
    })
  }

  const hasMutation = createMutation.isPending || submitMutation.isPending || cancelMutation.isPending || closeMutation.isPending
  const operationError = spacesQuery.error ?? detailQuery.error ?? eventsQuery.error

  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/20 py-4">
        <CardTitle className="text-base">Browser Spaces</CardTitle>
        <CardDescription>
          为 Workspace 中的 Agent 保留独占 BrowserInstance。任务只调用已授权的命名 Capability，不提供共享标签页或 CDP 回退。
        </CardDescription>
        <CardAction>
          {workspaceId ? <Badge variant="outline">Workspace 已选择</Badge> : <Badge variant="destructive">缺少 Workspace</Badge>}
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-6 p-4 xl:grid-cols-[minmax(16rem,0.8fr)_minmax(0,1.2fr)]">
        <section className="space-y-4" aria-labelledby="browser-space-create-title">
          <div>
            <h3 id="browser-space-create-title" className="font-medium">创建 Space</h3>
            <p className="mt-1 text-xs text-muted-foreground">只能使用已有且获授权的 BrowserInstance。此页面不展示实例端点、Cookie 或其他运行时凭据。</p>
          </div>
          <form className="space-y-3" onSubmit={handleCreate}>
            <label className="block space-y-1 text-sm">
              <span>BrowserInstance ID</span>
              <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={browserInstanceId} onChange={(event) => {
                const instance = instancesQuery.data?.find((item) => item.id === event.target.value)
                setBrowserInstanceId(event.target.value)
                setGrantedCapabilities(instance?.capabilities.join(', ') ?? '')
                setCapability(instance?.capabilities[0] ?? '')
              }} disabled={hasMutation || !workspaceId || !instancesQuery.data?.length}>
                <option value="">选择可用实例</option>
                {(instancesQuery.data ?? []).map((instance) => <option key={instance.id} value={instance.id}>{instance.id}</option>)}
              </select>
              {instancesQuery.error ? <p role="alert" className="text-xs text-destructive">{errorText(instancesQuery.error)}</p> : null}
            </label>
            <label className="block space-y-1 text-sm">
              <span>Binding ID（可选）</span>
              <Input value={bindingId} onChange={(event) => setBindingId(event.target.value)} placeholder="已有绑定的 opaque ID" disabled={hasMutation || !workspaceId} />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block space-y-1 text-sm"><span>Owner 类型</span><select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={ownerType} onChange={(event) => setOwnerType(event.target.value as BrowserSpaceOwnerType)} disabled={hasMutation || !workspaceId}><option value="operator">operator</option><option value="runtime_agent">runtime_agent</option></select></label>
              <label className="block space-y-1 text-sm"><span>Owner ID</span><Input value={ownerId} onChange={(event) => setOwnerId(event.target.value)} placeholder="opaque identity" disabled={hasMutation || !workspaceId} /></label>
            </div>
            <label className="block space-y-1 text-sm"><span>授权 Capabilities（逗号分隔）</span><Input value={grantedCapabilities} onChange={(event) => setGrantedCapabilities(event.target.value)} placeholder="page.metadata, page.read" disabled={hasMutation || !workspaceId} /></label>
            <Button type="submit" className="w-full" disabled={hasMutation || !workspaceId || !instancesQuery.data?.some((instance) => instance.id === browserInstanceId)}><Plus className="size-4" />{createMutation.isPending ? '正在创建…' : '创建 Browser Space'}</Button>
          </form>
          {createMutation.error ? <p role="alert" className="text-xs text-destructive">{errorCode(createMutation.error) ? `${errorCode(createMutation.error)}：` : ''}{errorText(createMutation.error)}</p> : null}
          <div className="border-t pt-4">
            <h3 className="font-medium">Workspace Spaces</h3>
            {workspaces.isLoading || spacesQuery.isLoading ? <LoadingState /> : operationError && spaces.length === 0 ? <ErrorState message={errorText(operationError)} hint={BACKEND_HINT} /> : spaces.length === 0 ? <EmptyState title="暂无 Browser Space" description="先使用已有 BrowserInstance ID 创建一个独占 Space。" /> : <div className="mt-2 space-y-2">{spaces.map((space) => <button key={space.id} type="button" className={`w-full rounded-md border p-3 text-left transition-colors hover:bg-muted/40 ${space.id === activeSpaceId ? 'border-primary bg-muted/30' : ''}`} onClick={() => setSelectedSpaceId(space.id)}><div className="flex items-center justify-between gap-2"><span className="truncate font-mono text-xs">{space.id}</span><StatusBadge status={space.status} /></div><div className="mt-2 flex flex-wrap gap-1">{space.granted_capabilities.map((item) => <Badge key={item} variant="secondary">{item}</Badge>)}</div></button>)}</div>}
          </div>
        </section>

        <section className="min-w-0 space-y-4" aria-labelledby="browser-space-detail-title">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="browser-space-detail-title" className="font-medium">Space 任务与事件</h3>
              <p className="mt-1 text-xs text-muted-foreground">事件按 sequence 升序回放；结果仅显示后端已截断、脱敏的投影。</p>
            </div>
            {detail ? (
              <div className="flex gap-2">
                <Button
                  size="xs"
                  variant={confirmAction === 'cancel' ? 'destructive' : 'outline'}
                  disabled={hasMutation || detail.status === 'closed' || !taskInProgress}
                  onClick={() => confirmAction === 'cancel' ? cancelMutation.mutate() : setConfirmAction('cancel')}
                >
                  <Pause className="size-3" />
                  {confirmAction === 'cancel' ? '确认取消' : '取消当前任务'}
                </Button>
                <Button
                  size="xs"
                  variant={confirmAction === 'close' ? 'destructive' : 'outline'}
                  disabled={hasMutation || detail.status === 'closed'}
                  onClick={() => confirmAction === 'close' ? closeMutation.mutate() : setConfirmAction('close')}
                >
                  <X className="size-3" />
                  {confirmAction === 'close' ? '确认关闭' : '关闭 Space'}
                </Button>
              </div>
            ) : null}
          </div>
          {!activeSpaceId ? <EmptyState title="选择一个 Space" description="创建或选择左侧的 Space 后，可提交任务并查看事件。" /> : detailQuery.isLoading ? <LoadingState /> : detailQuery.error ? <ErrorState message={errorText(detailQuery.error)} hint={BACKEND_HINT} /> : detail ? <>
            {detail.last_error_code ? <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">运行时错误：{detail.last_error_code}</p> : null}
            <div className="grid gap-2 sm:grid-cols-3"><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">Space 状态</p><div className="mt-1"><StatusBadge status={detail.status} /></div></div><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">BrowserInstance</p><p className="mt-1 truncate font-mono text-xs">{detail.browser_instance_id}</p></div><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground">授权能力</p><p className="mt-1 text-xs">{detail.granted_capabilities.join(', ') || '无'}</p></div></div>
            <form className="space-y-3 rounded-md border p-4" onSubmit={(event) => { event.preventDefault(); submitMutation.mutate() }}><div className="flex items-center justify-between gap-2"><h4 className="font-medium">提交命名 Capability</h4><Badge variant="outline">无共享标签页回退</Badge></div><div className="grid gap-2 sm:grid-cols-2"><label className="block space-y-1 text-sm"><span>Capability</span><Input value={capability} onChange={(event) => setCapability(event.target.value)} list="browser-space-capabilities" disabled={hasMutation || detail.status === 'closed'} /><datalist id="browser-space-capabilities">{detail.granted_capabilities.map((item) => <option key={item} value={item} />)}</datalist></label><label className="block space-y-1 text-sm"><span>Request ID</span><Input value={requestId} onChange={(event) => setRequestId(event.target.value)} maxLength={64} disabled={hasMutation || detail.status === 'closed'} /></label></div><label className="block space-y-1 text-sm"><span>Args（JSON 对象，最大 64 KiB）</span><Textarea value={argsText} onChange={(event) => setArgsText(event.target.value)} rows={4} spellCheck={false} className="font-mono text-xs" disabled={hasMutation || detail.status === 'closed'} /></label><label className="block max-w-48 space-y-1 text-sm"><span>超时（秒，1-600）</span><Input type="number" min={1} max={MAX_TIMEOUT_SECONDS} value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(event.target.value)} disabled={hasMutation || detail.status === 'closed'} /></label><Button type="submit" disabled={hasMutation || detail.status === 'closed' || taskInProgress}>{submitMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}{submitMutation.isPending ? '正在提交…' : '提交任务'}</Button>{submitMutation.error ? <p role="alert" className="text-xs text-destructive">{errorCode(submitMutation.error)}：{errorText(submitMutation.error)}</p> : null}</form>
            {activeTask ? <div className="rounded-md border p-4"><div className="flex items-center justify-between gap-2"><h4 className="font-medium">最近任务</h4><StatusBadge status={activeTask.status} /></div><p className="mt-2 font-mono text-xs text-muted-foreground">{activeTask.capability ?? '已授权 Capability'} · {activeTask.operation_id}</p>{activeTask.error ? <p role="alert" className="mt-2 text-xs text-destructive">{activeTask.error}</p> : null}{activeTask.result ? <pre className="mt-3 max-h-64 overflow-auto rounded bg-muted/40 p-3 text-xs">{formattedJson(activeTask.result)}</pre> : null}</div> : null}
            <div className="rounded-md border p-4"><h4 className="font-medium">事件时间线</h4>{eventsQuery.isLoading ? <LoadingState /> : eventsQuery.error ? <ErrorState message={errorText(eventsQuery.error)} hint={BACKEND_HINT} /> : (eventsQuery.data?.events ?? []).length === 0 ? <p className="mt-3 text-xs text-muted-foreground">暂无事件</p> : <ol className="mt-3 space-y-2">{(eventsQuery.data?.events ?? []).map((event) => <li key={event.id} className="flex gap-3 border-l-2 border-muted pl-3 text-xs"><span className="font-mono text-muted-foreground">#{event.sequence}</span><span><strong>{eventLabel(event)}</strong><span className="ml-2 text-muted-foreground">{event.created_at}</span>{event.payload && Object.keys(event.payload).length > 0 ? <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 p-2 text-[11px]">{formattedJson(event.payload)}</pre> : null}</span></li>)}</ol>}</div>
          </> : null}
        </section>
      </CardContent>
    </Card>
  )
}
