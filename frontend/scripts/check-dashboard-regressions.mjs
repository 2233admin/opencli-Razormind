import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')

test('dashboard is an action-first control plane backed by real hooks', async () => {
  const dashboard = await read('app/(app)/dashboard/page.tsx')

  assert.match(dashboard, /useDashboardStats\(\)/)
  assert.match(dashboard, /useDashboardActivity\(\)/)
  assert.match(dashboard, /useOpinionMonitor\(\)/)
  assert.match(dashboard, /useWorkers\(\)/)
  assert.match(dashboard, /useAgents\(\{ enabled: true \}\)/)
  assert.match(dashboard, /useNotificationLogs\(\)/)
  assert.match(dashboard, /useNotificationRules\(\)/)
  assert.match(dashboard, /useSchedules\(\{ enabled: true \}\)/)
  assert.doesNotMatch(dashboard, /useMonitorFeed/)
  assert.doesNotMatch(dashboard, /演示数据/)
})

test('dashboard restores the real signal chain and makes Agent delivery visible', async () => {
  const dashboard = await read('app/(app)/dashboard/page.tsx')

  for (const label of ['从来源到 Agent，再到交付', '来源', '运行', '数据', 'Agent', '交付']) {
    assert.match(dashboard, new RegExp(label))
  }
  assert.match(dashboard, /<AgentDeliveryPanel/)
  assert.match(dashboard, /已提交（\{delivery\.attempts\} 次尝试）/)
  assert.match(dashboard, /已确认回执/)
  assert.match(dashboard, /提交成功不等于已确认送达/)
  assert.match(dashboard, /发送目标由通知规则决定，不预设为某一个平台/)
  assert.match(dashboard, /notificationChannels=\{activeDeliveryChannels\}/)
  assert.match(dashboard, /deliveryChannels=\{activeDeliveryChannels\.length\}/)
  assert.doesNotMatch(dashboard, /邮箱 P0/)
  assert.doesNotMatch(dashboard, /邮箱专属统计/)
})

test('dashboard answers attention, live state, and next action before analytics', async () => {
  const dashboard = await read('app/(app)/dashboard/page.tsx')
  const attention = dashboard.indexOf('需要你处理')
  const liveState = dashboard.indexOf('现在正在发生')
  const nextAction = dashboard.indexOf('>下一步</p>')
  const overview = dashboard.indexOf('系统概览')

  assert.ok(attention >= 0, 'attention summary should be present')
  assert.ok(liveState > attention, 'live state should follow the attention summary')
  assert.ok(nextAction > liveState, 'next actions should follow live state')
  assert.ok(overview > nextAction, 'analytics should be secondary to actions')

  for (const href of ['/studio', '/sources', '/schedules', '/tasks']) {
    assert.match(dashboard, new RegExp(`href="${href.replace('/', '\\/')}"`))
  }
  assert.doesNotMatch(dashboard, /href="\/studio\/workflow"/)
})

test('dashboard keeps existing real operational views after the action layer', async () => {
  const dashboard = await read('app/(app)/dashboard/page.tsx')

  assert.match(dashboard, /<TaskStream tasks=\{stream\}/)
  assert.match(dashboard, /<FailureFeed failures=\{failures\}/)
  assert.match(dashboard, /<FailureFeed failures=\{failures\} totalFailed=\{s\.tasks\.failed\} \/>/)
  assert.match(dashboard, /<ThroughputChart data=\{throughput\} daily/)
  assert.match(dashboard, /<WorkerAllocation workers=\{workers\}/)
  assert.match(dashboard, /<OpinionMonitorPanel/)
})

test('task stream uses a compact empty state instead of rendering an empty table shell', async () => {
  const taskStream = await read('components/monitor/task-stream.tsx')

  assert.match(taskStream, /tasks\.length === 0/)
  assert.match(taskStream, /data-stream-empty/)
  assert.match(taskStream, /当前没有排队或运行中的任务/)
  assert.match(taskStream, /<Card size="sm" className="h-full">/)
})

test('dashboard treats failures as a compact state layer above the full-width run history', async () => {
  const dashboard = await read('app/(app)/dashboard/page.tsx')
  const taskStream = await read('components/monitor/task-stream.tsx')

  assert.doesNotMatch(dashboard, /min-h-72/)
  assert.match(dashboard, /grid items-start gap-3/)
  assert.match(dashboard, /<section className="grid gap-4" aria-label="运行与异常">/)
  assert.doesNotMatch(dashboard, /grid items-start gap-4 lg:grid-cols-3/)
  assert.match(taskStream, /groupStreamTasks/)
  assert.match(taskStream, /groupFailures/)
  assert.match(taskStream, /相同任务、Worker 与状态已合并/)
  assert.match(taskStream, /同类失败 ×/)
  assert.match(taskStream, /aria-label="失败与重试"/)
  assert.match(taskStream, /最近运行未发现需要重试或人工处理的异常/)
  assert.match(taskStream, /查看运行历史/)
  assert.match(taskStream, /divide-y divide-border/)
  assert.doesNotMatch(taskStream, /rounded-lg border border-border p-3/)
})

