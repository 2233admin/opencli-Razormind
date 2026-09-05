"use client"

import { useQuery } from "@tanstack/react-query"

import { getApiAuthHeaders } from "@/lib/api/auth-headers"
import type {
  WorkflowCapability,
  WorkflowNodeKind,
} from "@/lib/workflow/schema"

export type BackendNodeCapabilityCategoryId =
  | "input"
  | "trigger"
  | "ai"
  | "knowledge"
  | "logic"
  | "transform"
  | "flow"
  | "tool"
  | "agent"
  | "human"
  | "output"
  | "plugin"
  | "compatibility"

export type BackendNodeCapabilityReadiness =
  | "runnable"
  | "blocked"
  | "composed"
  | "plugin_required"

export type BackendNodeCapabilityOrigin =
  | "native"
  | "composite"
  | "plugin"
  | "compatibility"

export type BackendNodeCapabilityPort = {
  name: string
  type: string
  required: boolean
}

export type BackendNodeCapabilityParameter = {
  name: string
  label: string
  type: string
  required: boolean
  default?: unknown
  options: unknown[]
}

export type BackendNodeCapabilityDefinition = {
  id: string
  installationId?: string | null
  pluginVersion?: string | null
  pluginCapabilityId?: string | null
  label: string
  description: string
  category: BackendNodeCapabilityCategoryId
  origin: BackendNodeCapabilityOrigin
  provider: string
  source: string
  readiness: BackendNodeCapabilityReadiness
  runtimeBinding?: string | null
  kind: WorkflowNodeKind
  capability: WorkflowCapability
  icon: string
  inputPorts: BackendNodeCapabilityPort[]
  outputPorts: BackendNodeCapabilityPort[]
  parameters: BackendNodeCapabilityParameter[]
  difyNodeTypes: string[]
  missing: string[]
}

export type BackendNodeCapabilityCategory = {
  id: BackendNodeCapabilityCategoryId
  label: string
  count: number
}

export type BackendNodeCapabilityCatalog = {
  version: "opencli.node-capabilities.v1" | string
  authority: "backend" | string
  nodes: BackendNodeCapabilityDefinition[]
  categories: BackendNodeCapabilityCategory[]
  summary: {
    total: number
    byReadiness: Partial<Record<BackendNodeCapabilityReadiness, number>>
    byOrigin: Partial<Record<BackendNodeCapabilityOrigin, number>>
  }
}

type ApiResponse<T> = {
  success?: boolean
  data?: T | null
  error?: string | null
  message?: string | null
  detail?: { code?: string; message?: string } | string
}

export const NODE_CAPABILITY_CATALOG_QUERY_KEY = ["node-capability-catalog"] as const

export async function fetchBackendNodeCapabilityCatalog(
  workspaceId?: string | null,
): Promise<BackendNodeCapabilityCatalog> {
  const path = workspaceId
    ? `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/plugins/capabilities`
    : "/api/v1/plugins/capabilities"
  const response = await fetch(path, {
    cache: "no-store",
    headers: getApiAuthHeaders(),
  })
  const payload = (await response.json().catch(() => null)) as
    | ApiResponse<BackendNodeCapabilityCatalog>
    | null
  if (!response.ok || !payload?.success || !payload.data) {
    throw new Error(readApiError(payload, `节点能力目录读取失败 (${response.status})`))
  }
  return payload.data
}

export function useBackendNodeCapabilityCatalog(
  enabled = true,
  workspaceId?: string | null,
) {
  const query = useQuery({
    queryKey: [...NODE_CAPABILITY_CATALOG_QUERY_KEY, workspaceId ?? "global"],
    queryFn: () => fetchBackendNodeCapabilityCatalog(workspaceId),
    enabled: enabled && (workspaceId === undefined || Boolean(workspaceId)),
    staleTime: 15_000,
    retry: 1,
  })
  return {
    catalog: query.data ?? null,
    loading: query.isLoading,
    error: query.error instanceof Error ? query.error.message : null,
    refetch: query.refetch,
  }
}

export function nodeCapabilityReadinessLabel(
  readiness: BackendNodeCapabilityReadiness,
): string {
  if (readiness === "runnable") return "可运行"
  if (readiness === "composed") return "组合能力"
  if (readiness === "plugin_required") return "需要插件"
  return "受阻"
}

export function nodeCapabilityReadinessTone(
  readiness: BackendNodeCapabilityReadiness,
): string {
  if (readiness === "runnable") return "border-success/35 bg-success/10 text-success"
  if (readiness === "composed") return "border-primary-400/35 bg-primary-500/10 text-primary-400"
  return "border-warning/35 bg-warning/10 text-warning"
}

function readApiError<T>(payload: ApiResponse<T> | null, fallback: string): string {
  if (typeof payload?.detail === "string") return payload.detail
  if (payload?.detail && typeof payload.detail === "object") {
    return payload.detail.message ?? payload.detail.code ?? fallback
  }
  return payload?.message ?? payload?.error ?? fallback
}
