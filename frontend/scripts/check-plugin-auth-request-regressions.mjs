import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

registerHooks({
  resolve(specifier, context, nextResolve) {
    const candidates = []
    if (specifier.startsWith('@/')) {
      candidates.push(path.join(frontendRoot, specifier.slice(2)))
    } else if (specifier.startsWith('.') && context.parentURL?.startsWith('file:')) {
      candidates.push(path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier))
    }
    for (const candidate of candidates) {
      for (const resolvedPath of [candidate, `${candidate}.ts`, `${candidate}.tsx`]) {
        if (existsSync(resolvedPath)) {
          return { url: pathToFileURL(resolvedPath).href, shortCircuit: true }
        }
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.ts') || url.endsWith('.tsx')) {
      return {
        format: 'module',
        source: stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), {
          mode: 'strip',
          sourceUrl: url,
        }),
        shortCircuit: true,
      }
    }
    return nextLoad(url, context)
  },
})

const memoryStorage = () => {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  }
}

const catalogFixture = {
  version: 'opencli.node-capabilities.v1',
  authority: 'backend',
  nodes: [],
  categories: [],
  summary: { total: 0, byReadiness: {}, byOrigin: {} },
}

test('plugin catalog requests use the current identity and fleet credentials', async () => {
  const previousFetch = globalThis.fetch
  const previousLocalStorage = globalThis.localStorage
  const previousSessionStorage = globalThis.sessionStorage
  const previousFleetToken = process.env.NEXT_PUBLIC_API_AUTH_TOKEN
  const previousDevelopmentLogin = process.env.NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV
  const localStorage = memoryStorage()
  const sessionStorage = memoryStorage()
  const requests = []
  let responseFactory = (url) => ({
    ok: true,
    status: 200,
    json: async () => ({
      success: true,
      data: url.endsWith('/capabilities') ? catalogFixture : [],
    }),
  })

  globalThis.localStorage = localStorage
  globalThis.sessionStorage = sessionStorage
  delete process.env.NEXT_PUBLIC_API_AUTH_TOKEN
  process.env.NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV = 'false'
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    requests.push({ url, headers: Object.fromEntries(new Headers(init.headers).entries()) })
    return responseFactory(url)
  }

  let setApiAuthToken = () => {}
  let setRuntimeIdentityToken = () => {}
  try {
    const [authToken, session, plugins, capabilities] = await Promise.all([
      import('../lib/api/auth-token.ts'),
      import('../lib/auth/session.ts'),
      import('../lib/plugins/backend-plugin-catalog.ts'),
      import('../lib/plugins/backend-node-capabilities.ts'),
    ])
    setApiAuthToken = authToken.setApiAuthToken
    setRuntimeIdentityToken = session.setRuntimeIdentityToken

    const clients = [
      {
        name: 'plugin installations',
        fetch: plugins.fetchPluginInstallations,
        globalPath: '/api/v1/plugins',
        workspacePath: '/api/v1/workspaces/workspace%20one/plugins',
      },
      {
        name: 'node capabilities',
        fetch: capabilities.fetchBackendNodeCapabilityCatalog,
        globalPath: '/api/v1/plugins/capabilities',
        workspacePath: '/api/v1/workspaces/workspace%20one/plugins/capabilities',
      },
    ]
    const credentialCases = [
      {
        name: 'identity only',
        identity: 'identity-token',
        fleet: '',
        headers: { authorization: 'Bearer identity-token' },
      },
      {
        name: 'identity and fleet',
        identity: 'identity-token',
        fleet: 'fleet-token',
        headers: {
          authorization: 'Bearer identity-token',
          'x-api-token': 'fleet-token',
        },
      },
      {
        name: 'fleet only',
        identity: '',
        fleet: 'fleet-token',
        headers: { authorization: 'Bearer fleet-token' },
      },
      {
        name: 'anonymous',
        identity: '',
        fleet: '',
        headers: {},
      },
    ]

    for (const client of clients) {
      for (const route of [
        { workspaceId: undefined, path: client.globalPath },
        { workspaceId: 'workspace one', path: client.workspacePath },
      ]) {
        for (const credentials of credentialCases) {
          setRuntimeIdentityToken(credentials.identity)
          setApiAuthToken(credentials.fleet)
          const requestIndex = requests.length

          await client.fetch(route.workspaceId)

          assert.deepEqual(
            requests[requestIndex],
            { url: route.path, headers: credentials.headers },
            `${client.name} ${route.path} with ${credentials.name}`,
          )
          assert.equal(
            'x-opencli-development-identity' in requests[requestIndex].headers,
            false,
            `${client.name} must not fabricate a development identity`,
          )
        }
      }
    }

    setRuntimeIdentityToken('identity-token')
    setApiAuthToken('fleet-token')
    responseFactory = () => ({
      ok: false,
      status: 401,
      json: async () => ({
        success: false,
        detail: { code: 'invalid_api_token', message: 'Invalid or missing API token' },
      }),
    })
    await assert.rejects(
      plugins.fetchPluginInstallations('workspace one'),
      /Invalid or missing API token/,
    )

    responseFactory = () => ({
      ok: false,
      status: 503,
      json: async () => {
        throw new SyntaxError('not json')
      },
    })
    await assert.rejects(
      capabilities.fetchBackendNodeCapabilityCatalog(),
      /节点能力目录读取失败 \(503\)/,
    )
  } finally {
    setRuntimeIdentityToken('')
    setApiAuthToken('')
    if (previousFetch === undefined) delete globalThis.fetch
    else globalThis.fetch = previousFetch
    if (previousLocalStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = previousLocalStorage
    if (previousSessionStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = previousSessionStorage
    if (previousFleetToken === undefined) delete process.env.NEXT_PUBLIC_API_AUTH_TOKEN
    else process.env.NEXT_PUBLIC_API_AUTH_TOKEN = previousFleetToken
    if (previousDevelopmentLogin === undefined) {
      delete process.env.NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV
    } else {
      process.env.NEXT_PUBLIC_ALLOW_UNAUTHENTICATED_DEV = previousDevelopmentLogin
    }
  }
})
