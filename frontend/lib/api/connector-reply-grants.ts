import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiClient } from './client'
import type { ApiResponse } from './types'

export type ConnectorReplyGrantCreate = {
  request_id: string
  installation_public_id: string
  binding_public_id: string
  conversation_id: string
  expires_in_seconds?: number
}

export type ConnectorReplyGrant = {
  reply_grant_public_id: string
  installation_public_id: string
  binding_public_id: string
  workspace_id: string
  studio_workspace_id: string
  project_id: string
  workflow_id: string | null
  run_id: string | null
  conversation_id: string
  status: string
  version: number
  expires_at: string
  revoked_at: string | null
  activation_delivery_status: string | null
  created_at: string
  updated_at: string
}

export type ConnectorReplyGrantCreated = ConnectorReplyGrant & {
  created: boolean
}

export type ConnectorArtifactGrantCreate = {
  request_id: string
  artifact_public_id: string
  workflow_id: string
  run_id: string
  expires_in_seconds?: number
}

export type ConnectorArtifactGrant = {
  artifact_grant_public_id: string
  reply_grant_public_id: string
  artifact_public_id: string
  project_id: string
  workflow_id: string
  run_id: string
  session_id: string
  content_hash: string
  title: string
  media_type: string
  simulated: boolean
  status: string
  version: number
  expires_at: string
  offer_delivery_status: string | null
  delivery_status: string | null
  error_code: string | null
  created_at: string
  updated_at: string
}

export type ConnectorArtifactGrantCreated = ConnectorArtifactGrant & {
  created: boolean
  claim_text: string | null
}

function replyGrantCollectionPath(workspaceId: string, projectId: string) {
  return `/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/connector-reply-grants`
}

function replyGrantPath(workspaceId: string, projectId: string, replyGrantId: string) {
  return `${replyGrantCollectionPath(workspaceId, projectId)}/${encodeURIComponent(replyGrantId)}`
}

function artifactGrantCollectionPath(workspaceId: string, projectId: string, replyGrantId: string) {
  return `${replyGrantPath(workspaceId, projectId, replyGrantId)}/artifact-grants`
}

function artifactGrantPath(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
  artifactGrantId: string,
) {
  return `${artifactGrantCollectionPath(workspaceId, projectId, replyGrantId)}/${encodeURIComponent(artifactGrantId)}`
}

export const connectorReplyGrantsQueryKey = (
  workspaceId: string | null,
  projectId: string | null,
  conversationId: string | null,
) => ['connector-reply-grants', workspaceId, projectId, conversationId] as const

export const connectorReplyGrantQueryKey = (
  workspaceId: string | null,
  projectId: string | null,
  conversationId: string | null,
  replyGrantId: string | null,
) => ['connector-reply-grant', workspaceId, projectId, conversationId, replyGrantId] as const

export const connectorArtifactGrantsQueryKey = (
  workspaceId: string | null,
  projectId: string | null,
  replyGrantId: string | null,
) => ['connector-artifact-grants', workspaceId, projectId, replyGrantId] as const

export const connectorArtifactGrantQueryKey = (
  workspaceId: string | null,
  projectId: string | null,
  replyGrantId: string | null,
  artifactGrantId: string | null,
) => ['connector-artifact-grant', workspaceId, projectId, replyGrantId, artifactGrantId] as const

export async function listConnectorReplyGrants(
  workspaceId: string,
  projectId: string,
  conversationId: string,
  limit = 100,
) {
  const response = await apiClient.get<ApiResponse<ConnectorReplyGrant[]>>(
    replyGrantCollectionPath(workspaceId, projectId),
    { params: { conversation_id: conversationId, limit } },
  )
  return response.data.data
}

export async function createConnectorReplyGrant(
  workspaceId: string,
  projectId: string,
  input: ConnectorReplyGrantCreate,
) {
  const response = await apiClient.post<ApiResponse<ConnectorReplyGrantCreated>>(
    replyGrantCollectionPath(workspaceId, projectId),
    input,
  )
  return response.data.data
}

export async function getConnectorReplyGrant(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
) {
  const response = await apiClient.get<ApiResponse<ConnectorReplyGrant>>(
    replyGrantPath(workspaceId, projectId, replyGrantId),
  )
  return response.data.data
}

export async function revokeConnectorReplyGrant(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
) {
  const response = await apiClient.delete<ApiResponse<ConnectorReplyGrant>>(
    replyGrantPath(workspaceId, projectId, replyGrantId),
  )
  return response.data.data
}

export async function listConnectorArtifactGrants(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
  limit = 100,
) {
  const response = await apiClient.get<ApiResponse<ConnectorArtifactGrant[]>>(
    artifactGrantCollectionPath(workspaceId, projectId, replyGrantId),
    { params: { limit } },
  )
  return response.data.data
}

export async function createConnectorArtifactGrant(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
  input: ConnectorArtifactGrantCreate,
) {
  const response = await apiClient.post<ApiResponse<ConnectorArtifactGrantCreated>>(
    artifactGrantCollectionPath(workspaceId, projectId, replyGrantId),
    input,
  )
  return response.data.data
}

export async function getConnectorArtifactGrant(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
  artifactGrantId: string,
) {
  const response = await apiClient.get<ApiResponse<ConnectorArtifactGrant>>(
    artifactGrantPath(workspaceId, projectId, replyGrantId, artifactGrantId),
  )
  return response.data.data
}

