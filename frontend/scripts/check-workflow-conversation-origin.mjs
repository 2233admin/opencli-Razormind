import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const dock = readFileSync(new URL('../components/shell/global-agent-dock.tsx', import.meta.url), 'utf8')
const panel = readFileSync(new URL('../components/flow/run-trace-panel.tsx', import.meta.url), 'utf8')
const runs = readFileSync(new URL('../lib/workflow/backend-runs.ts', import.meta.url), 'utf8')

test('Agent workflow links preserve the loaded conversation explicitly', () => {
  assert.match(dock, /function workflowDraftHref\([\s\S]*query\.set\('conversation', conversationId\)/)
  assert.match(dock, /setCompletedResultHref\(workflowDraftHref\([\s\S]*sessionId,/)
  assert.match(dock, /workflowDraftHref\(workspaceId,[\s\S]*selectedSession\.id\)/)
})

test('only scoped published starts send the conversation candidate', () => {
  assert.match(panel, /searchParams\.get\("conversation"\)\?\.trim\(\) \|\| null/)
  assert.match(panel, /startWorkspaceWorkflowRun\(scope, \{ authorization, input, conversationId \}\)/)
  assert.match(panel, /runFileInput && workflowRunScope \? \{ conversationId \} : \{\}/)
  assert.match(runs, /\.\.\.\(options\.conversationId \? \{ conversation_id: options\.conversationId \} : \{\}\)/)
  const legacyStart = runs.indexOf('body: questionBankBody ?? JSON.stringify({')
  const legacyEnd = runs.indexOf('return readApiResponse(response, "Workflow run failed")', legacyStart)
  assert.ok(legacyStart >= 0 && legacyEnd > legacyStart)
  const legacyBody = runs.slice(legacyStart, legacyEnd)
  assert.doesNotMatch(legacyBody, /conversation_id/)
})
