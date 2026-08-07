import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('login keeps the liquid, terminal, and pixel theme switcher', async () => {
  const login = await read('app/login/page.tsx')

  assert.match(login, /type LoginBackdrop = 'liquid' \| 'terminal' \| 'pixel'/)
  assert.match(login, /aria-label="登录背景主题"/)
  assert.match(login, /<PixelLiquidBg/)
  assert.match(login, /<FaultyTerminal/)
  assert.match(login, /<Dither/)
})

test('login keeps local setup, recovery, OIDC, development, and reduced-motion paths', async () => {
  const [login, localAdmin] = await Promise.all([
    read('app/login/page.tsx'),
    read('components/auth/local-admin-access.tsx'),
  ])

  assert.match(login, /signInWithOidc/)
  assert.match(login, /enterDevelopmentMode/)
  assert.match(login, /prefers-reduced-motion: reduce/)
  assert.match(localAdmin, /setupLocalAdmin/)
  assert.match(localAdmin, /signInWithLocal/)
  assert.match(localAdmin, /signInWithBootstrap/)
  assert.match(localAdmin, /localAdminStatus/)
  assert.match(localAdmin, /refreshLocalAdminStatus/)
})

test('auth defaults return to the project list instead of a contextless workflow', async () => {
  const [provider, oidc] = await Promise.all([
    read('components/auth/auth-provider.tsx'),
    read('lib/auth/oidc.ts'),
  ])

  assert.match(provider, /returnTo = ['"]\/studio['"]/)
  assert.doesNotMatch(provider, /returnTo = ['"]\/studio\/workflow['"]/)
  assert.match(oidc, /return ['"]\/studio['"]/)
  assert.doesNotMatch(oidc, /return ['"]\/studio\/workflow['"]/)
})

test('OIDC keeps PKCE in the browser while proxying CORS-blocked token and JWKS calls', async () => {
  const [provider, header, oidc, nextConfig] = await Promise.all([
    read('components/auth/auth-provider.tsx'),
    read('components/shell/app-header.tsx'),
    read('lib/auth/oidc.ts'),
    read('next.config.mjs'),
  ])

  assert.match(provider, /acceptIdentityToken\(user\.id_token\)/)
  assert.match(provider, /acceptIdentityToken\(oidcUser\.id_token\)/)
  assert.doesNotMatch(provider, /acceptIdentityToken\(user\.access_token\)/)
  assert.doesNotMatch(provider, /acceptIdentityToken\(oidcUser\.access_token\)/)
  assert.match(oidc, /NEXT_PUBLIC_OIDC_AUTHORIZATION_ENDPOINT/)
  assert.match(oidc, /token_endpoint: `\$\{origin\}\/api\/auth\/oidc\/token`/)
  assert.match(oidc, /jwks_uri: `\$\{origin\}\/api\/auth\/oidc\/jwks`/)
  assert.doesNotMatch(oidc, /login\/oauth/)
  assert.match(nextConfig, /destination: OIDC_TOKEN_ENDPOINT/)
  assert.match(nextConfig, /destination: OIDC_JWKS_URL/)
  assert.doesNotMatch(nextConfig, /login\/oauth/)
  assert.match(header, /<AvatarImage src=\{avatarUrl\}/)
  assert.match(header, /identity\?\.picture/)
  assert.match(header, /identity\?\.username/)
})
