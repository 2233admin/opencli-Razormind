"use client"

import { useQuery } from "@tanstack/react-query"

import { getApiAuthHeaders } from "@/lib/api/auth-headers"

export type BackendPluginBlocker = {
  code: string
  message: string
}

export type BackendPluginCapability = {
  id: string
  family: "tool" | "model" | "datasource" | "trigger" | "agent_strategy" | "endpoint"
  key: string
  label: string
  sourcePath?: string | null
  status: "READY" | "BLOCKED"
  runtimeAdapterId?: string | null
  blockers: BackendPluginBlocker[]
  flowCapability: boolean
}

export type BackendPluginNodeDefinition = {
  id: string
  label: string
  family: string
  status: "READY" | "BLOCKED"
  locked: boolean
  lockReason?: string | null
  installationId: string
  providerKey: string
  pluginVersion: string
  capabilityId: string
}

export type BackendPluginInstallation = {
  id: string
  workspaceId?: string | null
  enabled: boolean
  grantedPermissions: string[]
  providerKey: string
  name: string
  author: string
  version: string
  sourceKind: "manifest" | "difypkg" | "bundled"
  sourceDigest: string
  manifestSpecVersion: string
  signatureState: "unsigned" | "present_unverified" | "bundled"
  labels: Record<string, string>
  descriptions: Record<string, string>
  icon?: string | null
  pluginTypes: string[]
  manifest: Record<string, unknown>
  capabilities: BackendPluginCapability[]
  permissions: Record<string, unknown>
  runtimeStatus: "READY" | "BLOCKED"
  blockers: BackendPluginBlocker[]
  nodeDefinitions: BackendPluginNodeDefinition[]
  bundled: boolean
  installedAt: string
  updatedAt: string
}

type ApiResponse<T> = {
  success?: boolean
  data?: T | null
  error?: string | null
  detail?: { code?: string; message?: string } | string
}
export const PLUGIN_CATALOG_QUERY_KEY = ["plugin-installations"] as const

export async function fetchPluginInstallations(
  workspaceId?: string | null,
): Promise<BackendPluginInstallation[]> {
  const response = await fetch(pluginPath(workspaceId), {
    cache: "no-store",
    headers: getApiAuthHeaders(),
  })
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<BackendPluginInstallation[]>
    | null
  if (!response.ok || !payload?.success || !payload.data) {
    throw new Error(readApiError(payload, `插件目录读取失败 (${response.status})`))
  }
  return payload.data
}

export async function importDifyPluginPackage(
  workspaceId: string,
  file: File,
): Promise<BackendPluginInstallation> {
  const body = new FormData()
  body.append("file", file)
  const response = await fetch(`${pluginPath(workspaceId)}/import/dify`, {
    method: "POST",
    headers: getApiAuthHeaders(),
    body,
  })
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<BackendPluginInstallation>
    | null
  if (!response.ok || !payload?.success || !payload.data) {
    throw new Error(readApiError(payload, `Dify 插件导入失败 (${response.status})`))
  }
  return payload.data
}

export async function updatePluginInstallation(
  workspaceId: string,
  installationId: string,
  update: { enabled?: boolean; grantedPermissions?: string[] },
): Promise<BackendPluginInstallation> {
  const response = await fetch(`${pluginPath(workspaceId)}/${installationId}`, {
    method: "PATCH",
    headers: { ...getApiAuthHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<BackendPluginInstallation>
    | null
  if (!response.ok || !payload?.success || !payload.data) {
    throw new Error(readApiError(payload, `插件状态更新失败 (${response.status})`))
  }
  return payload.data
}

export function useBackendPluginCatalog(
  workspaceId?: string | null,
  enabled = true,
) {
  const query = useQuery({
    queryKey: [...PLUGIN_CATALOG_QUERY_KEY, workspaceId ?? "global"],
    queryFn: () => fetchPluginInstallations(workspaceId),
    enabled: enabled && (workspaceId === undefined || Boolean(workspaceId)),
    staleTime: 15_000,
    retry: 1,
  })
  return {
    installations: query.data ?? null,
    loading: query.isLoading,
    error: query.error instanceof Error ? query.error.message : null,
    refetch: query.refetch,
  }
}

function pluginPath(workspaceId?: string | null): string {
  return workspaceId
    ? `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/plugins`
    : "/api/v1/plugins"
}
function readApiError<T>(payload: ApiResponse<T> | null, fallback: string): string {
  if (typeof payload?.detail === "string") return payload.detail
  if (payload?.detail && typeof payload.detail === "object") {
    return payload.detail.message ?? payload.detail.code ?? fallback
  }
  return payload?.error ?? fallback
}
