export type AuthIdentity = {
  subject: string
  email: string | null
  name: string | null
  username: string | null
  picture: string | null
  is_platform_admin: boolean
  auth_method: 'oidc' | 'local' | 'bootstrap' | 'development' | string
}

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous'
