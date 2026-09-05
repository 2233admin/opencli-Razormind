import { apiClient } from './client'
import type { ApiResponse } from './types'

export type BrowserSpaceStatus = 'idle' | 'running' | 'closed' | 'error'
export type BrowserSpaceTaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type BrowserSpaceEventKind = 'queued' | 'started' | 'completed' | 'failed' | 'cancel_requested' | 'cancelled'
export type BrowserSpaceOwnerType = 'operator' | 'runtime_agent'

export interface BrowserSpace {
  id: string
  workspace_id: string
  browser_instance_id: string
  binding_id: string | null
  owner_type: BrowserSpaceOwnerType
  owner_id: string
  status: BrowserSpaceStatus
  granted_capabilities: string[]
  revision: number
  last_error_code: string | null
  created_at: string
  updated_at: string
}

export interface BrowserSpaceTask {
  space_id: string
  task_id: string
  operation_id: string
  capability?: string
  status: BrowserSpaceTaskStatus
  result: unknown
  error: string | null
}

export interface BrowserSpaceEvent {
  id: string
  space_id: string
  task_id: string | null
  sequence: number
  kind: BrowserSpaceEventKind
  payload: Record<string, unknown>
  created_at: string
}

export interface BrowserSpaceList {
  spaces: BrowserSpace[]
  total: number
  limit: number
}

export type BrowserSpaceDetail = BrowserSpace & {
  active_task?: BrowserSpaceTask | null
  latest_task?: BrowserSpaceTask | null
}

export interface BrowserSpaceTaskRequest {
  request_id: string
  capability: string
  args: Record<string, unknown>
  timeout_seconds: number
}

export interface BrowserSpaceCreateRequest {
  browser_instance_id: string
  binding_id?: string
  owner_type: BrowserSpaceOwnerType
  owner_id: string
  granted_capabilities: string[]
}

export interface BrowserSpaceTaskResponse {
  space_id: string
  task_id: string
  operation_id: string
  capability?: string
  status: BrowserSpaceTaskStatus
  result: unknown
  error: string | null
}

export interface BrowserSpaceEvents {
  events: BrowserSpaceEvent[]
}

const spacePath = (workspaceId: string, spaceId?: string) => {
  const base = `/workspaces/${encodeURIComponent(workspaceId)}/browser-spaces`
  return spaceId ? `${base}/${encodeURIComponent(spaceId)}` : base
}

export const listBrowserSpaceInstances = (workspaceId: string) =>
  apiClient
    .get<ApiResponse<{ id: string; capabilities: string[] }[]>>(`${spacePath(workspaceId)}/instances`)
    .then((response) => response.data.data)

export const listBrowserSpaces = (workspaceId: string, limit = 20) =>
  apiClient
    .get<ApiResponse<BrowserSpace[]>>(spacePath(workspaceId), { params: { limit } })
    .then((response) => ({
      spaces: response.data.data,
      total: response.data.data.length,
      limit,
    }))

export const createBrowserSpace = (workspaceId: string, data: BrowserSpaceCreateRequest) =>
  apiClient
    .post<ApiResponse<BrowserSpace>>(spacePath(workspaceId), data)
    .then((response) => response.data.data)

export const getBrowserSpace = (workspaceId: string, spaceId: string) =>
  apiClient
    .get<ApiResponse<BrowserSpaceDetail>>(spacePath(workspaceId, spaceId))
    .then((response) => response.data.data)

export const submitBrowserSpaceTask = (
  workspaceId: string,
  spaceId: string,
  data: BrowserSpaceTaskRequest,
) =>
  apiClient
    .post<ApiResponse<BrowserSpaceTaskResponse>>(`${spacePath(workspaceId, spaceId)}/tasks`, data)
    .then((response) => response.data.data)

export const cancelBrowserSpace = (workspaceId: string, spaceId: string) =>
  apiClient
    .post<ApiResponse<BrowserSpaceTaskResponse>>(`${spacePath(workspaceId, spaceId)}/cancel`)
    .then((response) => response.data.data)

export const closeBrowserSpace = (workspaceId: string, spaceId: string) =>
  apiClient
    .post<ApiResponse<BrowserSpace>>( `${spacePath(workspaceId, spaceId)}/close`)
    .then((response) => response.data.data)

export const listBrowserSpaceEvents = (
  workspaceId: string,
  spaceId: string,
  afterSequence = 0,
  limit = 100,
) => {
  // Drain bounded replay pages so polling continues beyond the first 100 events.
  const read = async () => {
    const events: BrowserSpaceEvent[] = []
    let cursor = afterSequence
    while (true) {
      const response = await apiClient.get<ApiResponse<BrowserSpaceEvent[]>>(
        `${spacePath(workspaceId, spaceId)}/events`,
        { params: { after_sequence: cursor, limit } },
      )
      const page = response.data.data
      events.push(...page)
      if (page.length < limit || page[page.length - 1].sequence <= cursor) break
      cursor = page[page.length - 1].sequence
    }
    return { events }
  }
  return read()
}