export async function revokeConnectorArtifactGrant(
  workspaceId: string,
  projectId: string,
  replyGrantId: string,
  artifactGrantId: string,
) {
  const response = await apiClient.delete<ApiResponse<ConnectorArtifactGrant>>(
    artifactGrantPath(workspaceId, projectId, replyGrantId, artifactGrantId),
  )
  return response.data.data
}

const terminalDeliveryStatuses = new Set(['sent', 'failed', 'indeterminate'])

export function useConnectorReplyGrants(
  workspaceId: string | null,
  projectId: string | null,
  conversationId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: connectorReplyGrantsQueryKey(workspaceId, projectId, conversationId),
    queryFn: () => listConnectorReplyGrants(workspaceId as string, projectId as string, conversationId as string),
    enabled: enabled && Boolean(workspaceId && projectId && conversationId),
  })
}

export function useConnectorReplyGrant(
  workspaceId: string | null,
  projectId: string | null,
  conversationId: string | null,
  replyGrantId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: connectorReplyGrantQueryKey(workspaceId, projectId, conversationId, replyGrantId),
    queryFn: () => getConnectorReplyGrant(workspaceId as string, projectId as string, replyGrantId as string),
    enabled: enabled && Boolean(workspaceId && projectId && replyGrantId),
    refetchInterval: (query) => {
      const status = query.state.data?.activation_delivery_status
      if (query.state.data?.status !== 'active' || (status && terminalDeliveryStatuses.has(status))) return false
      return 4_000
    },
  })
}

export function useConnectorArtifactGrants(
  workspaceId: string | null,
  projectId: string | null,
  replyGrantId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: connectorArtifactGrantsQueryKey(workspaceId, projectId, replyGrantId),
    queryFn: () => listConnectorArtifactGrants(workspaceId as string, projectId as string, replyGrantId as string),
    enabled: enabled && Boolean(workspaceId && projectId && replyGrantId),
  })
}

export function useConnectorArtifactGrant(
  workspaceId: string | null,
  projectId: string | null,
  replyGrantId: string | null,
  artifactGrantId: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: connectorArtifactGrantQueryKey(workspaceId, projectId, replyGrantId, artifactGrantId),
    queryFn: () => getConnectorArtifactGrant(workspaceId as string, projectId as string, replyGrantId as string, artifactGrantId as string),
    enabled: enabled && Boolean(workspaceId && projectId && replyGrantId && artifactGrantId),
    refetchInterval: (query) => {
      const offerStatus = query.state.data?.offer_delivery_status
      const deliveryStatus = query.state.data?.delivery_status
      if (query.state.data?.status !== 'active') return false
      if (deliveryStatus && terminalDeliveryStatuses.has(deliveryStatus)) return false
      if (offerStatus && offerStatus !== 'sent' && terminalDeliveryStatuses.has(offerStatus)) return false
      return 4_000
    },
  })
}

export function useCreateConnectorReplyGrant() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId, input }: { workspaceId: string; projectId: string; input: ConnectorReplyGrantCreate }) =>
      createConnectorReplyGrant(workspaceId, projectId, input),
    onSuccess: (_result, { workspaceId, projectId, input }) => {
      void queryClient.invalidateQueries({ queryKey: connectorReplyGrantsQueryKey(workspaceId, projectId, input.conversation_id) })
    },
  })
}

export function useRevokeConnectorReplyGrant() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId, replyGrantId }: { workspaceId: string; projectId: string; replyGrantId: string }) =>
      revokeConnectorReplyGrant(workspaceId, projectId, replyGrantId),
    onSuccess: (_result, { workspaceId, projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ['connector-reply-grants', workspaceId, projectId] })
      void queryClient.invalidateQueries({ queryKey: ['connector-reply-grant', workspaceId, projectId] })
    },
  })
}

export function useCreateConnectorArtifactGrant() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId, replyGrantId, input }: { workspaceId: string; projectId: string; replyGrantId: string; input: ConnectorArtifactGrantCreate }) =>
      createConnectorArtifactGrant(workspaceId, projectId, replyGrantId, input),
    onSuccess: (_result, { workspaceId, projectId, replyGrantId }) => {
      void queryClient.invalidateQueries({ queryKey: connectorArtifactGrantsQueryKey(workspaceId, projectId, replyGrantId) })
      void queryClient.invalidateQueries({ queryKey: ['connector-artifact-grant', workspaceId, projectId, replyGrantId] })
    },
  })
}

export function useRevokeConnectorArtifactGrant() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, projectId, replyGrantId, artifactGrantId }: { workspaceId: string; projectId: string; replyGrantId: string; artifactGrantId: string }) =>
      revokeConnectorArtifactGrant(workspaceId, projectId, replyGrantId, artifactGrantId),
    onSuccess: (_result, { workspaceId, projectId, replyGrantId, artifactGrantId }) => {
      void queryClient.invalidateQueries({ queryKey: connectorArtifactGrantsQueryKey(workspaceId, projectId, replyGrantId) })
      void queryClient.invalidateQueries({ queryKey: connectorArtifactGrantQueryKey(workspaceId, projectId, replyGrantId, artifactGrantId) })
    },
  })
}
