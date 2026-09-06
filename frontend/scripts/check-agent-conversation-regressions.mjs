import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

const sourcePromise = read('components/shell/global-agent-dock.tsx')
const apiPromise = read('lib/api/agent-conversations.ts')

test('Dock reload recovery uses the selected Workspace session pointer and replays turns', async () => {
  const source = await sourcePromise
  const api = await apiPromise
  assert.match(source, /listAgentConversations\(workspaceId, 20, hasScopedResultContext/)
  assert.match(source, /getAgentConversation\(sessionId\)/)
  assert.match(source, /opencli:agent-session:\$\{workspaceId\}/)
  assert.match(source, /restoreConversation\(detail\)/)
  assert.match(api, /after_sequence: afterSequence/)
})

test('Dock session switching changes the next-turn context without rewriting history', async () => {
  const source = await sourcePromise
  assert.match(source, /selectSession\(nextId: string\)/)
  assert.match(source, /context: navigationParams|context,/)
  assert.match(source, /sendAgentConversationMessage\(activeSessionId,/)
  assert.match(source, /context,/)
  assert.doesNotMatch(source, /localStorage\.setItem\([^,]+, JSON\.stringify\(messages\)/)
})

test('Dock supports starting a new session and closing the active session', async () => {
  const source = await sourcePromise
  const api = await apiPromise
  assert.match(source, /startNewSession\(\)/)
  assert.match(source, /closeSession\(\)/)
  assert.match(source, /closeAgentConversation\(sessionId\)/)
  assert.match(source, /aria-label="新建 Agent 会话"/)
  assert.match(source, /aria-label="关闭当前 Agent 会话"/)
  assert.match(api, /\/chat\/sessions\/\$\{conversationId\}\/close/)
})

test('Dock restores proposal turns and preserves Agent Control confirmation', async () => {
  const source = await sourcePromise
  assert.match(source, /response\?\.type === 'proposal'/)
  assert.match(source, /setProposal\(restored\.proposal\)/)
  assert.match(source, /apiClient\.post\('\/chat\/confirm', \{ proposal \}\)/)
  assert.match(source, /待确认操作/)
})

test('Ask Alice restores persisted tool records instead of inventing execution logs', async () => {
  const source = await read('components/shell/agent-conversation-surface.tsx')
  assert.match(source, /turn\.tool_trace\.length > 0/)
  assert.match(source, /setToolTraces\(restored\.toolTraces\)/)
  assert.match(source, /已执行 \{trace\.entries\.length\} 项工具操作/)
})

test('Dock bridges only a trusted, scoped Studio workspace into durable sessions', async () => {
  const source = await sourcePromise
  const api = await apiPromise
  assert.match(source, /useAuth\(\)/)
  assert.match(source, /isTrustedStudioContext/)
  assert.match(source, /identity\?\.auth_method === 'local'/)
  assert.match(source, /identity\?\.auth_method === 'bootstrap'/)
  assert.match(source, /identity\?\.is_platform_admin/)
  assert.match(source, /detail\.context_binding\.studio_workspace_id === workspaceId/)
  assert.match(source, /detail\.workspace_id === workspaceId && !detail\.context_binding\.studio_workspace_id/)
  assert.match(api, /readonly studio_workspace_id\?: string \| null/)
  assert.match(api, /AgentConversationRequestContext/)
})
