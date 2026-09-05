"use client";

import { useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { useChromePool, useRemoveChromeInstance, useUpdateChromeEndpointMode, useWsAgentStatus } from '@/lib/api/hooks'
import type { ChromeEndpoint } from '@/lib/api/types'
import { ChromeInstanceFormDialog } from '@/components/browsers/chrome-instance-form-dialog'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { StatusBadge } from '@/components/shell/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const MODE_LABEL: Record<string, string> = { bridge: "Bridge", cdp: "CDP" };
const PROFILE_KIND_LABEL: Record<string, string> = {
  anonymous: "匿名",
  authenticated: "持久化登录环境",
};

/**
 * agent-N Docker container index for this endpoint, or null when it isn't a
 * locally Docker-managed container this UI can stop/remove (e.g. a raw CDP
 * endpoint registered via /browsers/cdp-endpoint, or a remote agent
 * registered via /browsers/agents/register or the WS channel). Mirrors the
 * hostname convention backend/api/v1/workers.py's _novnc_port already
 * relies on: agent → instance 1, agent-N → instance N.
 */
function agentContainerIndex(url: string): number | null {
  try {
    const hostname = new URL(url).hostname;
    const match = /^agent(?:-(\d+))?$/.exec(hostname);
    if (!match) return null;
    return match[1] ? Number(match[1]) : 1;
  } catch {
    return null;
  }
}

export function ChromeInstancesPanel() {
  const { data, isLoading, isError, error } = useChromePool()
  const endpoints = data?.endpoints ?? []
  const [confirmRemoveUrl, setConfirmRemoveUrl] = useState<string | null>(null)
  const [confirmModeUrl, setConfirmModeUrl] = useState<string | null>(null)
  const removeMutation = useRemoveChromeInstance()
  const modeMutation = useUpdateChromeEndpointMode()
  const wsStatus = useWsAgentStatus()

  const handleRemove = (endpoint: ChromeEndpoint) => {
    const n = agentContainerIndex(endpoint.url);
    if (n === null || n < 2) return;
    if (confirmRemoveUrl !== endpoint.url) {
      setConfirmRemoveUrl(endpoint.url);
      return;
    }
    removeMutation.mutate(n, {
      onSuccess: () => {
        toast.success(`已移除 agent-${n}`);
        setConfirmRemoveUrl(null);
      },
      onError: (cause: Error) => toast.error(cause.message),
    });
  };

  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/20 py-4">
        <CardTitle className="text-base">Chrome 实例</CardTitle>
        <CardDescription>
          本机 Docker 采集池，可选路由到远程 Agent。移除操作仅支持 Docker 管理的 agent-2 及以后实例。
          {wsStatus.isLoading ? ' WebSocket Agent 状态同步中。' : ` 当前已连接 ${wsStatus.data?.connected?.length ?? 0} 个 WebSocket Agent。`}
        </CardDescription>
        <CardAction>
          <ChromeInstanceFormDialog
            mode="create"
            triggerLabel="添加实例"
            triggerIcon={<Plus className="size-4" />}
          />
        </CardAction>
      </CardHeader>
      <CardContent className="p-4">
        {isLoading ? (
          <LoadingState />
        ) : isError ? (
          <ErrorState message={(error as Error)?.message} hint={BACKEND_HINT} />
        ) : endpoints.length === 0 ? (
          <EmptyState
            title="暂无 Chrome 实例"
            description="添加一个实例后即可用于浏览器采集。"
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>地址</TableHead>
                <TableHead>模式</TableHead>
                <TableHead>Agent 路由</TableHead>
                <TableHead>Profile 类型</TableHead>
                <TableHead>运行时</TableHead>
                <TableHead>可用</TableHead>
                <TableHead>容器状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {endpoints.map((endpoint) => {
                const n = agentContainerIndex(endpoint.url)
                const removable = n !== null && n >= 2
                const confirming = confirmRemoveUrl === endpoint.url
                const confirmingMode = confirmModeUrl === endpoint.url
                const nextMode = endpoint.mode === 'bridge' ? 'cdp' : 'bridge'
                return (
                  <TableRow key={endpoint.url}>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      <div className="flex flex-col">
                        <span>{endpoint.url}</span>
                        <span className="text-muted-foreground/70">
                          noVNC :{endpoint.novnc_port}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="secondary">{MODE_LABEL[endpoint.mode] ?? endpoint.mode}</Badge>
                        <Button
                          size="xs"
                          variant={confirmingMode ? 'outline' : 'ghost'}
                          disabled={modeMutation.isPending}
                          onClick={() => {
                            if (!confirmingMode) {
                              setConfirmModeUrl(endpoint.url)
                              return
                            }
                            modeMutation.mutate(
                              { endpoint: endpoint.url, mode: nextMode },
                              {
                                onSuccess: () => {
                                  toast.success(`${endpoint.url} 已切换为 ${MODE_LABEL[nextMode]}`)
                                  setConfirmModeUrl(null)
                                },
                                onError: (cause: Error) => toast.error(cause.message),
                              },
                            )
                          }}
                          title={`切换为 ${MODE_LABEL[nextMode]}`}
                        >
                          {confirmingMode ? `确认 ${MODE_LABEL[nextMode]}` : `切换为 ${MODE_LABEL[nextMode]}`}
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      {endpoint.agent_url ? (
                        <span className="font-mono text-muted-foreground">
                          {endpoint.agent_url} ·{" "}
                          {endpoint.agent_protocol?.toUpperCase()}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">本地</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <Badge variant="outline" className="w-fit">
                          {PROFILE_KIND_LABEL[endpoint.profile_kind ?? ""] ??
                            endpoint.profile_kind ??
                            "未知"}
                        </Badge>
                        <span className="font-mono text-3xs text-muted-foreground">
                          {endpoint.profile_name ?? "未命名"} ·{" "}
                          {endpoint.resource_class ?? "standard"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex min-w-40 flex-col gap-1">
                        <Badge
                          variant={
                            endpoint.runtime_status === "READY" ||
                            endpoint.runtime_status === "LEGACY"
                              ? "secondary"
                              : "destructive"
                          }
                          className="w-fit"
                        >
                          {endpoint.runtime_status ?? "LEGACY"}
                        </Badge>
                        {endpoint.runtime_bundle_name &&
                        endpoint.runtime_bundle_version ? (
                          <span className="font-mono text-3xs text-muted-foreground">
                            期望 {endpoint.runtime_bundle_name}@
                            {endpoint.runtime_bundle_version}
                          </span>
                        ) : (
                          <span className="text-3xs text-muted-foreground">
                            未锁定 Bundle（旧 Slot）
                          </span>
                        )}
                        {endpoint.loaded_bundle_name &&
                        endpoint.loaded_bundle_version ? (
                          <span className="font-mono text-3xs text-muted-foreground">
                            已载入 {endpoint.loaded_bundle_name}@
                            {endpoint.loaded_bundle_version}
                          </span>
                        ) : null}
                        {endpoint.runtime_diagnostics?.[0] ? (
                          <span className="line-clamp-2 text-3xs text-destructive">
                            {endpoint.runtime_diagnostics[0]}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        status={endpoint.available ? "online" : "offline"}
                      />
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={endpoint.container_status} />
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <ChromeInstanceFormDialog
                          mode="edit"
                          instance={endpoint}
                          triggerLabel="编辑"
                          triggerIcon={<Pencil className="size-3" />}
                          triggerVariant="ghost"
                          triggerSize="xs"
                        />
                        <Button
                          size="xs"
                          variant={confirming ? "destructive" : "ghost"}
                          disabled={!removable || removeMutation.isPending}
                          onClick={() => handleRemove(endpoint)}
                          title={
                            removable
                              ? undefined
                              : "agent-1 由 docker-compose 管理；非 Docker 管理的实例（CDP 直连 / 远程注册的 Agent）暂不支持从此处移除"
                          }
                          className="gap-1"
                        >
                          <Trash2 className="size-3" />
                          {confirming ? "确认移除" : "移除"}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
