import { apiClient } from './client'
import type { ApiResponse } from './types'

export type ProjectArtifactSummary = {
  id: string
  artifact_id: string
  title: string
  media_type: string
  kind: string
  content_hash: string
  workspace_id: string
  project_id: string
  workflow_id: string
  run_id: string
  session_id: string | null
  conversation_id: string | null
  source: string | null
  simulated: boolean
  created_at: string
  updated_at: string
}

export type ProjectArtifactDetail = ProjectArtifactSummary & {
  schema_version: string
  content: Record<string, unknown>
  payload: Record<string, unknown>
  provenance: Record<string, unknown>
  grounding_artifact_ids: string[]
  algorithm_version: string | null
  seed: number | null
}

export type ProjectArtifactQuery = {
  workflowId?: string | null
  runId?: string | null
  offset?: number
  limit?: number
}

function artifactBasePath(workspaceId: string, projectId: string) {
  return `/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/artifacts`
}

function artifactParams(query?: ProjectArtifactQuery) {
  return {
    ...(query?.workflowId ? { workflow_id: query.workflowId } : {}),
    ...(query?.runId ? { run_id: query.runId } : {}),
    ...(query?.offset !== undefined ? { offset: query.offset } : {}),
    ...(query?.limit !== undefined ? { limit: query.limit } : {}),
  }
}

export async function listProjectArtifacts(
  workspaceId: string,
  projectId: string,
  query?: ProjectArtifactQuery,
) {
  const response = await apiClient.get<ApiResponse<ProjectArtifactSummary[]>>(
    artifactBasePath(workspaceId, projectId),
    { params: artifactParams(query) },
  )
  return response.data.data
}

export async function getProjectArtifact(
  workspaceId: string,
  projectId: string,
  artifactId: string,
  query?: Pick<ProjectArtifactQuery, 'workflowId' | 'runId'>,
) {
  const response = await apiClient.get<ApiResponse<ProjectArtifactDetail>>(
    `${artifactBasePath(workspaceId, projectId)}/${encodeURIComponent(artifactId)}`,
    { params: artifactParams(query) },
  )
  return response.data.data
}

