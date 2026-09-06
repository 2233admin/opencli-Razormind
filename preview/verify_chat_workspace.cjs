const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium, expect } = require('D:/projects/opencli-openalice-reports-20260906/frontend/node_modules/@playwright/test')

// This verifier only attaches to the already-running isolated preview. The
// credentials file is intentionally outside this worktree and is never logged.
const credentialsPath = 'D:/projects/opencli-Razormind/.git/openalice-dispatch-20260906/preview/credentials.json'
const credentials = JSON.parse(fs.readFileSync(credentialsPath, 'utf8').replace(/^\uFEFF/, ''))
const base = 'http://127.0.0.1:8047'
const workspace = 'preview-studio-workspace'
const project = 'preview-studio-project'
const workflow = 'preview-studio-workflow'
const conversation = 'preview-saved-conversation'

function chatUrl(extra = '') {
  const query = new URLSearchParams({ workspace, project, workflow })
  if (extra) query.set('conversation', extra)
  return `${base}/chat?${query.toString()}`
}

(async () => {
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  try {
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' })
    await page.waitForFunction(() => (
      window.location.pathname === '/login'
      || Boolean(document.querySelector('[aria-label="Ask Alice 主对话"]'))
    ), undefined, { timeout: 45_000 })
    const login = page.locator('#local-username')
    if (await login.isVisible().catch(() => false)) {
      await login.fill(credentials.username)
      await page.locator('#local-password').fill(credentials.password)
      await page.getByRole('button', { name: '登录', exact: true }).click()
      await page.waitForURL('**/dashboard**', { timeout: 45_000 })
    }
    await expect(page.getByRole('heading', { name: 'Ask Alice', exact: true }).first()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByLabel('Ask Alice 主对话', { exact: true }).last()).toBeVisible()
    await page.screenshot({ path: path.join(__dirname, 'chat-dashboard.png') })

    await page.goto(chatUrl(), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Ask Alice', exact: true }).first()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByLabel('Ask Alice 主对话', { exact: true }).last()).toBeVisible()
    await expect(page.getByText('对话可调用工具', { exact: true })).toBeVisible()
    await expect(page.getByText('当前服务还未返回工具目录，下面是已知聊天契约摘要；不会因此扩大实际调用范围。', { exact: true })).toBeVisible()
    await expect(page.getByText('尚未配置可用模型', { exact: true })).toBeVisible()
    await page.screenshot({ path: path.join(__dirname, 'chat-workspace.png') })

    await page.goto(chatUrl('does-not-exist'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('指定的 Agent 会话不存在，或你无权访问。没有打开其他会话。', { exact: true })).toBeVisible({ timeout: 30_000 })
    assert.equal(new URL(page.url()).searchParams.get('conversation'), 'does-not-exist')

    await page.goto(chatUrl(conversation), { waitUntil: 'domcontentloaded' })
    const sessionSelect = page.getByLabel('选择 Agent 会话')
    await expect(sessionSelect).toHaveValue(conversation, { timeout: 30_000 })
    await expect(page.getByText('项目', { exact: true }).first()).toBeVisible()
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(sessionSelect).toHaveValue(conversation, { timeout: 30_000 })
    await expect(page.getByText('工作流草稿', { exact: true }).first()).toBeVisible()

    await page.getByRole('button', { name: '新建 Agent 会话', exact: true }).click()
    await expect(sessionSelect).toHaveValue('', { timeout: 5_000 })
    await page.goto(chatUrl(), { waitUntil: 'domcontentloaded' })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByLabel('选择 Agent 会话')).toHaveValue('', { timeout: 30_000 })

    await page.route('**/api/v1/dashboard/stats**', (route) => route.abort())
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' })
    await expect(page.getByLabel('Ask Alice 主对话', { exact: true }).last()).toBeVisible({ timeout: 30_000 })
    await expect(page.getByRole('button', { name: '重新连接', exact: true })).toBeVisible({ timeout: 30_000 })
    await page.screenshot({ path: path.join(__dirname, 'chat-dashboard-stats-error.png') })
    await page.unroute('**/api/v1/dashboard/stats**')

    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto(chatUrl(conversation), { waitUntil: 'domcontentloaded' })
    await expect(page.getByLabel('Ask Alice 主对话', { exact: true }).last()).toBeVisible({ timeout: 30_000 })
    const dimensions = await page.evaluate(() => ({
      viewport: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }))
    assert(dimensions.scrollWidth <= dimensions.viewport + 1, `mobile overflow: ${JSON.stringify(dimensions)}`)
    await page.screenshot({ path: path.join(__dirname, 'chat-mobile.png') })

    await page.goto(`${base}/chat?workspace=workspace-that-is-not-authorized`, { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('该工作区不在你的授权范围内。请选择一个可用工作区后再继续。', { exact: true })).toBeVisible({ timeout: 30_000 })

    assert.deepEqual(pageErrors, [])
    console.log(JSON.stringify({
      passed: true,
      checks: [
        'dashboard composer',
        'chat route and real tool reference',
        '404 tool catalog explicit compatibility state',
        'missing model configuration state',
        'invalid conversation state',
        'selected conversation context and reload recovery',
        'new conversation reset and reload recovery',
        'dashboard stats failure keeps composer visible',
        '375px viewport bounds',
        'unauthorized workspace message',
        'no page errors',
      ],
      screenshots: ['chat-dashboard.png', 'chat-workspace.png', 'chat-dashboard-stats-error.png', 'chat-mobile.png'],
    }))
  } finally {
    await context.close()
    await browser.close()
  }
})().catch((error) => {
  console.error(error.message)
  process.exitCode = 1
})
