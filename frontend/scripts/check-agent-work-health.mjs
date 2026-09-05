import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('../components/studio/agent-work-health.tsx', import.meta.url)
const apiUrl = new URL('../lib/api/agent-work-health.ts', import.meta.url)
const operationsPageUrl = new URL('../app/(app)/operations-agents/page.tsx', import.meta.url)

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
  assert.match(source, /暂无 Agent 工作/)
  assert.doesNotMatch(source, /运行路径正常/)
})

test('Agent work actions call only existing pause and automation run endpoints', async () => {
  const source = await readFile(apiUrl, 'utf8')
  assert.match(source, /action\.kind === 'pause_run'/)
  assert.match(source, /action\.kind === 'run_automation' \|\| action\.kind === 'retry_automation'/)
  assert.match(source, /operations-agents\/\$\{encodeURIComponent\(action\.operations_agent_id\)\}\/runs\/\$\{encodeURIComponent\(action\.run_id\)\}\/pause/)
  assert.match(source, /automations\/\$\{encodeURIComponent\(action\.automation_id\)\}\/runs/)
  assert.doesNotMatch(source, /kind: 'resume'/)
})

test('repair links keep governed action scope and the operations page validates targets', async () => {
  const [component, page] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(operationsPageUrl, 'utf8'),
  ])
  assert.match(component, /performAgentWorkAction\(health\.data\.workspace_id, next\)/)
  assert.match(component, /navigationWorkspaceId=\{health\.data\.studio_workspace_id \?\? health\.data\.workspace_id\}/)
  assert.match(component, /params\.set\('config', 'contract'\)/)
  assert.match(component, /params\.set\('config', 'binding'\)/)
  assert.match(page, /resolveOperationsAgentWorkspace\(/)
  assert.match(page, /resolveOperationsAgentConfigTarget\(/)
  assert.match(page, /agents\.data\?\.find\(\(candidate\) => candidate\.id === target\.id\)/)
  assert.match(page, /automations\.data\?\.find\(\(candidate\) => candidate\.id === target\.id\)/)
})
