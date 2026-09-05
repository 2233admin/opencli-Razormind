import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiClient } from './client'
import type { ApiResponse } from './types'

export type ConnectorInstallation = {
  installation_public_id: string
  workspace_id: string
  provider: string
  name: string
  app_id: string
  tenant_key: string
  status: string
  config_revision: number
  has_app_secret: boolean
  has_encrypt_key: boolean
  has_verification_token: boolean
  created_at: string
  updated_at: string
}

export type ConnectorInstallationCreate = {
  name: string
  app_id: string
  tenant_key: string
  app_secret: string
  encrypt_key: string
  verification_token: string
}

export type ConnectorInstallationUpdate = {
  name?: string
  app_secret?: string
  encrypt_key?: string
  verification_token?: string
  enabled?: boolean
}

export type ConnectorInstallationHealth = {
  installation_public_id: string
  status: string
  callback_ready: boolean
  binding_ready: boolean
  reply_execution_ready: boolean
  artifact_delivery_ready: boolean
  last_ready_at: string | null
  last_error_code: string | null
}

export type ConnectorBindingChallenge = {
  challenge_public_id: string
  installation_public_id: string
  command_text: string
  expires_at: string
}

export type ConnectorBinding = {
  binding_public_id: string
  installation_public_id: string
  user_id: string
  active: boolean
  revoked_at: string | null
  updated_at: string
}

function installationPath(workspaceId: string) {
  return `/workspaces/${encodeURIComponent(workspaceId)}/connector-installations`
}

export const connectorInstallationsQueryKey = (workspaceId: string | null) =>
  ['connector-installations', workspaceId] as const

export const connectorInstallationHealthQueryKey = (
  workspaceId: string | null,
  installationId: string | null,
) => ['connector-installation-health', workspaceId, installationId] as const

export const connectorMyBindingQueryKey = (
  workspaceId: string | null,
  installationId: string | null,
) => ['connector-my-binding', workspaceId, installationId] as const

export async function listConnectorInstallations(workspaceId: string) {
  const response = await apiClient.get<ApiResponse<ConnectorInstallation[]>>(installationPath(workspaceId))
  return response.data.data
}

export async function createConnectorInstallation(
  workspaceId: string,
  input: ConnectorInstallationCreate,
) {
  const response = await apiClient.post<ApiResponse<ConnectorInstallation>>(
    installationPath(workspaceId),
    input,
  )
  return response.data.data
}

export async function updateConnectorInstallation(
  workspaceId: string,
  installationId: string,
  input: ConnectorInstallationUpdate,
) {
  const response = await apiClient.patch<ApiResponse<ConnectorInstallation>>(
    `${installationPath(workspaceId)}/${encodeURIComponent(installationId)}`,
    input,
  )
  return response.data.data
}

export async function getConnectorInstallationHealth(workspaceId: string, installationId: string) {
  const response = await apiClient.get<ApiResponse<ConnectorInstallationHealth>>(
    `${installationPath(workspaceId)}/${encodeURIComponent(installationId)}/health`,
  )
  return response.data.data
}

export async function createConnectorBindingChallenge(workspaceId: string, installationId: string) {
  const response = await apiClient.post<ApiResponse<ConnectorBindingChallenge>>(
    `${installationPath(workspaceId)}/${encodeURIComponent(installationId)}/binding-challenges`,
  )
  return response.data.data
}

export async function getMyConnectorBinding(workspaceId: string, installationId: string) {
  const response = await apiClient.get<ApiResponse<ConnectorBinding | null>>(
    `${installationPath(workspaceId)}/${encodeURIComponent(installationId)}/my-binding`,
  )
  return response.data.data
}

export async function revokeConnectorBinding(workspaceId: string, bindingId: string) {
  const response = await apiClient.delete<ApiResponse<ConnectorBinding>>(
    `/workspaces/${encodeURIComponent(workspaceId)}/connector-bindings/${encodeURIComponent(bindingId)}`,
  )
  return response.data.data
}

export function useConnectorInstallations(workspaceId: string | null) {
  return useQuery({
    queryKey: connectorInstallationsQueryKey(workspaceId),
    queryFn: () => listConnectorInstallations(workspaceId as string),
    enabled: Boolean(workspaceId),
  })
}

export function useConnectorInstallationHealth(
  workspaceId: string | null,
  installationId: string | null,
) {
  return useQuery({
    queryKey: connectorInstallationHealthQueryKey(workspaceId, installationId),
    queryFn: () => getConnectorInstallationHealth(workspaceId as string, installationId as string),
    enabled: Boolean(workspaceId && installationId),
    refetchInterval: 30_000,
  })
}

export function useMyConnectorBinding(workspaceId: string | null, installationId: string | null) {
  return useQuery({
    queryKey: connectorMyBindingQueryKey(workspaceId, installationId),
    queryFn: () => getMyConnectorBinding(workspaceId as string, installationId as string),
    enabled: Boolean(workspaceId && installationId),
  })
}

export function useCreateConnectorInstallation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, input }: { workspaceId: string; input: ConnectorInstallationCreate }) =>
      createConnectorInstallation(workspaceId, input),
    onSuccess: (_result, { workspaceId }) =>
      queryClient.invalidateQueries({ queryKey: connectorInstallationsQueryKey(workspaceId) }),
  })
}

export function useUpdateConnectorInstallation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      workspaceId,
      installationId,
      input,
    }: {
      workspaceId: string
      installationId: string
      input: ConnectorInstallationUpdate
    }) => updateConnectorInstallation(workspaceId, installationId, input),
    onSuccess: (_result, { workspaceId, installationId }) => {
      void queryClient.invalidateQueries({ queryKey: connectorInstallationsQueryKey(workspaceId) })
      void queryClient.invalidateQueries({
        queryKey: connectorInstallationHealthQueryKey(workspaceId, installationId),
      })
    },
  })
}

export function useCreateConnectorBindingChallenge() {
  return useMutation({
    mutationFn: ({ workspaceId, installationId }: { workspaceId: string; installationId: string }) =>
      createConnectorBindingChallenge(workspaceId, installationId),
  })
}

export function useRevokeConnectorBinding() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ workspaceId, bindingId }: { workspaceId: string; bindingId: string }) =>
      revokeConnectorBinding(workspaceId, bindingId),
    onSuccess: (_result, { workspaceId, bindingId }) => {
      void queryClient.invalidateQueries({ queryKey: ['connector-my-binding', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['connector-installation-health', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['connector-binding', workspaceId, bindingId] })
    },
  })
}
