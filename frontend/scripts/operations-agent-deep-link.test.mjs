import assert from 'node:assert/strict'
import test from 'node:test'

import {
  resolveOperationsAgentConfigTarget,
  resolveOperationsAgentWorkspace,
} from '../lib/operations-agent-deep-link.ts'

test('deep link selects only an accessible governed workspace', () => {
  assert.equal(
    resolveOperationsAgentWorkspace('workspace-b', null, ['workspace-a', 'workspace-b']),
    'workspace-b',
  )
  assert.equal(
    resolveOperationsAgentWorkspace('foreign-workspace', 'workspace-b', ['workspace-a', 'workspace-b']),
    'workspace-b',
  )
  assert.equal(
    resolveOperationsAgentWorkspace('foreign-workspace', null, ['workspace-a']),
    'workspace-a',
  )
})

test('deep link opens only an accessible target in the selected workspace and mode', () => {
  const base = {
    activeWorkspaceId: 'workspace-a',
    requestedWorkspaceId: 'workspace-a',
    requestedAgentId: null,
    requestedAutomationId: null,
    configMode: null,
    accessibleAgentIds: ['agent-a'],
    accessibleAutomationIds: ['automation-a'],
  }

  assert.deepEqual(
    resolveOperationsAgentConfigTarget({
      ...base,
      requestedAgentId: 'agent-a',
      configMode: 'contract',
    }),
    { kind: 'agent', id: 'agent-a', mode: 'contract' },
  )
  assert.deepEqual(
    resolveOperationsAgentConfigTarget({
      ...base,
      requestedAutomationId: 'automation-a',
      configMode: 'binding',
    }),
    { kind: 'automation', id: 'automation-a', mode: 'binding' },
  )
  assert.equal(
    resolveOperationsAgentConfigTarget({
      ...base,
      requestedWorkspaceId: 'workspace-b',
      requestedAgentId: 'agent-a',
      configMode: 'contract',
    }),
    null,
  )
  assert.equal(
    resolveOperationsAgentConfigTarget({
      ...base,
      requestedAgentId: 'foreign-agent',
      configMode: 'contract',
    }),
    null,
  )
  assert.equal(
    resolveOperationsAgentConfigTarget({
      ...base,
      requestedAutomationId: 'automation-a',
      configMode: 'contract',
    }),
    null,
  )
})
