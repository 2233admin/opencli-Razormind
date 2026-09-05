import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../app/(app)/sources/page.tsx', import.meta.url), 'utf8')
const endpoints = await readFile(new URL('../lib/api/endpoints.ts', import.meta.url), 'utf8')

test('legacy sources redirects to records while workspace source APIs remain available', () => {
  assert.match(endpoints, /\/governance\/workspaces/)
  assert.match(page, /redirect\('\/records'\)/)
  assert.match(endpoints, /\/workspaces\/\$\{workspaceId\}\/sources/)
  assert.match(endpoints, /\/workspaces\/\$\{workspaceId\}\/projects\/\$\{projectId\}\/source-bindings/)
  assert.doesNotMatch(page, /href=\{`\/sources\/\$\{source\.id\}`\}/)
})
