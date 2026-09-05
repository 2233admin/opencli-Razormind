import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('project Agent workspace scopes the session library to the project and opens the durable dock context', async () => {
  const source = await read('components/studio/project-agent-workspace.tsx')
  assert.match(source, /useAgentConversations\(workspaceId, \{[\s\S]*projectId/)
  assert.match(source, /context_binding\.project_id === projectId/)
  assert.match(source, /params\.set\('agent', '1'\)/)
  assert.match(source, /params\.set\('conversation', conversationId\)/)
  assert.match(source, /opencli:project-agent-tabs:/)
})

test('project Agent workspace exposes search, lifecycle filters, recovery and a new-session path', async () => {
  const source = await read('components/studio/project-agent-workspace.tsx')
  assert.match(source, /搜索项目 Agent 会话/)
  assert.match(source, /项目会话状态筛选/)
  assert.match(source, /进行中，可继续/)
  assert.match(source, /开始新会话/)
  assert.match(source, /关闭工作标签/)
})

test('project overview makes the workspace visible alongside project readiness', async () => {
  const source = await read('app/(app)/studio/projects/[projectId]/page.tsx')
  assert.match(source, /ProjectAgentWorkspace/)
  assert.match(source, /workspaceId=\{workspaceId\} projectId=\{projectId\}/)
})
