'use client'

import Link from 'next/link'
import { ArrowRight, Boxes, CheckCircle2, CircleAlert, ExternalLink, Wrench } from 'lucide-react'
import { useSearchParams } from 'next/navigation'

import { useGovernedWorkspaces } from '@/lib/api/hooks'
import { useChatToolCatalog } from '@/lib/api/chat-tools'
import {
  nodeCapabilityReadinessLabel,
  nodeCapabilityReadinessTone,
  useBackendNodeCapabilityCatalog,
} from '@/lib/plugins/backend-node-capabilities'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

function useWorkspaceFromUrl() {
  const params = useSearchParams()
  return params.get('workspace')
}

function CapabilityStatus({ readiness }: { readiness: 'runnable' | 'blocked' | 'composed' | 'plugin_required' }) {
  return (
    <span className={cn('rounded-full border px-2 py-0.5 text-[11px]', nodeCapabilityReadinessTone(readiness))}>
      {nodeCapabilityReadinessLabel(readiness)}
    </span>
  )
}

export function AgentToolReference({ compact = false }: { compact?: boolean }) {
  const workspaceParam = useWorkspaceFromUrl()
  const workspaces = useGovernedWorkspaces()
  const workspaceId = workspaceParam ?? (workspaces.data?.length === 1 ? workspaces.data[0].id : null)
  const { catalog: chatCatalog, loading: chatLoading, error: chatError, serverCatalogAvailable } = useChatToolCatalog(workspaceId)
  const capabilities = useBackendNodeCapabilityCatalog(Boolean(workspaceId), workspaceId)
  const nodes = capabilities.catalog?.nodes ?? []
  const visibleNodes = compact ? nodes.slice(0, 4) : nodes.slice(0, 8)

  return (
    <div className="space-y-4" aria-label="Agent 工具目录">
      <Card size="sm">
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <p className="eyebrow-mono">Chat tools</p>
            <CardTitle className="mt-1 flex items-center gap-2 text-base">
              <Wrench className="size-4 text-primary" aria-hidden />
              对话可调用工具
            </CardTitle>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              仅列出对话服务端注册的工具；权限、模型和连接状态仍以实际请求结果为准。
            </p>
          </div>
          <Badge variant={serverCatalogAvailable ? 'outline' : 'secondary'}>
            {chatLoading ? '读取中' : `${chatCatalog.tools.length} 项`}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-2">
          {!serverCatalogAvailable ? (
            <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-muted-foreground">
              当前服务还未返回工具目录，下面是已知聊天契约摘要；不会因此扩大实际调用范围。
            </div>
          ) : null}
          {chatError && !chatLoading ? (
            <p className="text-xs text-muted-foreground" role="status">服务端目录暂时不可用，已显示兼容摘要。</p>
          ) : null}
          <div className={cn('grid gap-2', compact ? 'sm:grid-cols-2' : 'sm:grid-cols-2')}>
            {chatCatalog.tools.map((tool) => (
              <div key={tool.name} className={cn('rounded-md border border-border/70 bg-background/60 p-2.5', tool.available === false && 'opacity-70')}>
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium">{tool.label}</span>
                  <span className={cn(
                    'shrink-0 rounded-full px-1.5 py-0.5 text-[10px]',
                    tool.available === false
                      ? 'bg-muted text-muted-foreground'
                      : tool.kind === 'proposal' ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success',
                  )}>
                    {tool.available === false ? '暂不可用' : tool.requires_confirmation || tool.kind === 'proposal' ? '需确认' : '只读'}
                  </span>
                </div>
                {!compact ? <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{tool.description}{tool.reason ? `（${tool.reason}）` : ''}</p> : null}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/providers" />}>
              查看模型连接
              <ExternalLink aria-hidden />
            </Button>
            <Button size="sm" variant="ghost" nativeButton={false} render={<Link href={workspaceId ? `/studio/new?workspace=${encodeURIComponent(workspaceId)}` : '/studio/new'} />}>
              创建项目
              <ArrowRight aria-hidden />
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card size="sm">
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <p className="eyebrow-mono">Workflow / plugins</p>
            <CardTitle className="mt-1 flex items-center gap-2 text-base">
              <Boxes className="size-4 text-primary" aria-hidden />
              工作流与插件能力
            </CardTitle>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              这是工作流能力目录，不代表每个节点都能在当前对话中直接调用。
            </p>
          </div>
          <Badge variant="outline">{capabilities.catalog?.summary.total ?? 0} 项</Badge>
        </CardHeader>
        <CardContent className="space-y-2">
          {!workspaceId ? (
            <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-muted-foreground" role="status">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              请选择一个已授权工作区后读取工作流能力。
            </div>
          ) : null}
          {capabilities.loading ? <p className="text-xs text-muted-foreground" role="status">正在读取当前 Workspace 的能力目录…</p> : null}
          {capabilities.error ? <p className="text-xs text-destructive" role="alert">能力目录读取失败：{capabilities.error}</p> : null}
          {!capabilities.loading && !capabilities.error && workspaceId && visibleNodes.length === 0 ? (
            <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">当前 Workspace 暂无可展示的工作流能力。</div>
          ) : null}
          {visibleNodes.map((node) => (
            <div key={node.id} className="rounded-md border border-border/70 bg-background/60 p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium">{node.label}</div>
                  <div className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">{node.id}</div>
                </div>
                <CapabilityStatus readiness={node.readiness} />
              </div>
              {!compact ? <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{node.description}</p> : null}
              {node.missing.length ? <p className="mt-1 text-[10px] text-warning">缺少：{node.missing.slice(0, 2).join('、')}</p> : null}
            </div>
          ))}
          {nodes.length > visibleNodes.length ? <p className="text-[11px] text-muted-foreground">还有 {nodes.length - visibleNodes.length} 项能力，前往目录查看完整状态。</p> : null}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button size="sm" variant="outline" nativeButton={false} render={<Link href={workspaceId ? `/plugins?tab=capabilities&type=tool&workspace=${encodeURIComponent(workspaceId)}` : '/plugins?tab=capabilities&type=tool'} />}>
              查看完整能力目录
              <ArrowRight aria-hidden />
            </Button>
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <CheckCircle2 className="size-3.5" aria-hidden />
              状态来自当前目录
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
