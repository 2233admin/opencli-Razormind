import assert from 'node:assert/strict'
import test from 'node:test'
import { resumeGaojixingWorkflowRun } from '../lib/workflow/backend-runs.ts'

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
