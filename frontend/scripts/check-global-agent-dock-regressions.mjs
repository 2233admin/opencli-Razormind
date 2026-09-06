import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const dockSource = () => readFile(
  new URL('../components/shell/global-agent-dock.tsx', import.meta.url),
  'utf8',
)

const {
  AGENT_CONVERSATION_ROUND_LIMIT,
  proposalQueryKeys,
  recentAgentMessages,
} = await import('../lib/agent-dock-state.ts')

test('Agent Dock restores durable conversations and renders the explicit recent window', async () => {
  const history = Array.from({ length: 15 }, (_, index) => [
    { role: 'user', content: `user-${index + 1}` },
    { role: 'assistant', content: `assistant-${index + 1}` },
  ]).flat()

  const recent = recentAgentMessages(history)
  assert.equal(AGENT_CONVERSATION_ROUND_LIMIT, 12)
  assert.equal(recent.length, 24)
  assert.deepEqual(recent[0], { role: 'user', content: 'user-4' })
  assert.deepEqual(recent.at(-1), { role: 'assistant', content: 'assistant-15' })

  const pending = recentAgentMessages([
    ...history,
    { role: 'user', content: 'user-16' },
  ])
  assert.equal(pending.filter((message) => message.role === 'user').length, 12)
  assert.deepEqual(pending[0], { role: 'user', content: 'user-5' })
  assert.deepEqual(pending.at(-1), { role: 'user', content: 'user-16' })

  const malformedRecovery = recentAgentMessages(
    Array.from({ length: 30 }, (_, index) => ({
      role: 'assistant',
      content: `recovered-${index + 1}`,
    })),
  )
  assert.equal(malformedRecovery.length, 24)
  assert.equal(malformedRecovery[0].content, 'recovered-7')

  const dock = await dockSource()
  assert.match(dock, /getAgentConversation\(sessionId\)/)
  assert.match(dock, /sendAgentConversationMessage\(activeSessionId,/)
  assert.doesNotMatch(dock, /messages: nextMessages/)
  assert.match(dock, /const visibleMessages = recentAgentMessages\(messages\)/)
  assert.match(dock, /\{visibleMessages\.map\(/)
})

test('Agent Dock confirmation invalidates only proposal-related query keys', async () => {
  for (const key of ['project-workflows', 'project-workflow-versions']) {
    assert(proposalQueryKeys({ tool: 'publish_workflow', args: {} }).some((query) => query[0] === key))
  }
  for (const key of ['project-runtime-summary', 'project-runtime-logs', 'agent-conversations']) {
    assert(proposalQueryKeys({ tool: 'run_managed_doubao_question', args: {} }).some((query) => query[0] === key))
  }
  assert.deepEqual(
    proposalQueryKeys({ tool: 'toggle_source', workspace_id: 'workspace-1' }),
    [
      ['sources'],
      ['dashboard', 'stats'],
      ['operations-inbox', 'workspace-1'],
      ['control-actions'],
    ],
  )
  assert.deepEqual(
    proposalQueryKeys({ tool: 'trigger_task', workspace_id: 'workspace-1' }),
    [['tasks'], ['dashboard', 'stats'], ['operations-inbox', 'workspace-1'], ['control-actions']],
  )
  assert.deepEqual(
    proposalQueryKeys({ tool: 'update_schedule', workspace_id: 'workspace-1' }),
    [['schedules'], ['operations-inbox', 'workspace-1'], ['control-actions']],
  )
  assert.deepEqual(
    proposalQueryKeys({ tool: 'update_provider', workspace_id: 'workspace-1' }),
    [['providers'], ['operations-inbox', 'workspace-1'], ['control-actions']],
  )

  const dock = await dockSource()
  assert.match(dock, /proposalQueryKeys\(proposalToConfirm\)\.map\(\(queryKey\) =>/)
  assert.match(dock, /invalidateQueries\(\{ queryKey \}\)/)
  assert.doesNotMatch(dock, /invalidateQueries\(\s*\)/)
})
