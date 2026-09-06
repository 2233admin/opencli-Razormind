'use client'

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Clipboard, Link2, LoaderCircle, RefreshCw, ShieldAlert, Unlink } from 'lucide-react'
import { toast } from 'sonner'

import { useAgentConversation } from '@/lib/api/hooks'
import {
  connectorInstallationHealthQueryKey,
  connectorMyBindingQueryKey,
  getConnectorInstallationHealth,
  getMyConnectorBinding,
  useConnectorInstallations,
  type ConnectorBinding,
  type ConnectorInstallation,
  type ConnectorInstallationHealth,
} from '@/lib/api/connector-installations'
import {
  connectorArtifactGrantsQueryKey,
  createConnectorArtifactGrant,
  type ConnectorArtifactGrantCreated,
  type ConnectorReplyGrant,
  useConnectorArtifactGrant,
  useConnectorArtifactGrants,
  useConnectorReplyGrant,
  useConnectorReplyGrants,
  useCreateConnectorReplyGrant,
  useRevokeConnectorArtifactGrant,
  useRevokeConnectorReplyGrant,
} from '@/lib/api/connector-reply-grants'
import type { ProjectArtifactDetail } from '@/lib/api/project-artifacts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type ConnectorReplyGrantPanelProps = {
  artifact: ProjectArtifactDetail
  open: boolean
}

type Candidate = {
  installation: ConnectorInstallation
  binding: ConnectorBinding | null
  health: ConnectorInstallationHealth | null
  healthError: boolean
}

type SafeError = Error & { code?: string; status?: number }

const DELIVERY_LABELS: Record<string, string> = {
  pending: '等待发送',
  connecting: '连接中',
  sending: '发送中',
  sent: '已发送',
  retryable_failed: '需要处理',
  failed: '发送失败',
  indeterminate: '状态未知',
}

const GRANT_LABELS: Record<string, string> = {
  active: '有效',
  revoked: '已撤销',
  expired: '已过期',
  redeemed: '已领取',
}

function statusLabel(status: string | null | undefined, labels: Record<string, string> = DELIVERY_LABELS) {
  if (!status) return '尚未开始'
  return labels[status] ?? `未知状态（${status}）`
}

function statusVariant(status: string | null | undefined): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'sent' || status === 'active') return 'secondary'
  if (status === 'failed' || status === 'indeterminate' || status === 'revoked') return 'destructive'
  return 'outline'
}

function GrantStatusBadge({ status, labels = DELIVERY_LABELS }: { status: string | null | undefined; labels?: Record<string, string> }) {
  return <Badge variant={statusVariant(status)}>{statusLabel(status, labels)}</Badge>
}

function formatExpiry(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '有效期未知' : `有效期至 ${date.toLocaleString()}`
}

