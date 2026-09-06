import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildProjectNavigationUrl,
  buildScopedAgentUrl,
  buildRunUrl,
  clearRunNavigation,
  parseRunNavigation,
  shouldDiscardTraceHistoryEntry,
  traceCloseAction,
} from '../lib/studio/run-navigation.ts'

test('project tabs retain a run only within its exact workspace, project, and workflow scope', () => {
  const current = {
    workspace: 'workspace-a',
    project: 'project-a',
    workflow: 'workflow-a',
    run: 'run-a',
    trace: 'trace-a',
    conversation: 'conversation-a',
  }

  const matching = new URL(buildProjectNavigationUrl('evidence', { workspace: 'workspace-a', project: 'project-a', workflow: 'workflow-a' }, current), 'https://example.test')
  assert.equal(matching.searchParams.get('run'), 'run-a')
  assert.equal(matching.searchParams.get('trace'), 'trace-a')
  assert.equal(matching.searchParams.get('conversation'), 'conversation-a')

  for (const scope of [
    { workspace: 'workspace-b', project: 'project-a', workflow: 'workflow-a' },
    { workspace: 'workspace-a', project: 'project-b', workflow: 'workflow-a' },
    { workspace: 'workspace-a', project: 'project-a', workflow: 'workflow-b' },
  ]) {
    const href = new URL(buildProjectNavigationUrl('data', scope, current), 'https://example.test')
    assert.equal(href.searchParams.has('run'), false)
    assert.equal(href.searchParams.has('trace'), false)
    assert.equal(href.searchParams.has('conversation'), false)
  }
})

test('a confirmed run link preserves the exact originating conversation', () => {
  const context = { workspace: 'studio-a', project: 'project-a', workflow: 'workflow-a', run: 'run-a', conversation: 'conversation-a' }
  const url = new URL(buildRunUrl('operations', context), 'https://example.test')
  assert.equal(url.searchParams.get('conversation'), context.conversation)
  assert.equal(parseRunNavigation(url.searchParams).conversation, context.conversation)
})

test('run URLs encode scope and closing a trace retains its project context', () => {
  const href = buildRunUrl('operations', { workspace: 'workspace a', project: 'project/a b', workflow: 'workflow-a', run: 'run-a', trace: 'trace/a' })
  const url = new URL(href, 'https://example.test')
  assert.match(href, /\/studio\/projects\/project%2Fa%20b\/operations\?/)
  assert.match(href, /workspace\+a/)
  assert.match(href, /trace%2Fa/)
  assert.deepEqual(parseRunNavigation(url.searchParams), { workspace: 'workspace a', project: 'project/a b', workflow: 'workflow-a', run: 'run-a', trace: 'trace/a' })

  const cleared = clearRunNavigation(url.searchParams)
  assert.equal(cleared.get('workspace'), 'workspace a')
  assert.equal(cleared.get('project'), 'project/a b')
  assert.equal(cleared.get('workflow'), 'workflow-a')
  assert.equal(cleared.has('run'), false)
  assert.equal(cleared.has('trace'), false)
})

test('project navigation encodes a project ID as one path segment', () => {
  const href = buildProjectNavigationUrl(
    'overview',
    { workspace: 'workspace-a', project: 'project/a b', workflow: 'workflow-a' },
    {},
  )
  assert.match(href, /^\/studio\/projects\/project%2Fa%20b\?/)
})

test('trace close goes back only for the component-pushed target and discards stale markers', () => {
  const entry = { sourceHref: '/studio/projects/project-a/operations?workspace=workspace-a', targetHref: '/studio/projects/project-a/operations?workspace=workspace-a&workflow=workflow-a&run=run-a&trace=trace-a' }
  assert.equal(traceCloseAction(entry.targetHref, entry), 'back')
  assert.equal(traceCloseAction('/studio/projects/project-a/operations?workspace=workspace-a&run=run-b', entry), 'replace')
  assert.equal(shouldDiscardTraceHistoryEntry(entry.sourceHref, entry), false)
  assert.equal(shouldDiscardTraceHistoryEntry(entry.targetHref, entry), false)
  assert.equal(shouldDiscardTraceHistoryEntry('/studio/projects/project-b/operations?workspace=workspace-a&workflow=workflow-a&run=run-a', entry), true)
})

test('scoped agent links preserve the exact project run context', () => {
  const href = buildScopedAgentUrl('/studio/projects/project-a/evidence', {
    workspace: 'workspace-a', project: 'project-a', workflow: 'workflow-a', run: 'run-a',
  })
  const url = new URL(href, 'https://example.test')
  assert.equal(url.searchParams.get('agent'), '1')
  assert.equal(url.searchParams.get('workspace'), 'workspace-a')
  assert.equal(url.searchParams.get('run'), 'run-a')
})
