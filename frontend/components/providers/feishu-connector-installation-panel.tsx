'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { Copy, KeyRound, Loader2, Link2, Plus, RefreshCw, ShieldAlert, Unlink } from 'lucide-react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'

import { useAuth } from '@/components/auth/auth-provider'
import { useGovernedWorkspaces } from '@/lib/api/hooks'
import {
  useConnectorInstallationHealth,
  useConnectorInstallations,
  useCreateConnectorBindingChallenge,
  useCreateConnectorInstallation,
  useMyConnectorBinding,
  useRevokeConnectorBinding,
  useConnectorWorkspaceMemberRole,
  useUpdateConnectorInstallation,
  type ConnectorBindingChallenge,
  type ConnectorInstallation,
} from '@/lib/api/connector-installations'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type InstallationFormProps = {
  workspaceId: string
  installation?: ConnectorInstallation
}

function InstallationForm({ workspaceId, installation }: InstallationFormProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(installation?.name ?? '飞书消息连接')
  const [appId, setAppId] = useState(installation?.app_id ?? '')
  const [tenantKey, setTenantKey] = useState(installation?.tenant_key ?? '')
  const [appSecret, setAppSecret] = useState('')
  const [encryptKey, setEncryptKey] = useState('')
  const [verificationToken, setVerificationToken] = useState('')
  const [enabled, setEnabled] = useState(installation ? installation.status === 'active' : true)
  const create = useCreateConnectorInstallation()
  const update = useUpdateConnectorInstallation()
  const pending = create.isPending || update.isPending
  const editing = Boolean(installation)

  useEffect(() => {
    if (!open) return
    setName(installation?.name ?? '飞书消息连接')
    setAppId(installation?.app_id ?? '')
    setTenantKey(installation?.tenant_key ?? '')
    setAppSecret('')
    setEncryptKey('')
    setVerificationToken('')
    setEnabled(installation ? installation.status === 'active' : true)
  }, [installation, open])

  function clearSecrets() {
    setAppSecret('')
    setEncryptKey('')
    setVerificationToken('')
  }

  function closeAfterSuccess() {
    clearSecrets()
    setOpen(false)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    if (installation) {
      const input = {
        name: trimmedName,
        enabled,
        ...(appSecret ? { app_secret: appSecret } : {}),
        ...(encryptKey ? { encrypt_key: encryptKey } : {}),
        ...(verificationToken ? { verification_token: verificationToken } : {}),
      }
      update.mutate(
        { workspaceId, installationId: installation.installation_public_id, input },
        {
          onSuccess: () => {
            toast.success('飞书消息连接已更新')
            closeAfterSuccess()
          },
          onError: (error: Error) => toast.error(error.message),
        },
      )
      return
    }

    create.mutate(
      {
        workspaceId,
        input: {
          name: trimmedName,
          app_id: appId.trim(),
          tenant_key: tenantKey.trim(),
          app_secret: appSecret,
          encrypt_key: encryptKey,
          verification_token: verificationToken,
        },
      },
      {
        onSuccess: () => {
          toast.success('飞书消息连接已安装')
          closeAfterSuccess()
        },
        onError: (error: Error) => toast.error(error.message),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { setOpen(nextOpen); if (!nextOpen) clearSecrets() }}>
      <DialogTrigger render={<Button size="sm" variant={editing ? 'outline' : 'default'} />}>
        {editing ? <KeyRound className="size-3.5" /> : <Plus className="size-4" />}
        {editing ? '编辑安装' : '安装飞书消息连接'}
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <form className="space-y-4" onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{editing ? '编辑飞书消息连接' : '安装飞书消息连接'}</DialogTitle>
            <DialogDescription>
              保存后不会再次显示秘密；编辑时秘密留空则保留现有值。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="feishu-connector-name">名称</Label>
              <Input id="feishu-connector-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={255} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="feishu-connector-app-id">App ID</Label>
              <Input id="feishu-connector-app-id" value={appId} onChange={(event) => setAppId(event.target.value)} disabled={editing} required={!editing} maxLength={255} placeholder="cli_xxx" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="feishu-connector-tenant-key">Tenant Key</Label>
              <Input id="feishu-connector-tenant-key" value={tenantKey} onChange={(event) => setTenantKey(event.target.value)} disabled={editing} required={!editing} maxLength={255} placeholder="tenant_xxx" />
            </div>
            <SecretInput id="feishu-connector-app-secret" label="App Secret" value={appSecret} onChange={setAppSecret} required={!editing} placeholder={editing ? '留空则保留现有密钥' : 'App Secret'} />
            <SecretInput id="feishu-connector-encrypt-key" label="Encrypt Key" value={encryptKey} onChange={setEncryptKey} required={!editing} placeholder={editing ? '留空则保留现有密钥' : 'Encrypt Key'} />
            <div className="space-y-2 sm:col-span-2">
              <SecretInput id="feishu-connector-verification-token" label="Verification Token" value={verificationToken} onChange={setVerificationToken} required={!editing} placeholder={editing ? '留空则保留现有令牌' : 'Verification Token'} />
            </div>
          </div>

          {editing ? (
            <label className="flex items-center justify-between rounded-md border p-3 text-sm">
              <span>
                <span className="block font-medium">启用安装</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">停用后不能生成新的绑定指令。</span>
              </span>
              <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="size-4 accent-primary" />
            </label>
          ) : null}

          <DialogFooter>
            <Button type="submit" disabled={pending}>
              {pending ? <Loader2 className="size-4 animate-spin" /> : null}
              {pending ? '保存中…' : editing ? '保存更改' : '安装连接'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SecretInput({
  id,
  label,
  value,
  onChange,
  required,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  required: boolean
  placeholder: string
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="password" value={value} onChange={(event) => onChange(event.target.value)} required={required} placeholder={placeholder} autoComplete="new-password" />
    </div>
  )
}

const CONNECTOR_STATUS_META = {
  active: { label: '已启用', variant: 'secondary' },
  enabled: { label: '已启用', variant: 'secondary' },
  healthy: { label: '健康', variant: 'secondary' },
  disabled: { label: '已停用', variant: 'outline' },
  blocked: { label: '配置不可用', variant: 'destructive' },
  revoked: { label: '已撤销', variant: 'destructive' },
  degraded: { label: '需处理', variant: 'outline' },
  failed: { label: '不可用', variant: 'destructive' },
  error: { label: '不可用', variant: 'destructive' },
} as const

function ConnectorStatusBadge({ status }: { status?: string | null }) {
  const normalized = status?.trim().toLowerCase() || 'unknown'
  const meta = CONNECTOR_STATUS_META[normalized as keyof typeof CONNECTOR_STATUS_META]
  const label = meta?.label ?? (status?.trim() ? `未知状态（${status.trim()}）` : '未知状态')
  return <Badge variant={meta?.variant ?? 'outline'}>{label}</Badge>
}

function CapabilityState({ label, ready }: { label: string; ready: boolean }) {
  return (
    <span className={ready ? 'text-success' : 'text-muted-foreground'}>
      {ready ? '✓' : '—'} {label}
    </span>
  )
}

function InstallationCard({ workspaceId, installation, canManageConfiguration }: { workspaceId: string; installation: ConnectorInstallation; canManageConfiguration: boolean }) {
  const health = useConnectorInstallationHealth(workspaceId, installation.installation_public_id)
  const binding = useMyConnectorBinding(workspaceId, installation.installation_public_id)
  const challenge = useCreateConnectorBindingChallenge()
  const revoke = useRevokeConnectorBinding()
  const [latestChallenge, setLatestChallenge] = useState<ConnectorBindingChallenge | null>(null)
  const [copied, setCopied] = useState(false)
  const activeBinding = binding.data?.active ? binding.data : null
  const canCreateChallenge = installation.status === 'active' && health.data?.callback_ready === true && !challenge.isPending

  function createChallenge() {
    challenge.mutate(
      { workspaceId, installationId: installation.installation_public_id },
      {
        onSuccess: (result) => {
          setLatestChallenge(result)
          setCopied(false)
          toast.success('本人绑定指令已生成，有效期 10 分钟')
        },
        onError: (error: Error) => toast.error(error.message),
      },
    )
  }

  async function copyChallenge() {
    if (!latestChallenge) return
    try {
      await navigator.clipboard.writeText(latestChallenge.command_text)
      setCopied(true)
      toast.success('绑定指令已复制')
    } catch {
      toast.error('复制失败，请手动复制绑定指令')
    }
  }

  function revokeBinding() {
    if (!activeBinding) return
    revoke.mutate(
      { workspaceId, bindingId: activeBinding.binding_public_id },
      {
        onSuccess: () => toast.success('当前用户绑定已撤销'),
        onError: (error: Error) => toast.error(error.message),
      },
    )
  }

  return (
    <Card data-testid={`feishu-connector-installation-${installation.installation_public_id}`}>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <ShieldAlert className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <CardTitle className="truncate text-sm">{installation.name}</CardTitle>
            </div>
            <p className="mt-1 break-all font-mono text-xs text-muted-foreground">App ID {installation.app_id} · Tenant {installation.tenant_key}</p>
          </div>
          <ConnectorStatusBadge status={installation.status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="rounded-md border bg-muted/20 p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium">接收健康</span>
            {health.data ? <ConnectorStatusBadge status={health.data.status} /> : <span className="text-muted-foreground">{health.isLoading ? '读取中…' : '未知状态'}</span>}
          </div>
          <div className="mt-2 grid gap-1 text-muted-foreground sm:grid-cols-2">
            <CapabilityState label="回调配置就绪" ready={health.data?.callback_ready === true} />
            <CapabilityState label="绑定前置条件就绪" ready={health.data?.binding_ready === true} />
            <CapabilityState label="消息回复可用" ready={health.data?.reply_execution_ready === true} />
            <CapabilityState label="产物领取可用" ready={health.data?.artifact_delivery_ready === true} />
          </div>
          {health.isError ? <p role="alert" className="mt-2 text-destructive">健康状态读取失败：{health.error instanceof Error ? health.error.message : '请稍后重试。'}</p> : null}
          {health.data?.last_error_code ? <p role="alert" className="mt-2 break-words text-destructive">状态原因：{health.data.last_error_code}</p> : null}
          <p className="mt-2 text-[11px] leading-4 text-muted-foreground">消息回复与产物领取暂不可用；当前面板只配置接收、绑定和健康状态。</p>
        </div>

        <div className="rounded-md border p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Link2 className="size-3.5 text-primary" aria-hidden="true" />
              <span className="font-medium">当前用户绑定</span>
            </div>
            {binding.isLoading ? <span className="text-muted-foreground">读取中…</span> : binding.isError ? <Badge variant="destructive">读取失败</Badge> : activeBinding ? <Badge variant="secondary">已绑定</Badge> : binding.data ? <Badge variant="outline">已撤销</Badge> : <Badge variant="outline">未绑定</Badge>}
          </div>
          {activeBinding ? (
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-muted-foreground">
              <span className="font-mono">{activeBinding.binding_public_id}</span>
              <Button type="button" size="xs" variant="outline" onClick={revokeBinding} disabled={revoke.isPending}>
                {revoke.isPending ? <Loader2 className="size-3 animate-spin" /> : <Unlink className="size-3" />}
                {revoke.isPending ? '撤销中…' : '撤销本人绑定'}
              </Button>
            </div>
          ) : null}
          {!activeBinding && binding.isError ? <p role="alert" className="mt-2 text-destructive">本人绑定状态读取失败：{binding.error instanceof Error ? binding.error.message : '请稍后重试。'}</p> : null}
        </div>

        {latestChallenge ? (
          <div className="rounded-md border border-primary/30 bg-primary/5 p-3" data-testid="feishu-connector-binding-challenge">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium">本人绑定指令</p>
              <span className="text-[11px] text-muted-foreground">有效期至 {new Date(latestChallenge.expires_at).toLocaleString()}</span>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded border bg-background px-2 py-1.5 font-mono text-xs">{latestChallenge.command_text}</code>
              <Button type="button" size="xs" variant="outline" onClick={() => void copyChallenge()}>
                <Copy className="size-3" />{copied ? '已复制' : '复制'}
              </Button>
            </div>
            <p className="mt-2 text-[11px] leading-4 text-muted-foreground">请将这条一次性指令私聊发送给对应的飞书机器人。绑定完成后刷新当前用户绑定状态。</p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={createChallenge} disabled={!canCreateChallenge} title={!canCreateChallenge ? '需要启用安装并完成凭据配置' : undefined}>
            {challenge.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Link2 className="size-3.5" />}
            {challenge.isPending ? '生成中…' : '生成本人绑定指令'}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => { void health.refetch(); void binding.refetch() }} disabled={health.isFetching || binding.isFetching}>
            <RefreshCw className={health.isFetching || binding.isFetching ? 'size-3.5 animate-spin' : 'size-3.5'} />刷新状态
          </Button>
          {canManageConfiguration ? <InstallationForm workspaceId={workspaceId} installation={installation} /> : null}
        </div>
      </CardContent>
    </Card>
  )
}

export function FeishuConnectorInstallationPanel() {
  const { identity } = useAuth()
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const workspaces = useGovernedWorkspaces()
  const requestedWorkspaceId = searchParams.get('workspace')?.trim() || null
  const selectedWorkspace = workspaces.data?.find((workspace) => workspace.id === requestedWorkspaceId) ?? null
  const workspaceId = selectedWorkspace?.id ?? null
  const installations = useConnectorInstallations(workspaceId)
  const workspaceAccess = useConnectorWorkspaceMemberRole(workspaceId, identity?.subject ?? null)
  const canManageConfiguration = workspaceAccess.isSuccess && workspaceAccess.canManageConfiguration

  function selectWorkspace(nextWorkspaceId: string) {
    const params = new URLSearchParams(searchParams.toString())
    if (nextWorkspaceId) params.set('workspace', nextWorkspaceId)
    else params.delete('workspace')
    const query = params.toString()
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false })
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby="feishu-connector-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id="feishu-connector-title" className="text-base font-semibold">飞书消息连接</h2>
          <p className="mt-1 max-w-3xl text-sm leading-5 text-muted-foreground">连接飞书机器人，管理消息接收与账号绑定。</p>
        </div>
        {workspaceId && canManageConfiguration ? <InstallationForm workspaceId={workspaceId} /> : null}
      </div>

      <div className="flex flex-col gap-2 rounded-lg border bg-muted/15 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Label htmlFor="feishu-connector-workspace">配置 Workspace</Label>
          <p className="mt-1 text-xs text-muted-foreground">只显示你有访问权限的 Workspace。</p>
        </div>
        <select
          id="feishu-connector-workspace"
          aria-label="飞书消息连接 Workspace"
          value={workspaceId ?? ''}
          onChange={(event) => selectWorkspace(event.target.value)}
          disabled={workspaces.isLoading || !workspaces.data?.length}
          className="h-10 min-w-0 rounded-md border bg-background px-3 text-sm sm:min-w-64"
        >
          <option value="">选择已授权 Workspace</option>
          {workspaces.data?.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
        </select>
      </div>

      {workspaces.isLoading ? <LoadingState /> : null}
      {workspaces.isError ? <ErrorState message={(workspaces.error as Error)?.message} hint={BACKEND_HINT} /> : null}
      {workspaces.isSuccess && requestedWorkspaceId && !selectedWorkspace ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">URL 中的 Workspace 不在当前授权范围内；未读取该范围的连接。</p>
      ) : null}
      {workspaces.isSuccess && !workspaceId ? <EmptyState title="尚未选择 Workspace" description="选择一个已授权 Workspace 后，才能读取或安装飞书消息连接。" /> : null}

      {workspaceId && workspaceAccess.isLoading ? <p role="status" className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">正在读取 Workspace 配置权限…</p> : null}
      {workspaceId && workspaceAccess.isError ? <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">无法读取 Workspace 配置权限，已隐藏安装和编辑操作；你仍可查看连接并管理本人绑定。</p> : null}
      {workspaceId && workspaceAccess.isSuccess && !canManageConfiguration ? <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">当前账号可查看连接并管理本人绑定；安装或编辑连接需要 Workspace 配置权限。</p> : null}

      {workspaceId && installations.isLoading ? <LoadingState /> : null}
      {workspaceId && installations.isError ? <ErrorState message={(installations.error as Error)?.message} hint={BACKEND_HINT} /> : null}
      {workspaceId && installations.isSuccess && installations.data.length === 0 ? (
        <div className="flex flex-col gap-3">
          <EmptyState title="暂无飞书消息连接" description={canManageConfiguration ? '安装连接后，可以生成当前用户的绑定指令并查看回调健康状态。' : '当前没有可查看的飞书消息连接；安装连接需要 Workspace 配置权限。'} />
          {canManageConfiguration ? <div className="flex justify-center"><InstallationForm workspaceId={workspaceId} /></div> : null}
        </div>
      ) : null}
      {workspaceId && installations.isSuccess && installations.data.length ? (
        <div className="grid gap-3">
          {installations.data.map((installation) => <InstallationCard key={installation.installation_public_id} workspaceId={workspaceId} installation={installation} canManageConfiguration={canManageConfiguration} />)}
        </div>
      ) : null}
    </section>
  )
}