function requestId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `connector-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function safeErrorMessage(error: unknown, action: 'reply' | 'artifact' | 'revoke') {
  const reason = error as SafeError | null
  const code = reason?.code ?? reason?.message
  if (reason?.status === 403) return '当前账号没有完成此操作所需的 Workspace 或报告权限。'
  if (reason?.status === 404) return '授权对象已不可用，可能已被撤销或不属于当前报告范围。请刷新后重试。'
  if (reason?.status === 503) return '当前连接回复能力暂不可用，请稍后刷新状态。'
  if (reason?.status === 409) {
    if (code === 'idempotency_key_reused') return '同一请求编号对应的参数已变化，请重新选择后再试。'
    if (code === 'active_reply_grant_exists') return '当前原会话已有有效授权，请使用已恢复的授权或先撤销它。'
    if (code === 'active_artifact_grant_exists') return '当前报告已有进行中的领取授权，请刷新状态后再处理。'
    if (code?.includes('expired')) return '授权已经过期，请重新授权。'
    return '服务端拒绝了这次授权状态变更，请刷新状态后再试。'
  }
  if (action === 'revoke') return '撤销没有完成，请刷新状态后重试。'
  return '操作没有完成；可以使用相同请求重试，系统不会重复授权。'
}

function validateConversationScope(artifact: ProjectArtifactDetail, conversation: ReturnType<typeof useAgentConversation>['data']) {
  if (!conversation) return null
  if (!conversation.workspace_id) return '原会话没有可验证的 governed Workspace。'
  const context = conversation.context_binding ?? {}
  if (context.studio_workspace_id !== artifact.workspace_id) return '原会话与当前报告的 Studio Workspace 不一致。'
  if (context.project_id !== artifact.project_id) return '原会话与当前报告项目不一致。'
  if (context.workflow_id !== artifact.workflow_id) return '原会话与当前报告工作流不一致。'
  if (context.run_id !== artifact.run_id) return '原会话与当前报告运行不一致。'
  return null
}

function useConnectorCandidates(workspaceId: string | null) {
  const installations = useConnectorInstallations(workspaceId)
  const rows = installations.data ?? []
  const bindings = useQueries({
    queries: rows.map((installation) => ({
      queryKey: connectorMyBindingQueryKey(workspaceId, installation.installation_public_id),
      queryFn: () => getMyConnectorBinding(workspaceId as string, installation.installation_public_id),
      enabled: Boolean(workspaceId),
    })),
  })
  const health = useQueries({
    queries: rows.map((installation) => ({
      queryKey: connectorInstallationHealthQueryKey(workspaceId, installation.installation_public_id),
      queryFn: () => getConnectorInstallationHealth(workspaceId as string, installation.installation_public_id),
      enabled: Boolean(workspaceId),
      refetchInterval: 30_000,
    })),
  })
  const candidates = rows.map((installation, index): Candidate => ({
    installation,
    binding: bindings[index]?.data ?? null,
    health: health[index]?.data ?? null,
    healthError: Boolean(health[index]?.isError),
  }))
  return { installations, candidates, bindings, health }
}

function ArtifactGrantAction({
  artifact,
  governedWorkspaceId,
  grant,
  canCreate,
  open,
}: {
  artifact: ProjectArtifactDetail
  governedWorkspaceId: string
  grant: ConnectorReplyGrant
  canCreate: boolean
  open: boolean
}) {
  const queryClient = useQueryClient()
  const artifactGrants = useConnectorArtifactGrants(governedWorkspaceId, artifact.project_id, grant.reply_grant_public_id, open)
  const existing = artifactGrants.data?.find((item) => item.artifact_public_id === artifact.id) ?? null
  const artifactGrantDetail = useConnectorArtifactGrant(
    governedWorkspaceId,
    artifact.project_id,
    grant.reply_grant_public_id,
    existing?.artifact_grant_public_id ?? null,
    open,
  )
  const current = artifactGrantDetail.data ?? existing
  const [claimText, setClaimText] = useState<string | null>(null)
  const [copyState, setCopyState] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestRef = useRef<{ key: string; id: string } | null>(null)
  const revoke = useRevokeConnectorArtifactGrant()
  const requestKey = `${governedWorkspaceId}:${artifact.project_id}:${grant.reply_grant_public_id}:${artifact.id}`
  const currentId = current?.artifact_grant_public_id ?? null
  const currentStatus = current?.status ?? null
  const offerReady = grant.activation_delivery_status === 'sent' && canCreate
  const canCreateArtifact = offerReady && (!current || current.status === 'expired' || current.status === 'revoked' || current.status === 'redeemed')

  useEffect(() => {
    setClaimText(null)
    setCopyState(false)
    setError(null)
    requestRef.current = null
  }, [requestKey, open])

  useEffect(() => {
    if (currentId && currentStatus !== 'active') {
      requestRef.current = null
      setClaimText(null)
    }
  }, [currentId, currentStatus])

  async function createArtifactGrant() {
    if (!canCreateArtifact || pending) return
    setError(null)
    const id = requestRef.current?.key === requestKey ? requestRef.current.id : requestId()
    requestRef.current = { key: requestKey, id }
    setPending(true)
    try {
      const result: ConnectorArtifactGrantCreated = await createConnectorArtifactGrant(
        governedWorkspaceId,
        artifact.project_id,
        grant.reply_grant_public_id,
        {
          request_id: id,
          artifact_public_id: artifact.id,
          workflow_id: artifact.workflow_id,
          run_id: artifact.run_id,
          expires_in_seconds: 1800,
        },
      )
      setClaimText(result.created ? result.claim_text : null)
      if (result.created && result.claim_text) toast.success('报告领取指令已生成并进入投递流程')
      else toast.message('报告领取授权已恢复，新的指令不会再次显示')
      await queryClient.invalidateQueries({ queryKey: connectorArtifactGrantsQueryKey(governedWorkspaceId, artifact.project_id, grant.reply_grant_public_id) })
    } catch (reason) {
      setError(safeErrorMessage(reason, 'artifact'))
    } finally {
      setPending(false)
    }
  }

  async function copyClaim() {
    if (!claimText) return
    try {
      await navigator.clipboard.writeText(claimText)
      setCopyState(true)
      toast.success('领取指令已复制')
    } catch {
      toast.error('复制失败，请手动复制领取指令')
    }
  }

  function revokeArtifactGrant() {
    if (!current || current.status !== 'active') return
    revoke.mutate(
      {
        workspaceId: governedWorkspaceId,
        projectId: artifact.project_id,
        replyGrantId: grant.reply_grant_public_id,
        artifactGrantId: current.artifact_grant_public_id,
      },
      {
        onSuccess: () => toast.success('报告领取授权已撤销'),
        onError: (reason) => setError(safeErrorMessage(reason, 'revoke')),
      },
    )
  }

  const status = current?.delivery_status ?? current?.offer_delivery_status
  return (
    <div className="mt-3 rounded-md border bg-background p-3" data-testid={`connector-artifact-grant-${grant.reply_grant_public_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium">当前报告领取</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">领取有效期默认 30 分钟；领取内容由服务端重新校验当前报告来源。</p>
        </div>
        {current ? <GrantStatusBadge status={current.status} labels={GRANT_LABELS} /> : null}
      </div>
      {current ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>投递：{statusLabel(status)}</span>
          {current.error_code ? <span className="text-destructive">原因：{current.error_code}</span> : null}
          <span>{formatExpiry(current.expires_at)}</span>
        </div>
      ) : null}
      {claimText ? (
        <div className="mt-2 rounded border border-primary/30 bg-primary/5 p-2">
          <p className="text-[11px] text-muted-foreground">这条领取指令只在本次新授权响应中显示一次：</p>
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all font-mono text-xs">{claimText}</code>
            <Button type="button" size="xs" variant="outline" onClick={() => void copyClaim()}><Clipboard className="size-3" />{copyState ? '已复制' : '复制'}</Button>
          </div>
        </div>
      ) : null}
      {error ? <p role="alert" className="mt-2 text-xs text-destructive">{error}</p> : null}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={() => void createArtifactGrant()} disabled={!canCreateArtifact || pending} title={!canCreateArtifact ? '需先完成激活投递，且当前绑定和产物能力可用' : undefined}>
          {pending ? <LoaderCircle className="size-3.5 animate-spin" /> : <Link2 className="size-3.5" />}
          {pending ? '授权中…' : current?.status === 'redeemed' || current?.status === 'expired' || current?.status === 'revoked' ? '重新授权领取' : '授权并发送领取指令'}
        </Button>
        {current?.status === 'active' ? <Button type="button" size="sm" variant="outline" onClick={revokeArtifactGrant} disabled={revoke.isPending}><Unlink className="size-3.5" />{revoke.isPending ? '撤销中…' : '撤销领取授权'}</Button> : null}
      </div>
      {!grant.activation_delivery_status || grant.activation_delivery_status !== 'sent' ? <p className="mt-2 text-[11px] text-muted-foreground">激活消息送达后，才能发送当前报告的领取指令。</p> : null}
      {current?.delivery_status === 'indeterminate' ? <p className="mt-2 text-[11px] text-destructive">投递结果未知，系统不会自动重发；请先在飞书中核对结果。</p> : null}
      {current && current.status === 'active' && (current.delivery_status === 'failed' || current.delivery_status === 'retryable_failed') ? <p className="mt-2 text-[11px] text-destructive">投递没有成功；页面不会自动重发，请撤销后重新授权。</p> : null}
    </div>
  )
}
function ReplyGrantCard({
  artifact,
  governedWorkspaceId,
  grant,
  candidate,
  installationName,
  open,
  revokePending,
  onRevoke,
}: {
  artifact: ProjectArtifactDetail
  governedWorkspaceId: string
  grant: ConnectorReplyGrant
  candidate: Candidate | undefined
  installationName: string | undefined
  open: boolean
  revokePending: boolean
  onRevoke: (grant: ConnectorReplyGrant) => void
}) {
  const detail = useConnectorReplyGrant(
    governedWorkspaceId,
    artifact.project_id,
    artifact.conversation_id,
    grant.reply_grant_public_id,
    open,
  )
  const current = detail.data ?? grant
  const canCreateArtifact = Boolean(candidate?.binding?.active && candidate.health?.artifact_delivery_ready === true)

  return (
    <div className="rounded-md border bg-muted/15 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><p className="text-xs font-medium">已授权连接：{installationName ?? '当前飞书连接'}</p><p className="mt-0.5 text-[11px] text-muted-foreground">激活：{statusLabel(current.activation_delivery_status)} · {formatExpiry(current.expires_at)}</p></div>
        <GrantStatusBadge status={current.activation_delivery_status} />
      </div>
      {current.status === 'active' ? <div className="mt-2 flex flex-wrap gap-2"><Button type="button" size="xs" variant="outline" onClick={() => onRevoke(current)} disabled={revokePending}><Unlink className="size-3" />撤销原会话授权</Button></div> : null}
      {current.status === 'active' ? <ArtifactGrantAction artifact={artifact} governedWorkspaceId={governedWorkspaceId} grant={current} canCreate={canCreateArtifact} open={open} /> : null}
    </div>
  )
}

