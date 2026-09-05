import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('persistent chrome cannot start native root snapshots alongside SSGOI', async () => {
  const [config, themeToggle, shell, routeTransition] = await Promise.all([
    read('next.config.mjs'),
    read('components/shell/theme-toggle.tsx'),
    read('components/shell/app-shell.tsx'),
    read('components/motion/app-route-transition.tsx'),
  ])

  assert.doesNotMatch(config, /viewTransition/)
  assert.doesNotMatch(themeToggle, /ViewTransition|startTransition/)
  assert.doesNotMatch(shell, /<ViewTransition\b/)
  assert.doesNotMatch(routeTransition, /<ViewTransition\b/)
})

test('persistent application chrome stays outside the routed animation boundary', async () => {
  const shell = await read('components/shell/app-shell.tsx')
  const sidebarIndex = shell.indexOf('<AppSidebar />')
  const headerIndex = shell.indexOf('<AppHeader')
  const transitionIndex = shell.indexOf('<AppRouteTransition>')

  assert.ok(sidebarIndex >= 0, 'AppSidebar should remain mounted')
  assert.ok(headerIndex >= 0, 'AppHeader should remain mounted')
  assert.ok(transitionIndex > sidebarIndex, 'route animation must not wrap the sidebar')
  assert.ok(transitionIndex > headerIndex, 'route animation must not wrap the header')
  assert.match(shell, /<AppRouteTransition>\{children\}<\/AppRouteTransition>/)
  assert.match(shell, /className="[^"]*relative[^"]*z-0[^"]*overflow-x-clip[^"]*bg-background[^"]*"/)
})

test('sidebar keeps automation separate from Agent surfaces', async () => {
  const [navigation, sidebar] = await Promise.all([
    read('lib/navigation.ts'),
    read('components/shell/app-sidebar.tsx'),
  ])

  for (const label of [
    '概览',
    '任务与通知',
    '项目',
    '插件中心',
    '自动化与智能体',
    '执行资源',
    '成果与数据',
    '模型与连接',
  ]) {
    assert.match(navigation, new RegExp(`label: '${label}'`))
  }

  assert.doesNotMatch(navigation, /href: '\/agent-workbench'/)
  assert.match(navigation, /href: '\/inbox\?tab=pending'/)
  assert.match(navigation, /match: \['\/inbox', '\/tasks', '\/notifications'\]/)
  assert.match(navigation, /match: \['\/operations-agents', '\/schedules'\]/)
  assert.doesNotMatch(navigation, /label: '自动化与 Agent'/)
  assert.doesNotMatch(navigation, /match: \[[^\]]*'\/agents'/)
  assert.doesNotMatch(navigation, /match: \[[^\]]*'\/skills'/)
  assert.match(navigation, /'\/operations-agents': '自动化与智能体'/)
  assert.match(navigation, /match: \['\/nodes', '\/workers', '\/browsers'\]/)
  assert.match(navigation, /match: \['\/providers'\]/)
  assert.match(navigation, /href: '\/control\/kill-switch'[\s\S]{0,120}match: \['\/control'\]/)
  for (const group of ['工作台', '构建', '运行与数据', '管理']) {
    assert.match(navigation, new RegExp(`label: '${group}'`))
  }
  assert.doesNotMatch(navigation, /label: '工作项'/)
  assert.doesNotMatch(navigation, /label: 'Agent 团队'/)
  assert.doesNotMatch(navigation, /CREATE_WORK_ITEM/)
  assert.doesNotMatch(sidebar, /CREATE_WORK_ITEM/)
  assert.doesNotMatch(sidebar, /新建工作/)
})

