import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('../components/studio/agent-work-health.tsx', import.meta.url)
const apiUrl = new URL('../lib/api/agent-work-health.ts', import.meta.url)

test('Agent work health UI renders every server state and explicit non-resume copy', async () => {
  const source = await readFile(componentUrl, 'utf8')
  for (const state of [
    'not_started', 'ready', 'queued', 'running', 'paused', 'completed',
    'failed', 'blocked', 'inactive', 'cancelled',
  ]) {
    assert.match(source, new RegExp(`\\b${state}:`))
  }
  assert.match(source, /!item\.resume_supported/)
  assert.match(source, /不会被描述为恢复/)
  assert.match(source, /health\.isLoading/)
  assert.match(source, /health\.isError/)
  assert.match(source, /health\.data\?\.items\.length === 0/)
})

test('Agent work actions call only existing pause and automation run endpoints', async () => {
  const source = await readFile(apiUrl, 'utf8')
  assert.match(source, /action\.kind === 'pause_run'/)
  assert.match(source, /action\.kind === 'run_automation' \|\| action\.kind === 'retry_automation'/)
  assert.match(source, /operations-agents\/\$\{action\.operations_agent_id\}\/runs\/\$\{action\.run_id\}\/pause/)
  assert.match(source, /automations\/\$\{action\.automation_id\}\/runs/)
  assert.doesNotMatch(source, /kind: 'resume'/)
})
