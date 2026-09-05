/**
 * Focused regression test for WorkflowLifecycleStrip's state -> view logic.
 * No test runner is configured in this project (no vitest/jest), so this
 * follows the existing plain-script pattern (see generate-m3-theme.mjs):
 * a standalone Node script run directly, using Node's built-in TypeScript
 * support and the built-in assert module.
 * Usage: node scripts/test-workflow-lifecycle-strip.mjs
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { deriveWorkflowLifecycleView } from '../components/studio/workflow-lifecycle-strip.logic.ts'

test('draft exposes only workflow authoring stages', () => {
  const view = deriveWorkflowLifecycleView('draft')
  assert.equal(view.primaryStatusLabel, '草稿')
  assert.deepEqual(view.stages.map((stage) => stage.key), ['draft', 'validate', 'publish'])
})

test('validating -> validated -> publishing labels are distinct', () => {
  assert.equal(deriveWorkflowLifecycleView('validating').primaryStatusLabel, '验证中')
  assert.equal(deriveWorkflowLifecycleView('validated').primaryStatusLabel, '已验证')
  assert.equal(deriveWorkflowLifecycleView('publishing').primaryStatusLabel, '发布中')
})

test('published: publish stage is done', () => {
  const view = deriveWorkflowLifecycleView('published')
  assert.equal(view.primaryStatusLabel, '已发布')

  const publish = view.stages.find((s) => s.key === 'publish')
  assert.equal(publish.status, 'done')

})

test('blocked: exactly one blocker message surfaces and validate stage reports error', () => {
  const view = deriveWorkflowLifecycleView('blocked', '节点端口类型不匹配')
  assert.equal(view.primaryStatusLabel, '已阻塞')
  assert.equal(view.blockerText, '节点端口类型不匹配')

  const validate = view.stages.find((s) => s.key === 'validate')
  assert.equal(validate.status, 'error')
})

test('blocker text is only surfaced when state is actually blocked', () => {
  const view = deriveWorkflowLifecycleView('draft', 'stale blocker text should be ignored')
  assert.equal(view.blockerText, undefined)
})

test('every state exposes exactly three workflow authoring stages in a stable order', () => {
  const states = ['draft', 'validating', 'validated', 'publishing', 'published', 'blocked']
  for (const state of states) {
    const view = deriveWorkflowLifecycleView(state)
    assert.deepEqual(
      view.stages.map((s) => s.key),
      ['draft', 'validate', 'publish'],
    )
  }
})

test('render contract carries authoring revision and published version separately', async () => {
  const { readFile } = await import('node:fs/promises')
  const [source, logic, session] = await Promise.all([
    readFile(new URL('../components/studio/workflow-lifecycle-strip.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../components/studio/workflow-lifecycle-strip.logic.ts', import.meta.url), 'utf8'),
    readFile(new URL('../components/flow/workflow-editor-session.tsx', import.meta.url), 'utf8'),
  ])
  assert.match(source, /revision: number \| null/)
  assert.match(source, /publishedVersion\?: number \| null/)
  assert.match(source, /aria-label="工作流生命周期"/)
  assert.match(session, /data-testid="workflow-lifecycle-panel"/)
  assert.match(session, /aria-label="保存、验证与发布"/)
  assert.doesNotMatch(session, /lifecyclePanelOpen|workflow-lifecycle-toggle/)
  assert.doesNotMatch(logic, /activate|激活|待后端接入/)
})