test('opinion monitor projects configured notification channels without hardcoding Feishu', async () => {
  const [dashboard, channelLabels] = await Promise.all([
    read('app/(app)/dashboard/page.tsx'),
    read('lib/notification-channels.ts'),
  ])

  assert.match(dashboard, /通知日志/)
  assert.match(dashboard, /来自通知日志，非实时回执/)
  assert.match(dashboard, /无发送记录/)
  assert.match(dashboard, /\(item\.notification_channels \?\? \[\]\)\.map\(notificationChannelLabel\)/)
  assert.match(dashboard, /已有发送记录/)
  assert.match(dashboard, /grid items-start gap-4 lg:grid-cols-\[280px_1fr\]/)
  assert.doesNotMatch(dashboard, /飞书通知日志/)
  assert.doesNotMatch(dashboard, /飞书已记录发送/)
  for (const label of ['飞书', 'QQ', '微信', '企业微信', '邮件', 'Webhook']) {
    assert.match(channelLabels, new RegExp(label))
  }
})

test('dashboard restores the next schedule countdown from backend next_run_at', async () => {
  const [dashboard, hooks, matrixClock] = await Promise.all([
    read('app/(app)/dashboard/page.tsx'),
    read('lib/api/hooks.ts'),
    read('components/monitor/matrix-clock.tsx'),
  ])

  assert.match(dashboard, /function NextRunCountdown/)
  assert.match(dashboard, /window\.setInterval\(tick, 1_000\)/)
  assert.match(dashboard, /nextSchedule\?\.next_run_at/)
  assert.match(dashboard, />下次执行</)
  assert.match(dashboard, /<MatrixClock \/>[\s\S]*实时/)
  assert.match(matrixClock, /const DIGITS =/)
  assert.match(matrixClock, /role="timer"/)
  assert.match(matrixClock, /当前时间/)
  assert.match(matrixClock, /window\.setInterval\(update, 1_000\)/)
  assert.match(
    hooks,
    /queryKey:\s*\[["']schedules["'],\s*params\][\s\S]*?refetchInterval:\s*30_000/,
  )
})

test('failure triage deep-links from dashboard to filtered and authoritative task context', async () => {
  const [dashboard, taskStream, taskGrouping, tasksPage, taskDetailPage] = await Promise.all([
    read('app/(app)/dashboard/page.tsx'),
    read('components/monitor/task-stream.tsx'),
    read('lib/monitor/task-grouping.ts'),
    read('app/(app)/tasks/page.tsx'),
    read('app/(app)/tasks/[id]/page.tsx'),
  ])

  assert.match(dashboard, /href: `\/tasks\/\$\{r\.task_id\}`/)
  assert.match(dashboard, /href: task\.href/)
  assert.match(dashboard, /hasAttention \? '\/tasks\?status=failed' : '\/tasks'/)
  assert.match(taskStream, /href="\/tasks\?status=failed"/)
  assert.match(taskStream, /hasUnlistedFailures = totalFailed > 0/)
  assert.match(taskStream, /这些任务不在最近 10 条运行中/)
  assert.match(taskStream, /hasUnlistedFailures \? '\/tasks\?status=failed' : '\/tasks'/)
  assert.match(taskStream, /<Link href=\{t\.href\}/)
  assert.match(taskStream, /<Link href=\{f\.href\}/)
  assert.match(taskGrouping, /\[task\.href \?\? '', task\.title, task\.lane, task\.workerName, task\.phase\]/)
  assert.match(taskGrouping, /\[failure\.href \?\? '', failure\.title, failure\.workerName, failure\.error\]/)
  assert.match(tasksPage, /useSearchParams\(\)/)
  assert.match(tasksPage, /normalizeTaskStatus\(searchParams\.get\('status'\)\)/)
  assert.match(tasksPage, /queryForTaskStatus\(searchParams\.toString\(\), nextStatus\)/)
  assert.match(tasksPage, /router\.replace\(pathWithQuery\(pathname, query\), \{ scroll: false \}\)/)
  assert.match(tasksPage, /useTasks\(\{[\s\S]*page,[\s\S]*limit: TASKS_PER_PAGE/)
  assert.match(tasksPage, /aria-pressed=\{status === f\.key\}/)
  assert.match(tasksPage, /暂无\$\{activeFilter\.label\}任务/)
  assert.match(tasksPage, /normalizeTaskPage\(searchParams\.get\('page'\)\)/)
  assert.match(tasksPage, /queryForTaskPage\(searchParams\.toString\(\), nextPage\)/)
  assert.match(tasksPage, /aria-label="任务分页"/)
  assert.match(tasksPage, /disabled=\{currentPage >= totalPages\}/)
  assert.match(tasksPage, /taskDetailPath\(t\.id, returnTo\)/)
  assert.match(taskDetailPage, /normalizeTaskReturnPath\(typeof query\.returnTo === 'string' \? query\.returnTo : null\)/)
  assert.match(taskDetailPage, /<Link href=\{returnTo\}/)
})

