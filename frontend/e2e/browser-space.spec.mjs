import { expect, test } from '@playwright/test'

const workspace = {
  id: 'workspace-1',
  name: 'Research',
  slug: 'research',
  active: true,
  created_at: '',
  updated_at: '',
}
const instance = {
  id: 'instance-1',
  label: 'Chromium slot A',
  granted_capabilities: ['snapshot'],
}

function browserSpace(overrides = {}) {
  return {
    id: 'space-1',
    workspace_id: workspace.id,
    browser_instance_id: instance.id,
    binding_id: null,
    owner_type: 'operator',
    owner_id: 'agent-7',
    status: 'idle',
    granted_capabilities: ['snapshot'],
    revision: 1,
    last_error_code: null,
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

function browserTask(status, result) {
  const task = {
    id: 'task-1',
    operation_id: 'op-1',
    capability: 'snapshot',
    status,
  }
  if (result !== undefined) task.result = result
  return task
}

async function mockBrowserSpaces(page, options = {}) {
  const state = {
    spaces: options.spaces ?? [],
    events: options.events ?? [],
    createError: options.createError,
    submitError: options.submitError,
  }
  await page.addInitScript(() => sessionStorage.setItem('opencli.bootstrapIdentityToken', 'test-identity'))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    function json(data, status = 200) {
      return route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify({ data }),
      })
    }
    if (url.pathname === '/api/v1/auth/me') {
      return json({
        subject: 'test-agent',
        email: null,
        name: 'Test Agent',
        username: null,
        picture: null,
        is_platform_admin: true,
        auth_method: 'development',
      })
    }
    if (url.pathname === '/api/v1/workspaces') return json([workspace])
    if (url.pathname.endsWith('/browser-spaces/instances')) return json([{ id: instance.id, capabilities: ['snapshot'] }])
    if (url.pathname.endsWith('/browser-spaces') && request.method() === 'GET') {
      return json(state.spaces)
    }
    if (url.pathname.endsWith('/browser-spaces') && request.method() === 'POST') {
      if (state.createError) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ error: state.createError }),
        })
      }
      state.spaces = [browserSpace()]
      return json(state.spaces[0], 201)
    }
    if (url.pathname.endsWith('/space-1') && request.method() === 'GET') {
      const space = state.spaces[0]
      return json(space ? { ...space, latest_task: space.latest_task ?? null, active_task: space.latest_task?.status === 'running' ? space.latest_task : null } : null)
    }
    if (url.pathname.endsWith('/space-1/tasks')) {
      if (state.submitError) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ error: state.submitError }),
        })
      }
      state.spaces[0].latest_task = browserTask('completed', {
        title: 'safe page',
        cookies: '[redacted]',
      })
      state.events = [{
        id: 'event-1',
        sequence: 1,
        kind: 'completed',
        payload: { title: 'safe page', authorization: '[redacted]' },
        created_at: '2026-01-01T00:00:00Z',
      }]
      return json({
        space_id: 'space-1',
        task_id: 'task-1',
        operation_id: 'op-1',
        capability: 'snapshot',
        status: 'completed',
        result: state.spaces[0].latest_task.result,
        error: null,
      })
    }
    if (url.pathname.endsWith('/space-1/cancel')) {
      state.spaces[0].latest_task = browserTask('cancelled')
      return json({
        space_id: 'space-1',
        task_id: 'task-1',
        operation_id: 'op-1',
        capability: 'snapshot',
        status: 'cancelled',
        result: null,
        error: null,
      })
    }
    if (url.pathname.endsWith('/space-1/close')) {
      state.spaces[0].status = 'closed'
      return json(state.spaces[0])
    }
    if (url.pathname.endsWith('/space-1/events')) return json(state.events)
    return route.fallback()
  })
}

test('creates a space only from an allowed existing browser instance', async ({ page }) => {
  await mockBrowserSpaces(page)
  await page.goto('/browsers')
  await page.getByLabel('BrowserInstance ID').selectOption('instance-1')
  await page.getByLabel('Owner ID').fill('agent-7')
  await page.getByRole('button', { name: '创建 Browser Space' }).click()
  await expect(page.getByText('Browser Space 已创建')).toBeVisible()
  await expect(page.locator('p').filter({ hasText: /^instance-1$/ })).toBeVisible()
})

test('submits a granted capability and redacts sensitive result fields', async ({ page }) => {
  await mockBrowserSpaces(page, { spaces: [browserSpace()] })
  await page.goto('/browsers')
  await page.getByRole('button', { name: '提交任务' }).click()
  await expect(page.getByText('safe page').first()).toBeVisible()
  await expect(page.getByText('[redacted]')).toHaveCount(2)
  await expect(page.getByText('must-not-render')).toHaveCount(0)
})

test('shows the instance ownership typed error without a fallback', async ({ page }) => {
  await mockBrowserSpaces(page, { createError: 'browser_instance_in_use' })
  await page.goto('/browsers')
  await page.getByLabel('BrowserInstance ID').selectOption('instance-1')
  await page.getByLabel('Owner ID').fill('agent-7')
  await page.getByRole('button', { name: '创建 Browser Space' }).click()
  await expect(page.locator('p[role="alert"]')).toContainText('browser_instance_in_use')
})

test('confirms cancellation and close before sending lifecycle actions', async ({ page }) => {
  await mockBrowserSpaces(page, {
    spaces: [browserSpace({
      status: 'running',
      latest_task: browserTask('running'),
    })],
  })
  await page.goto('/browsers')
  await page.getByRole('button', { name: '取消当前任务' }).click()
  await page.getByRole('button', { name: '确认取消' }).click()
  await expect(page.getByText('已请求取消任务')).toBeVisible()
  await page.getByRole('button', { name: '关闭 Space' }).click()
  await page.getByRole('button', { name: '确认关闭' }).click()
  await expect(page.getByText('Browser Space 已关闭')).toBeVisible()
})
