import { useQuery } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'

export type ChatToolKind = 'read' | 'proposal'

export type ChatTool = {
  name: string
  label: string
  description: string
  kind: ChatToolKind
  available?: boolean
  requires_confirmation?: boolean
  reason?: string | null
  missing?: string[]
}

export type ChatToolCatalog = {
  version?: string
  tools: ChatTool[]
  source?: 'server' | 'fallback'
}

export type ChatToolContext = {
  project_id?: string | null
  workflow_id?: string | null
  run_id?: string | null
}

type ChatToolCatalogResponse = {
  data?: ChatToolCatalog
  success?: boolean
  message?: string
  error?: string
}

/**
 * Short-lived compatibility metadata for deployments that predate GET /chat/tools.
 * The server catalog wins as soon as that endpoint is available; this is not a
 * claim that these tools bypass the server's runtime, permission, or model checks.
 */
export const LEGACY_CHAT_TOOL_CATALOG: ChatTool[] = [
  { name: 'list_sources', label: '查看数据源', description: '读取当前工作区的数据源和启用状态。', kind: 'read' },
  { name: 'toggle_source', label: '调整数据源', description: '提出启用或停用数据源的变更提案。', kind: 'proposal' },
  { name: 'list_schedules', label: '查看调度', description: '读取定时计划和下一次执行信息。', kind: 'read' },
  { name: 'list_tasks', label: '查看任务', description: '读取最近采集任务及其状态。', kind: 'read' },
  { name: 'trigger_task', label: '触发采集', description: '提出对已启用数据源立即采集的提案。', kind: 'proposal' },
  { name: 'update_schedule', label: '调整调度', description: '提出修改 cron 或启停计划的提案。', kind: 'proposal' },
  { name: 'list_providers', label: '查看模型连接', description: '读取模型提供商、默认模型和启用状态。', kind: 'read' },
  { name: 'update_provider', label: '调整模型连接', description: '提出修改默认模型或启停连接的提案。', kind: 'proposal' },
  { name: 'list_projects', label: '查看项目', description: '读取当前工作区的 Studio 项目。', kind: 'read' },
  { name: 'list_workflows', label: '查看工作流', description: '读取项目中的工作流。', kind: 'read' },
  { name: 'get_workflow_draft', label: '查看草稿', description: '读取工作流草稿和 revision。', kind: 'read' },
  { name: 'create_project', label: '创建项目', description: '提出创建项目、主工作流和初始草稿的提案。', kind: 'proposal' },
  { name: 'update_workflow_draft', label: '修改草稿', description: '提出按当前 revision 更新工作流草稿的提案。', kind: 'proposal' },
]

export const CHAT_TOOLS_QUERY_KEY = ['chat-tool-catalog'] as const

export async function fetchChatToolCatalog(workspaceId: string, context: ChatToolContext = {}): Promise<ChatToolCatalog> {
  const response = await apiClient.get<ChatToolCatalogResponse>('/chat/tools', {
    params: { workspace_id: workspaceId, ...context },
  })
  const catalog = response.data.data
  if (!catalog || !Array.isArray(catalog.tools)) {
    throw new Error(response.data.message || response.data.error || '聊天工具目录格式无效')
  }
  return { ...catalog, source: 'server' }
}

export function useChatToolCatalog(workspaceId?: string | null, context: ChatToolContext = {}) {
  const query = useQuery({
    queryKey: [...CHAT_TOOLS_QUERY_KEY, workspaceId, context],
    queryFn: () => fetchChatToolCatalog(workspaceId!, context),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
    retry: 0,
  })
  const legacyEndpoint = query.error instanceof Error && 'status' in query.error && query.error.status === 404

  return {
    catalog: query.data ?? {
      version: 'legacy-chat-contract',
      tools: legacyEndpoint ? LEGACY_CHAT_TOOL_CATALOG : [],
      source: 'fallback' as const,
    },
    loading: query.isLoading,
    error: query.error instanceof Error ? query.error.message : null,
    serverCatalogAvailable: Boolean(query.data),
    legacyEndpoint,
    retry: query.refetch,
  }
}
