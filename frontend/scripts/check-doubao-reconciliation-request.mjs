import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync, readFileSync } from 'node:fs'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
registerHooks({
  resolve(specifier, context, nextResolve) {
    const candidate = specifier.startsWith('@/') ? path.join(frontendRoot, specifier.slice(2))
      : specifier.startsWith('.') && context.parentURL?.startsWith('file:')
        ? path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier) : null
    if (candidate) {
      for (const resolvedPath of [candidate, `${candidate}.ts`]) {
        if (existsSync(resolvedPath)) return { url: pathToFileURL(resolvedPath).href, shortCircuit: true }
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url.endsWith('.ts')) return { format: 'module', shortCircuit: true,
      source: stripTypeScriptTypes(readFileSync(fileURLToPath(url), 'utf8'), { mode: 'strip', sourceUrl: url }) }
    return nextLoad(url, context)
  },
})
const { resumeGaojixingWorkflowRun, queryWorkflowRunTrace } = await import('../lib/workflow/backend-runs.ts')
const { setRuntimeIdentityToken } = await import('../lib/auth/session.ts')
const { fetchWorkflowResearchGraph, mutateWorkflowResearchGraph } = await import('../lib/workflow/research-graph-client.ts')

test('trace, recovery and the default research extension preserve identity and fleet authentication', async () => {
  const originalFetch = globalThis.fetch
  const previousFleet = process.env.NEXT_PUBLIC_API_AUTH_TOKEN
  process.env.NEXT_PUBLIC_API_AUTH_TOKEN = 'fleet-test'
  setRuntimeIdentityToken('identity-test')
  const calls = []
  globalThis.fetch = async (_url, options) => {
    calls.push(options)
    return Response.json({ success: true, data: { runId: 'run-a' } })
  }
  try {
    await queryWorkflowRunTrace('run-a')
    await resumeGaojixingWorkflowRun('run-a', { expectedChatUrl: 'https://www.doubao.com/chat/123' })
    await fetchWorkflowResearchGraph('run-a', { authorization: 'Bearer identity-test' })
    await mutateWorkflowResearchGraph('run-a', { action: 'propose', expectedSequence: 0 }, { authorization: 'Bearer identity-test' })
    assert.equal(calls.length, 4)
    for (const call of calls) {
      assert.equal(call.headers.Authorization, 'Bearer identity-test')
      assert.equal(call.headers['X-API-Token'], 'fleet-test')
    }
  } finally {
    globalThis.fetch = originalFetch
    setRuntimeIdentityToken('')
    if (previousFleet === undefined) delete process.env.NEXT_PUBLIC_API_AUTH_TOKEN
    else process.env.NEXT_PUBLIC_API_AUTH_TOKEN = previousFleet
  }
})

test('trace loading normalizes the actual Studio envelope and preserves generic trace responses', async () => {
  const originalFetch = globalThis.fetch
  const trace = { projection: { runId: 'run-a', status: 'waiting' }, events: [], nextAfterSequence: 32 }
  try {
    for (const data of [trace, { workflow_version: 1, trace }]) {
      globalThis.fetch = async () => Response.json({ success: true, data })
      assert.deepEqual(await queryWorkflowRunTrace('run-a'), trace)
    }
  } finally { globalThis.fetch = originalFetch }
})

test('explicit recovery sends only the selected URL with the existing run scope and auth', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({url, options})
    return Response.json({success: true, data: {runId: 'run-a', status: 'waiting'}})
  }
  try {
    const result = await resumeGaojixingWorkflowRun('run-a', {
      authorization: 'Bearer test-token',
      scope: {workspaceId: 'workspace-a', projectId: 'project-a', workflowId: 'workflow-a'},
      expectedChatUrl: ' https://www.doubao.com/chat/123456 ',
    })
    assert.equal(result.runId, 'run-a')
    assert.equal(calls.length, 1)
    const endpoint = new URL(calls[0].url, 'https://app.test')
    assert.match(endpoint.pathname, /\/run-a\/gaojixing\/resume$/)
    assert.equal(endpoint.searchParams.get('workspace'), 'workspace-a')
    assert.equal(endpoint.searchParams.get('project'), 'project-a')
    assert.equal(endpoint.searchParams.get('workflow'), 'workflow-a')
    assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token')
    assert.deepEqual(JSON.parse(calls[0].options.body), {expectedChatUrl: 'https://www.doubao.com/chat/123456'})
  } finally {globalThis.fetch = originalFetch}
})

test('ordinary resume remains bodyless and server errors remain visible', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.body, undefined)
    return Response.json({success: false, message: 'Recovery is not authorized'}, {status: 403})
  }
  try {
    await assert.rejects(resumeGaojixingWorkflowRun('run-a'), /Recovery is not authorized/)
  } finally {globalThis.fetch = originalFetch}
})

test('temporary, foreign and malformed conversation links never submit a recovery request', async () => {
  const originalFetch = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => {calls++; throw new Error('Unexpected network call')}
  try {
    for (const expectedChatUrl of ['http://www.doubao.com/chat/1', 'https://www.doubao.com/chat/local_1',
      'https://www.doubao.com.evil.test/chat/1', 'https://www.doubao.com/chat/1?other=2', 'not a URL']) {
      await assert.rejects(resumeGaojixingWorkflowRun('run-a', {expectedChatUrl}), /正式会话链接/)
    }
    assert.equal(calls, 0)
  } finally {globalThis.fetch = originalFetch}
})
