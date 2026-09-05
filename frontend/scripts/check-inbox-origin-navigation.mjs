import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { buildScopedAgentUrl } from '../lib/studio/run-navigation.ts'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('trusted inbox origin accepts only server-projected IDs and local paths', async () => {
  const source = await read('lib/inbox/origin-navigation.ts')
  assert.match(source, /project_id.*workflow_id.*run_id.*conversation_id/s)
  assert.match(source, /projectedWorkspace !== workspace/)
  assert.match(source, /new URLSearchParams\(\{ agent: '1', conversation: origin\.conversation, workspace: origin\.workspace \}\)/)
  assert.match(source, /\/inbox\?\$\{params\.toString\(\)\}/)
  assert.doesNotMatch(source, /evidence\.href|evidence\.url/)
})

test('missing, invalid, and cross-workspace origin states stay explicit', async () => {
  const source = await read('lib/inbox/origin-navigation.ts')
  assert.match(source, /unavailableReason: hasKnownKey \? 'invalid' : 'missing'/)
  assert.match(source, /unavailableReason: 'scope-mismatch'/)
  assert.match(source, /if \(origin\.unavailableReason \|\| !origin\.workspace\) return \[\]/)
})

test('result discussion opens a scoped Dock only with complete route context', () => {
  const href = buildScopedAgentUrl('/studio/projects/project-a/data', {
    workspace: 'workspace-a', project: 'project-a', workflow: 'workflow-a', run: 'run-a',
  })
  assert.ok(href)
  const url = new URL(href, 'https://example.test')
  assert.equal(url.searchParams.get('conversation'), null)
  assert.equal(url.searchParams.get('agent'), '1')
  assert.equal(buildScopedAgentUrl('/studio/projects/project-a/data', { workspace: 'workspace-a', project: 'project-a' }), null)
})