test('records use a scalable schema-adaptive table with pagination and raw evidence detail', async () => {
  const records = await read('app/(app)/records/page.tsx')

  assert.match(records, /DATA_EXPLORER_TABS/)
  assert.match(records, /useRecords\(\{/)
  assert.doesNotMatch(records, /useSources|selectedSourceId/)
  assert.match(records, /const MAX_VISIBLE_FIELDS = 7/)
  assert.match(records, /limit: PAGE_SIZE/)
  assert.match(records, /visibleFields/)
  assert.match(records, /aria-label="当前数据字段"/)
  assert.match(records, /<Table className="min-w-max">/)
  assert.match(records, /第 \{page\.toLocaleString/)
  assert.match(records, /<Sheet open=\{Boolean\(selectedRecord\)\}/)
  assert.match(records, /<LineagePanel record=\{selectedRecord\}/)
  assert.match(records, /标准化数据/)
  assert.match(records, /原始数据/)
})

test('task and automation sibling routes share their consolidated route tabs', async () => {
  const [tabs, inbox, tasks, notifications, sources, schedules, plans, agents, skills] = await Promise.all([
    read('components/shell/route-tabs.tsx'),
    read('app/(app)/inbox/page.tsx'),
    read('app/(app)/tasks/page.tsx'),
    read('app/(app)/notifications/page.tsx'),
    read('app/(app)/sources/page.tsx'),
    read('app/(app)/schedules/page.tsx'),
    read('app/(app)/plans/page.tsx'),
    read('app/(app)/agents/page.tsx'),
    read('app/(app)/skills/page.tsx'),
  ])

  for (const label of ['待处理', '工作项', '通知规则', '自动化与智能体', 'Agent', '技能']) {
    assert.match(tabs, new RegExp(`label: '${label}'`))
  }
  for (const page of [inbox, tasks, notifications]) {
    assert.match(page, /ACTION_CENTER_TABS/)
  }
  for (const page of [plans, agents, skills]) {
    assert.match(page, /AUTOMATION_TABS/)
  }
  assert.match(sources, /redirect\('\/records'\)/)
  assert.match(schedules, /redirect\('\/operations-agents'\)/)
})

test('studio keeps Agent conversation global while management has its own entry', async () => {
  const [studio, templates, shell, header, agentDock, transition] = await Promise.all([
    read('app/(app)/studio/page.tsx'),
    read('app/(app)/studio/templates/page.tsx'),
    read('components/shell/app-shell.tsx'),
    read('components/shell/app-header.tsx'),
    read('components/shell/global-agent-dock.tsx'),
    read('components/motion/app-route-transition.tsx'),
  ])

  assert.match(studio, /\/plugins\?type=template&workspace=/)
  assert.match(studio, /创建空白工作流/)
  assert.match(studio, /setCreateTemplate\('blank'\)/)
  assert.doesNotMatch(studio, /Collection starters/i)
  assert.doesNotMatch(studio, /从采集项目开始/)
  assert.doesNotMatch(studio, /FEATURED_COLLECTION_TEMPLATES/)
  assert.doesNotMatch(studio, /与 Agent 创建/)
  assert.doesNotMatch(studio, /\/studio\/new\?workspace=/)
  assert.match(templates, /redirect\(`\/plugins\?\$\{params\.toString\(\)\}`\)/)
  assert.match(shell, /onOpenAgent=\{\(\) => setAgentOpen\(true\)\}/)
  assert.match(shell, /<GlobalAgentDock open=\{agentOpen\} onOpenChange=\{setAgentOpen\} \/>/)
  assert.match(header, /onOpenAgent\?: \(\) => void/)
  assert.match(header, /aria-label="打开全局 Agent"/)
  assert.match(agentDock, /当前上下文/)
  assert.match(agentDock, /new URLSearchParams\(searchParams\.toString\(\)\)/)
  assert.match(agentDock, /workspace_id: workspaceId/)
  assert.match(agentDock, /const workspaceId = navigationParams\.get\('workspace'\)/)
  assert.match(agentDock, /project_id: navigationParams\.get\('project'\)/)
  assert.match(agentDock, /workflow_id: navigationParams\.get\('workflow'\)/)
  assert.match(agentDock, /source_id: navigationParams\.get\('source'\)/)
  assert.match(agentDock, /apiClient\.post\('\/chat\/confirm', \{ proposal \}\)/)
  assert.match(agentDock, /status === 409/)
  assert.match(agentDock, /仅在后端能解析出唯一授权范围时允许确认写操作/)
  assert.match(agentDock, /\/chat\/confirm/)
  assert.match(agentDock, /queryClient\.invalidateQueries/)
  assert.match(transition, /on: '\/\*\*'/)
})

test('SSGOI boundary is pathname-keyed and every app path shares one history-driven rule', async () => {
  const transition = await read('components/motion/app-route-transition.tsx')

  assert.match(transition, /const pathname = usePathname\(\)/)
  assert.match(transition, /key=\{transitionKey\}/)
  assert.match(transition, /data-ssgoi-transition=\{transitionKey\}/)
  assert.match(transition, /const transitionKey = pathname\s/)
  assert.match(transition, /className="[^"]*h-full[^"]*min-h-full[^"]*"/)
  assert.match(transition, /on: '\/\*\*'/)
  assert.match(transition, /transition: createRouteTransition\(/)
  assert.doesNotMatch(transition, /ordered:|\baxis\(/)
  assert.doesNotMatch(transition, /\bdrill\(/)
  assert.match(transition, /<Ssgoi config=\{config\}>/)
  assert.match(transition, /const \[config\] = useState<SsgoiConfig>/)
  assert.doesNotMatch(transition, /STATIC_CONFIG|useReducedMotion/)
})

test('navigation indicators do not introduce separate layout springs or ripple clocks', async () => {
  const [sidebar, tabs] = await Promise.all([
    read('components/shell/app-sidebar.tsx'),
    read('components/shell/route-tabs.tsx'),
  ])

  for (const component of [sidebar, tabs]) {
    assert.doesNotMatch(component, /layoutId|stiffness|damping|<Ripple/)
  }
})

test('route-level loading, pixel indicators, and recovery boundaries remain available', async () => {
  const [loading, error, dataStates, matrix, authGate, workflowSession] = await Promise.all([
    read('app/(app)/loading.tsx'),
    read('app/(app)/error.tsx'),
    read('components/shell/data-states.tsx'),
    read('components/unlumen-ui/matrix.tsx'),
    read('components/auth/auth-gate.tsx'),
    read('components/flow/workflow-editor-session.tsx'),
  ])

  assert.match(loading, /<LoadingState rows=\{5\}/)
  assert.match(error, /<Button onClick=\{reset\}>重试当前视图<\/Button>/)
  assert.match(matrix, /export const loader:/)
  assert.match(dataStates, /frames=\{loader\}/)
  assert.match(dataStates, /size=\{5\}/)
  assert.match(dataStates, /animate-none/)
  assert.match(matrix, /React\.useId\(\)\.replaceAll\(":", ""\)/)
  assert.match(matrix, /id=\{pixelOnId\}/)
  assert.match(matrix, /id=\{pixelOffId\}/)
  assert.match(matrix, /id=\{glowId\}/)
  assert.doesNotMatch(matrix, /id="matrix-(?:pixel|glow)/)
  assert.match(authGate, /frames=\{loader\}/)
  assert.match(workflowSession, /ariaLabel="正在加载工作流"/)
})