export function ConnectorReplyGrantPanel({ artifact, open }: ConnectorReplyGrantPanelProps) {
  const conversation = useAgentConversation(artifact.conversation_id, Boolean(artifact.conversation_id))
  const queryClient = useQueryClient()
  const conversationScopeError = validateConversationScope(artifact, conversation.data)
  const governedWorkspaceId = conversationScopeError ? null : conversation.data?.workspace_id ?? null
  const scopeReady = Boolean(governedWorkspaceId && conversation.data?.status === 'active' && !conversationScopeError)
  const candidatesQuery = useConnectorCandidates(governedWorkspaceId)
  const replyGrants = useConnectorReplyGrants(governedWorkspaceId, artifact.project_id, artifact.conversation_id, scopeReady)
  const createReply = useCreateConnectorReplyGrant()
  const revokeReply = useRevokeConnectorReplyGrant()
  const [selectedInstallationId, setSelectedInstallationId] = useState('')
  const [confirmation, setConfirmation] = useState(false)
  const [replyError, setReplyError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const requestRef = useRef<{ key: string; id: string } | null>(null)
  const scopeKey = `${governedWorkspaceId ?? ''}:${artifact.project_id}:${artifact.conversation_id ?? ''}:${artifact.id}`
  const candidates = candidatesQuery.candidates
  const currentCandidate = candidates.find((candidate) => candidate.installation.installation_public_id === selectedInstallationId) ?? null
  const grants = useMemo(() => replyGrants.data ?? [], [replyGrants.data])
  const activeGrants = grants.filter((grant) => grant.status === 'active')
  const selectedHasActiveGrant = activeGrants.some((grant) => grant.installation_public_id === selectedInstallationId)
  const installationNames = useMemo(() => new Map(candidates.map((candidate) => [candidate.installation.installation_public_id, candidate.installation.name])), [candidates])

  useEffect(() => {
    const selectedGrant = grants.find((grant) => grant.installation_public_id === selectedInstallationId)
    if (selectedGrant && selectedGrant.status !== 'active') requestRef.current = null
  }, [grants, selectedInstallationId])

  useEffect(() => {
    setSelectedInstallationId('')
    setConfirmation(false)
    setReplyError(null)
    requestRef.current = null
  }, [scopeKey, open])

  async function refresh() {
    setRefreshing(true)
    try {
      await Promise.all([
        replyGrants.refetch(),
        queryClient.refetchQueries({ queryKey: ['connector-reply-grant', governedWorkspaceId, artifact.project_id] }),
        candidatesQuery.installations.refetch(),
        ...candidatesQuery.bindings.map((query) => query.refetch()),
        ...candidatesQuery.health.map((query) => query.refetch()),
      ])
    } finally {
      setRefreshing(false)
    }
  }

  function createReplyGrant() {
    if (!governedWorkspaceId || !currentCandidate || !confirmation || !scopeReady || createReply.isPending) return
    if (currentCandidate.installation.status !== 'active' || !currentCandidate.binding?.active || currentCandidate.health?.reply_execution_ready !== true || currentCandidate.health.artifact_delivery_ready !== true) return
    setReplyError(null)
    const requestKey = `${scopeKey}:${selectedInstallationId}:${currentCandidate.binding?.binding_public_id ?? ''}`
    const id = requestRef.current?.key === requestKey ? requestRef.current.id : requestId()
    requestRef.current = { key: requestKey, id }
    createReply.mutate(
      {
        workspaceId: governedWorkspaceId,
        projectId: artifact.project_id,
        input: {
          request_id: id,
          installation_public_id: currentCandidate.installation.installation_public_id,
          binding_public_id: currentCandidate.binding.binding_public_id,
          conversation_id: artifact.conversation_id!,
          expires_in_seconds: 604800,
        },
      },
      {
        onSuccess: (result) => {
          setConfirmation(false)
          toast.success(result.created ? '原会话授权已创建，正在等待激活投递' : '已恢复原会话授权状态')
        },
        onError: (reason) => setReplyError(safeErrorMessage(reason, 'reply')),
      },
    )
  }

  function revokeReplyGrant(grant: ConnectorReplyGrant) {
    if (!governedWorkspaceId) return
    revokeReply.mutate(
      { workspaceId: governedWorkspaceId, projectId: artifact.project_id, replyGrantId: grant.reply_grant_public_id },
      {
        onSuccess: () => toast.success('原会话授权已撤销'),
        onError: (reason) => setReplyError(safeErrorMessage(reason, 'revoke')),
      },
    )
  }

  if (!artifact.conversation_id) {
    return <UnavailablePanel message="当前报告没有可验证的原 Agent 会话，无法授权飞书回复或领取。" />
  }
  if (conversation.isLoading) {
    return <PanelShell><p role="status" className="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-3.5 animate-spin" />正在读取原会话授权范围…</p></PanelShell>
  }
  if (conversation.isError) {
    return <UnavailablePanel message="原 Agent 会话暂时无法读取，无法安全授权飞书回复。" />
  }
  if (conversationScopeError) {
    return <UnavailablePanel message={conversationScopeError} />
  }
  if (conversation.data?.status !== 'active') {
    return <UnavailablePanel message="原 Agent 会话已关闭，不能创建新的飞书回复授权。" />
  }
  if (!governedWorkspaceId) {
    return <UnavailablePanel message="原 Agent 会话没有可用的 governed Workspace，无法读取飞书连接。" />
  }

  return (
    <PanelShell>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2"><ShieldAlert className="size-3.5 text-primary" aria-hidden="true" /><h3 id="connector-reply-grant-title" className="text-xs font-medium">飞书回复与报告领取</h3></div>
          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">授权当前绑定的飞书机器人回复同一原会话，并发送这份报告的领取指令。原会话授权默认 7 天，报告领取授权默认 30 分钟。</p>
        </div>
        <Button type="button" size="xs" variant="outline" onClick={() => void refresh()} disabled={refreshing}><RefreshCw className={refreshing ? 'size-3 animate-spin' : 'size-3'} />刷新</Button>
      </div>

      {candidatesQuery.installations.isLoading ? <p role="status" className="mt-3 text-xs text-muted-foreground">正在读取已绑定的飞书连接…</p> : null}
      {candidatesQuery.installations.isError ? <p role="alert" className="mt-3 text-xs text-destructive">飞书连接暂时无法读取，请稍后重试。</p> : null}
      {replyGrants.isLoading ? <p role="status" className="mt-3 text-xs text-muted-foreground">正在恢复当前原会话授权…</p> : null}
      {replyGrants.isError ? <p role="alert" className="mt-3 text-xs text-destructive">原会话授权状态暂时无法读取，请刷新后重试。</p> : null}

      {grants.length ? (
        <div className="mt-3 space-y-2">
          {grants.map((grant) => <ReplyGrantCard key={grant.reply_grant_public_id} artifact={artifact} governedWorkspaceId={governedWorkspaceId} grant={grant} candidate={candidates.find((item) => item.installation.installation_public_id === grant.installation_public_id)} installationName={installationNames.get(grant.installation_public_id)} open={open} revokePending={revokeReply.isPending} onRevoke={revokeReplyGrant} />)}
        </div>
      ) : null}

      {candidates.length ? (
        <div className="mt-3 rounded-md border p-3">
          <label className="block text-xs font-medium" htmlFor={`connector-reply-installation-${artifact.id}`}>选择已绑定的飞书机器人</label>
          <select id={`connector-reply-installation-${artifact.id}`} value={selectedInstallationId} onChange={(event) => { setSelectedInstallationId(event.target.value); setConfirmation(false); setReplyError(null) }} className="mt-2 h-9 w-full rounded-md border bg-background px-2 text-xs">
            <option value="">请选择连接</option>
            {candidates.map((candidate) => {
              const ready = candidate.installation.status === 'active' && candidate.binding?.active === true && candidate.health?.reply_execution_ready === true && candidate.health?.artifact_delivery_ready === true
              return <option key={candidate.installation.installation_public_id} value={candidate.installation.installation_public_id} disabled={!ready}>{candidate.installation.name}{ready ? '' : '（当前不可用）'}</option>
            })}
          </select>
          {currentCandidate ? <p className="mt-2 text-[11px] text-muted-foreground">绑定状态：{currentCandidate.binding?.active ? '当前用户已绑定' : '当前用户未绑定'}；回复能力：{currentCandidate.health?.reply_execution_ready ? '可用' : '不可用'}；领取能力：{currentCandidate.health?.artifact_delivery_ready ? '可用' : '不可用'}。</p> : null}
          <label className="mt-3 flex items-start gap-2 text-xs"><input type="checkbox" checked={confirmation} onChange={(event) => setConfirmation(event.target.checked)} disabled={!currentCandidate} className="mt-0.5 size-4 accent-primary" /><span>我确认授权这个已绑定连接回复当前报告对应的同一原 Agent 会话。</span></label>
          {replyError ? <p role="alert" className="mt-2 text-xs text-destructive">{replyError}</p> : null}
          <Button type="button" size="sm" className="mt-3" onClick={createReplyGrant} disabled={!currentCandidate || selectedHasActiveGrant || !confirmation || createReply.isPending || currentCandidate.health?.reply_execution_ready !== true || currentCandidate.health?.artifact_delivery_ready !== true}>
            {createReply.isPending ? <LoaderCircle className="size-3.5 animate-spin" /> : <CheckCircle2 className="size-3.5" />}
            {createReply.isPending ? '授权中…' : selectedHasActiveGrant ? '已有原会话授权' : createReply.isError ? '重试同一授权' : '授权原会话并发送激活'}
          </Button>
        </div>
      ) : !candidatesQuery.installations.isLoading && !candidatesQuery.installations.isError ? <p className="mt-3 rounded-md border border-dashed p-3 text-xs text-muted-foreground">当前 Workspace 没有可用的本人绑定飞书连接。</p> : null}

      {activeGrants.length === 0 && !candidates.length ? null : <p className="mt-3 text-[11px] leading-4 text-muted-foreground">连接、绑定和报告来源会在服务端再次校验；页面不会创建新会话，也不会把 Studio Workspace 当作 connector Workspace。</p>}
    </PanelShell>
  )
}

function PanelShell({ children }: { children: ReactNode }) {
  return <section className="mt-4 rounded-lg border bg-muted/10 p-3" aria-labelledby="connector-reply-grant-title">{children}</section>
}

function UnavailablePanel({ message }: { message: string }) {
  return <PanelShell><h3 id="connector-reply-grant-title" className="text-xs font-medium">飞书回复与报告领取</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{message}</p></PanelShell>
}
