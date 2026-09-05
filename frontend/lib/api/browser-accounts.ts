import { apiClient } from './client'
import type { ApiResponse } from './types'

export type AccountPlatform = string
export type BrowserAccount = {
  id: string
  platform: AccountPlatform
  label: string
  browser_instance_id: string
  profile_name: string
  status: 'unconfirmed' | 'confirmed' | 'needs_login' | 'profile_changed' | 'archived' | 'deleting'
  confirmed_at: string | null
  confirmation_source: 'operator' | null
  browser_state: 'ready' | 'unavailable' | 'busy_or_unavailable'
  login_url: string
  novnc_port: number | null
}
export type BrowserAccountList = {
  accounts: BrowserAccount[]
  archived_accounts?: BrowserAccount[]
  available_instances: { id: string; label: string; profile_name: string }[]
}

export const listBrowserAccounts = () => apiClient.get<ApiResponse<BrowserAccountList>>('/browser-accounts').then(r => r.data.data)
export const createBrowserAccount = (data: { site_url: string }) =>
  apiClient.post<ApiResponse<BrowserAccount>>('/browser-accounts', data, { timeout: 120_000 }).then(r => r.data.data)
export const confirmBrowserAccount = (id: string, status: 'confirmed' | 'needs_login') =>
  apiClient.post(`/browser-accounts/${id}/confirmation`, { status })
export const renameBrowserAccount = (id: string, label: string) => apiClient.patch(`/browser-accounts/${id}`, { label })
export const removeBrowserAccount = (id: string, clearLoginData: boolean) => apiClient.delete(`/browser-accounts/${id}`, { data: { clear_login_data: clearLoginData }, timeout: 60_000 })
export const restoreBrowserAccount = (id: string) => apiClient.post(`/browser-accounts/${id}/restore`)

export type LoginFrame = { image: string; width: number; height: number; title: string; origin: string; suggested_name: string }
export type LoginAction = { kind: 'click' | 'text' | 'key' | 'scroll' | 'reload'; x?: number; y?: number; text?: string; key?: string; delta?: number }
export const listLoginWebsites = () => apiClient.get<ApiResponse<{ label: string; url: string }[]>>('/browser-accounts/websites').then(r => r.data.data)
export const openBrowserLogin = (id: string, signal?: AbortSignal) => apiClient.post<ApiResponse<LoginFrame>>(`/browser-accounts/${id}/login`, {}, { signal }).then(r => r.data.data)
export const getBrowserLoginFrame = (id: string, signal?: AbortSignal) => apiClient.get<ApiResponse<LoginFrame>>(`/browser-accounts/${id}/frame`, { signal }).then(r => r.data.data)
export const sendBrowserLoginInput = (id: string, action: LoginAction, signal?: AbortSignal) => apiClient.post<ApiResponse<LoginFrame>>(`/browser-accounts/${id}/input`, action, { signal }).then(r => r.data.data)
