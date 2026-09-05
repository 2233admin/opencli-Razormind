export async function installActionCenterFixtures(page) {
  const taskRequests = []
  const controlRequests = []
  const paginated = (data) => ({
    success: true,
    data,
    meta: { total: data.length, page: 1, pages: 1, limit: 100 },
  })
  const controlActions = Array.from({ length: 24 }, (_, index) => ({
    id: `control-e2e-${index}`,
    source_id: `source-e2e-${index}`,
    action_type: 'pause_collection',
    reason: `E2E evidence ${index}`,
    state: 'pending',
    mode: 'advisory',
    executed: false,
    outcome: null,
    created_at: '2026-08-29T00:00:00Z',
  }))

  await page.route('**/api/v1/tasks*', (route) => {
    const status = new URL(route.request().url()).searchParams.get('status')
    taskRequests.push(status)
    const tasks =
      status === 'failed'
        ? [
            {
              id: 'task-failed-e2e',
              source_id: 'source-e2e-failed',
              source_name: 'Failed source',
              trigger_type: 'manual',
              priority: 1,
              status: 'failed',
              created_at: '2026-08-29T00:00:00Z',
              updated_at: '2026-08-29T00:00:00Z',
            },
          ]
        : status === 'pending'
          ? [
              {
                id: 'task-pending-e2e',
                source_id: 'source-e2e-pending',
                source_name: 'Pending source',
                trigger_type: 'scheduled',
                priority: 1,
                status: 'pending',
                created_at: '2026-08-29T00:00:00Z',
                updated_at: '2026-08-29T00:00:00Z',
              },
            ]
          : []
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(paginated(tasks)) })
  })
  await page.route('**/api/v1/notifications/logs*', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(paginated([])) }),
  )
  await page.route('**/api/v1/notifications/rules', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: [] }) }),
  )
  await page.route('**/api/v1/governance/workspaces', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: [] }) }),
  )
  await page.route('**/api/v1/control/actions*', (route) => {
    const query = new URL(route.request().url()).searchParams
    controlRequests.push({
      source_id: query.get('source_id'),
      mode: query.get('mode'),
      outcome: query.get('outcome'),
      page: query.get('page'),
      limit: query.get('limit'),
    })
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(paginated(controlActions)),
    })
  })

  return { controlRequests, taskRequests }
}

export async function installFailedTaskDetailFixtures(page) {
  const taskDetailRequests = []
  const runRequests = []
  const eventRequests = []
  const task = {
    id: 'task-failed-e2e',
    source_id: 'source-e2e-failed',
    source_name: 'Failed source',
    trigger_type: 'manual',
    parameters: {},
    priority: 1,
    status: 'failed',
    error_message: 'E2E collection failure',
    created_at: '2026-08-29T00:00:00Z',
    updated_at: '2026-08-29T00:01:00Z',
  }
  const run = {
    id: 'run-failed-e2e',
    task_id: task.id,
    status: 'failed',
    worker_id: 'worker-e2e',
    started_at: '2026-08-29T00:00:00Z',
    finished_at: '2026-08-29T00:01:00Z',
    duration_ms: 60000,
    records_collected: 3,
    error_message: task.error_message,
    created_at: '2026-08-29T00:00:00Z',
  }
  const event = {
    id: 'event-failed-e2e',
    run_id: run.id,
    level: 'error',
    step: 'collect',
    message: 'E2E collection failure',
    created_at: '2026-08-29T00:01:00Z',
  }

  await page.route(`**/api/v1/tasks/${task.id}`, (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ detail: 'Only GET is supported by this fixture' }) })
    }
    taskDetailRequests.push(route.request().url())
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: task }) })
  })
  await page.route(`**/api/v1/tasks/${task.id}/runs`, (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ detail: 'Only GET is supported by this fixture' }) })
    }
    runRequests.push(route.request().url())
    return route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: [run], meta: { total: 1, page: 1, pages: 1, limit: 20 } }),
    })
  })
  await page.route(`**/api/v1/tasks/${task.id}/runs/${run.id}/events`, (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ detail: 'Only GET is supported by this fixture' }) })
    }
    eventRequests.push(route.request().url())
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: [event] }) })
  })
  await page.route(`**/api/v1/sources/${task.source_id}`, (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({ status: 405, contentType: 'application/json', body: JSON.stringify({ detail: 'Only GET is supported by this fixture' }) })
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: { id: task.source_id, name: task.source_name } }) })
  })

  return { taskDetailRequests, runRequests, eventRequests }
}

export async function installNotificationRuleCrudFixtures(page) {
  const rules = []
  const deleteRequests = []

  await page.route('**/api/v1/notifications/rules', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: rules }) })
    }
    if (route.request().method() !== 'POST') {
      return route.fulfill({
        status: 405,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Only GET and POST are supported by this fixture' }),
      })
    }

    const input = route.request().postDataJSON()
    const rule = {
      id: `rule-e2e-${rules.length + 1}`,
      name: input.name,
      source_id: input.source_id ?? undefined,
      trigger_event: input.trigger_event,
      notifier_type: input.notifier_type,
      notifier_config: input.notifier_config,
      enabled: input.enabled,
      created_at: '2026-08-29T00:00:00Z',
      updated_at: '2026-08-29T00:00:00Z',
    }
    rules.push(rule)
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: rule }) })
  })
  await page.route('**/api/v1/notifications/rules/*', (route) => {
    if (route.request().method() !== 'DELETE') {
      return route.fulfill({
        status: 405,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Only DELETE is supported by this fixture' }),
      })
    }
    const id = route.request().url().split('/').at(-1)
    deleteRequests.push({ id, method: route.request().method() })
    const rule = rules.find((candidate) => candidate.id === id)
    if (rule?.name === 'Delete failure rule') {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'E2E delete failure' }),
      })
    }
    const index = rules.findIndex((candidate) => candidate.id === id)
    if (index >= 0) rules.splice(index, 1)
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: null }) })
  })
  await page.route('**/api/v1/sources*', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ success: true, data: [] }) }),
  )

  return { deleteRequests }
}

export async function installControlLedgerFailureRecoveryFixtures(page) {
  const requests = []
  let recovered = false
  let releaseRecoveryResponse
  const recoveryResponse = new Promise((resolve) => {
    releaseRecoveryResponse = resolve
  })

  await page.route('**/api/v1/control/actions*', (route) => {
    if (route.request().method() !== 'GET') {
      return route.fulfill({
        status: 405,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Only GET is supported by this fixture' }),
      })
    }
    const url = new URL(route.request().url())
    requests.push({
      method: route.request().method(),
      source_id: url.searchParams.get('source_id'),
      mode: url.searchParams.get('mode'),
      outcome: url.searchParams.get('outcome'),
      page: url.searchParams.get('page'),
      limit: url.searchParams.get('limit'),
    })
    if (!recovered) {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'E2E control ledger failure' }),
      })
    }
    return recoveryResponse.then(() =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: [], meta: { total: 0, page: 3, pages: 0, limit: 11 } }),
      }),
    )
  })

  return {
    recover: () => { recovered = true },
    releaseRecoveryResponse: () => releaseRecoveryResponse(),
    requests,
  }
}
