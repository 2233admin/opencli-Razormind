import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { test } from 'node:test'

const frontendRoot = fileURLToPath(new URL('../', import.meta.url))

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    if (entry.isDirectory() && ['.next', 'node_modules', 'dist', 'build'].includes(entry.name)) continue
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...(await filesUnder(path)))
    else if (/\.(?:ts|tsx)$/.test(entry.name)) files.push(path)
  }
  return files
}

async function readProjectFiles() {
  const paths = (await filesUnder(frontendRoot)).filter((path) => !path.endsWith(join('lib', 'api', 'hooks.ts')))
  const contents = await Promise.all(paths.map(async (path) => [path, await readFile(path, 'utf8')]))
  return new Map(contents)
}

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('control center exposes all real control-plane states and actions', async () => {
  const [center, killSwitch, advisory, odp, tabs] = await Promise.all([
    read('app/(app)/control/page.tsx'),
    read('app/(app)/control/kill-switch/page.tsx'),
    read('app/(app)/control/advisory-report/page.tsx'),
    read('app/(app)/control/odp-state/page.tsx'),
    read('components/shell/route-tabs.tsx'),
  ])

  for (const hook of ['useKillSwitch', 'useSetKillSwitch', 'useAdvisoryReport', 'useOdpState', 'useControlActions']) {
    assert.match(center, new RegExp(`\\b${hook}\\b`))
  }
  assert.match(center, /role=\{killFeedback\.kind === 'error' \? 'alert' : 'status'\}/)
  assert.match(center, /onError: \(cause: Error\)/)
  assert.match(center, /实时熔断|熔断/)
  assert.match(tabs, /href: '\/inbox\?tab=controls'/)
  assert.match(killSwitch, /useKillSwitch/)
  assert.match(killSwitch, /useSetKillSwitch/)
  assert.match(advisory, /useAdvisoryReport/)
  assert.match(odp, /useOdpState/)
  for (const href of ['/control/kill-switch', '/control/advisory-report', '/control/odp-state']) {
    assert.match(tabs, new RegExp(`href: '${href.replaceAll('/', '\\/')}'`))
  }
})

test('skill correction review preserves full detail after dismiss and rollback', async () => {
  const [list, detail, hooks, endpoints] = await Promise.all([
    read('app/(app)/skills/page.tsx'),
    read('app/(app)/skills/[id]/page.tsx'),
    read('lib/api/hooks.ts'),
    read('lib/api/endpoints.ts'),
  ])

  assert.match(list, /href=\{`\/skills\/\$\{s\.id\}`\}/)
  for (const hook of ['useSkill', 'useDismissCorrection', 'useRollbackSkill', 'useRedistillSkill']) {
    assert.match(detail, new RegExp(`\\b${hook}\\b`))
  }
  for (const action of ['忽略纠正建议', '回滚到上一版本', '使用失败 trace 重蒸馏']) {
    assert.match(detail, new RegExp(action))
  }
  assert.match(endpoints, /ApiResponse<SkillBrief>[\s\S]*dismiss-correction/)
  assert.match(hooks, /onSuccess: async \(_brief, id\)/)
  const dismissHook = hooks.slice(
    hooks.indexOf('export function useDismissCorrection'),
    hooks.indexOf('export function useRollbackSkill'),
  )
  assert.doesNotMatch(dismissHook, /setQueryData/)
})

test('all requested W5 wrappers and workspace settings remain wired', async () => {
  const files = await readProjectFiles()
  const project = [...files.values()].join('\n')
  const requiredHooks = [
    // agents
    'useCreateAgent',
    'useUpdateAgent',
    'useDeleteAgent',
    // plans
    'useCreatePlan',
    'useUpdatePlan',
    'useDeletePlan',
    'useRunPlan',
    'usePlanHealth',
    // schedules
    'useCreateSchedule',
    'useUpdateSchedule',
    'useDeleteSchedule',
    // sources and records
    'useCreateSource',
    'useSourceCredentials',
    'useStoreSourceCredential',
    'useDeleteSourceCredential',
    'useRecord',
    'useDeleteRecord',
    'useClearAllRecords',
    'useBatchDeleteRecords',
    // browsers and workers
    'useBrowserBindings',
    'useCreateBrowserBinding',
    'useDeleteBrowserBinding',
    'useWsAgentStatus',
    'useRestartApi',
    'useAddChromeInstance',
    'useRemoveChromeInstance',
    'useUpdateChromeInstanceConfig',
    // system
    'useCeleryStats',
    'useSystemConfig',
    'useUpdateSystemConfig',
    // notifications
    'useDeleteNotificationRule',
    // nodes
    'useNodeEvents',
    'useNodeStats',
    'useDeleteNode',
  ]
  for (const hook of requiredHooks) {
    assert.match(project, new RegExp(`\\b${hook}\\b`), `${hook} should be consumed by a page or component`)
  }

  const endpoints = await read('lib/api/endpoints.ts')
  const workspaceEndpoints = await read('lib/api/workspace-endpoints.ts')
  const hooks = await read('lib/api/hooks.ts')
  const types = await read('lib/api/types.ts')
  assert.match(project, /\buseWorkspaceSettings\b/)
  assert.match(project, /\buseUpdateWorkspaceSettings\b/)
  assert.match(project, /\buseResetWorkspaceSettings\b/)
  for (const endpoint of ['getWorkspaceSettings', 'updateWorkspaceSettings', 'resetWorkspaceSettings']) {
    assert.match(workspaceEndpoints, new RegExp(`\\b${endpoint}\\b`))
  }
  assert.match(hooks, /\buseWorkspaceSettings\b/)
  assert.match(hooks, /\buseUpdateWorkspaceSettings\b/)
  assert.match(hooks, /\buseResetWorkspaceSettings\b/)
  assert.match(types, /\bWorkspaceSettingsRead\b/)
  assert.match(types, /\bWorkspaceSettingsValues\b/)
})
