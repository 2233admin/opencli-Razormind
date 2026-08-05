const IDENTITY_TOKEN_KEY = 'opencli.identityToken'
const DEVELOPMENT_SESSION_KEY = 'opencli.developmentSession'

let runtimeIdentityToken = ''
let runtimeDevelopmentSession = false

function safeSessionGet(key: string): string {
  try {
    return typeof sessionStorage === 'undefined' ? '' : sessionStorage.getItem(key)?.trim() ?? ''
  } catch {
    return ''
  }
}

function safeSessionSet(key: string, value: string): void {
  try {
    if (typeof sessionStorage === 'undefined') return
    if (value) sessionStorage.setItem(key, value)
    else sessionStorage.removeItem(key)
  } catch {
    // Storage can be unavailable in private browsing or hardened browsers.
  }
}

export function getIdentityAccessToken(): string {
  return runtimeIdentityToken || safeSessionGet(IDENTITY_TOKEN_KEY)
}

export function setRuntimeIdentityToken(token: string): void {
  runtimeIdentityToken = token.trim()
}

export function getPersistedIdentityToken(): string {
  return safeSessionGet(IDENTITY_TOKEN_KEY)
}

export function persistIdentityToken(token: string): void {
  const trimmed = token.trim()
  runtimeIdentityToken = trimmed
  safeSessionSet(IDENTITY_TOKEN_KEY, trimmed)
}

export function clearIdentityToken(): void {
  runtimeIdentityToken = ''
  safeSessionSet(IDENTITY_TOKEN_KEY, '')
}

export function hasDevelopmentSession(): boolean {
  return runtimeDevelopmentSession || safeSessionGet(DEVELOPMENT_SESSION_KEY) === '1'
}

export function setDevelopmentSession(enabled: boolean): void {
  runtimeDevelopmentSession = enabled
  safeSessionSet(DEVELOPMENT_SESSION_KEY, enabled ? '1' : '')
}

export function isDevelopmentLoginAllowed(): boolean {
  return (
    process.env.NODE_ENV !== 'production' &&
    process.env.NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV !== 'false'
  )
}
