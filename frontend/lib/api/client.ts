import axios, { type InternalAxiosRequestConfig } from 'axios'

import { getIdentityGeneration } from '@/lib/auth/session'

import { getApiAuthHeaders } from './auth-headers'
import { notifyAuthRequired } from './auth-events'
import { FLEET_AUTH_ERROR_CODE, hasFleetTransportCredential } from './recovery'

type IdentityAwareRequestConfig = InternalAxiosRequestConfig & {
  opencliFleetTransportCredentialAttached?: boolean
  opencliIdentityGeneration?: number
}

export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

export const rootClient = axios.create({
  headers: { 'Content-Type': 'application/json' },
})

// Attach user identity and fleet transport credentials centrally. With both
// configured, Authorization carries OIDC/bootstrap identity and X-API-Token
// carries the deployment's fleet credential (ADR-0005).
const attachAuthHeaders = (config: InternalAxiosRequestConfig) => {
  const identityGeneration = getIdentityGeneration()
  const headers = getApiAuthHeaders()
  ;(config as IdentityAwareRequestConfig).opencliIdentityGeneration = identityGeneration
  if (headers.Authorization && !config.headers.Authorization) {
    config.headers.Authorization = headers.Authorization
  }
  if (headers['X-API-Token'] && !config.headers['X-API-Token']) {
    config.headers['X-API-Token'] = headers['X-API-Token']
  }
  const fleetTransportCredential =
    config.headers.get?.('X-API-Token') ?? config.headers['X-API-Token']
  ;(config as IdentityAwareRequestConfig).opencliFleetTransportCredentialAttached =
    hasFleetTransportCredential(fleetTransportCredential)
  return config
}

apiClient.interceptors.request.use(attachAuthHeaders)
rootClient.interceptors.request.use(attachAuthHeaders)

// Plan IR issue 07: a 422 from the Plans API carries a node-anchored error
// LIST in `detail` (backend.plan_ir.validation.PlanValidationError.to_dict()
// shape), not a string. Conversation errors can instead carry a dictionary
// with a public message and recovery code.
// Stringifying `detail` unconditionally (the old behavior) turned that list
// into a useless comma-joined blob for every caller and threw away the
// node_id/edge_id anchors the canvas needs to render errors in place — so
// array details are left OUT of the message and attached raw as `.detail`
// instead, additive to every existing caller that only reads `.message`.
const normalizeApiError = (err: unknown) => {
  if (axios.isAxiosError(err)) {
    const requestGeneration = (err.config as IdentityAwareRequestConfig | undefined)
      ?.opencliIdentityGeneration
    const fleetTransportCredentialAttached =
      (err.config as IdentityAwareRequestConfig | undefined)
        ?.opencliFleetTransportCredentialAttached === true
    const responseCode = err.response?.data?.code
    const retainIdentityForFleetFailure =
      responseCode === FLEET_AUTH_ERROR_CODE && fleetTransportCredentialAttached
    if (
      err.response?.status === 401 &&
      !retainIdentityForFleetFailure &&
      typeof requestGeneration === 'number'
    ) {
      notifyAuthRequired(requestGeneration)
    }
    const detail = err.response?.data?.detail
    const detailIsList = Array.isArray(detail)
    const detailMessage = detail && typeof detail === 'object' && !detailIsList
      ? (typeof detail.message === 'string' ? detail.message : undefined)
      : detail
    const message =
      err.response?.data?.error || (detailIsList ? undefined : detailMessage) || err.message || 'Unknown error'
    const normalized = new Error(message) as Error & {
      code?: string
      detail?: unknown
      fleetTransportCredentialAttached?: boolean
      status?: number
    }
    if (detailIsList || (detail && typeof detail === 'object')) normalized.detail = detail
    if (typeof responseCode === 'string') normalized.code = responseCode
    normalized.fleetTransportCredentialAttached = fleetTransportCredentialAttached
    normalized.status = err.response?.status
    return Promise.reject(normalized)
  }
  return Promise.reject(err)
}

apiClient.interceptors.response.use(
  (res) => res,
  normalizeApiError
)

rootClient.interceptors.response.use(
  (res) => res,
  normalizeApiError
)
