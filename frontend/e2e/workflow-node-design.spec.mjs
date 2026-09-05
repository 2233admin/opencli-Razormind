import { expect, test } from '@playwright/test'

const api = (path) => `**/api/v1${path}`

async function authenticateWorkflowPage(page) {
  await page.route(api('/auth/login'), (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data: { access_token: 'workflow-node-e2e', token_type: 'bearer', using_default_password: false } }),
  }))
  await page.route(api('/auth/me'), (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data: { subject: 'workflow-node-e2e', name: 'Workflow node E2E', is_platform_admin: true, auth_method: 'password' } }),
  }))
  await page.route('**/api/workflow/capabilities', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ success: true, data: { version: 'workflow-node-e2e', catalog: [], primitives: [], channels: [], notifiers: [], triggers: [], resources: [] } }),
  }))
  await page.goto('/login?returnTo=%2Fprototype%2Fworkflow-studio')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/prototype\/workflow-studio(?:\?.*)?$/)
  await page.getByRole('button', { name: '节点工作流' }).click()
}

async function geometrySnapshot(node, ports) {
  const geometry = await node.evaluate((element) => {
    const style = getComputedStyle(element)
    const wrapper = element.closest('.react-flow__node')
    const viewport = element.closest('.react-flow__viewport')
    const transform = viewport ? getComputedStyle(viewport).transform : 'none'
    const viewportScale = transform === 'none' ? 1 : Number(transform.match(/^matrix\(([^,]+)/)?.[1] ?? 1)
    const renderedWidth = element.getBoundingClientRect().width
    const wrapperRenderedWidth = wrapper?.getBoundingClientRect().width ?? Number.NaN
    return {
      computedWidth: Number.parseFloat(style.width),
      computedHeight: Number.parseFloat(style.height),
      unscaledRenderedWidth: Number((renderedWidth / viewportScale).toFixed(0)),
      unscaledWrapperWidth: Number((wrapperRenderedWidth / viewportScale).toFixed(0)),
    }
  })
  const handles = await ports.evaluateAll((elements) => elements.map((element) => {
    const nodeRoot = element.closest('[data-workflow-node="true"]')
    if (!nodeRoot) throw new Error('Port handle is detached from its workflow node')
    const nodeRect = nodeRoot.getBoundingClientRect()
    const handleRect = element.getBoundingClientRect()
    const normalized = (value) => Number(value.toFixed(4))
    return {
      x: normalized((handleRect.x - nodeRect.x) / nodeRect.width),
      y: normalized((handleRect.y - nodeRect.y) / nodeRect.height),
      width: normalized(handleRect.width / nodeRect.width),
      height: normalized(handleRect.height / nodeRect.height),
    }
  }))
  return { geometry, handles }
}

async function addCatalogNode(page, label) {
  await page.getByRole('button', { name: '添加节点' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'EN', exact: true }).click()
  const search = page.getByRole('dialog').getByRole('textbox', { name: 'Search node picker' })
  await search.fill(label)
  await search.press('Enter')
}

test('workflow node design keeps actual canvas geometry, ports, density, states, and Inspector image actions observable', async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await authenticateWorkflowPage(page)

  const canvas = page.locator('[data-zoom-bucket]')
  const node = page.locator('[data-workflow-node="true"]').first()
  await expect(node).toBeVisible()
  await expect(canvas).toHaveAttribute('data-zoom-bucket', 'high')
  await expect(canvas).toHaveAttribute('data-wiring-state', 'idle')

  const ports = node.locator('[data-port-id]')
  const highSnapshot = await geometrySnapshot(node, ports)
  expect(highSnapshot.geometry.computedWidth).toBe(240)
  expect(highSnapshot.geometry.unscaledRenderedWidth).toBe(240)
  expect(highSnapshot.geometry.unscaledWrapperWidth).toBe(240)
  expect(highSnapshot.geometry.computedHeight).toBeGreaterThanOrEqual(96)
  expect(highSnapshot.handles.length).toBeGreaterThan(0)
  const port = ports.first()
  await port.focus()
  await expect(port).toHaveAttribute('aria-label', /· (?:输入|输出) · .+ · .+ · .+/)
  await expect(node.locator('.workflow-port-name').first()).toHaveCSS('opacity', '1')

  await page.evaluate(() => {
    window.__workflowPortMenus = []
    window.addEventListener('opencli:workflow-port-menu', (event) => window.__workflowPortMenus.push(event.detail))
  })
  await port.press('Shift+F10')
  await expect.poll(() => page.evaluate(() => window.__workflowPortMenus.length)).toBe(1)
  await expect.poll(() => page.evaluate(() => window.__workflowPortMenus[0])).toMatchObject({
    nodeId: await node.getAttribute('data-node-id'),
    handleId: await port.getAttribute('data-port-id'),
  })

  const snapshots = new Map([['high', highSnapshot]])
  const zoomOut = page.getByRole('button', { name: 'Zoom Out' })
  for (let attempt = 0; attempt < 16 && !snapshots.has('low'); attempt += 1) {
    await zoomOut.click()
    await page.waitForTimeout(40)
    const bucket = await canvas.getAttribute('data-zoom-bucket')
    if (bucket === 'mid' || bucket === 'low') {
      snapshots.set(bucket, await geometrySnapshot(node, ports))
    }
  }
  expect([...snapshots.keys()]).toEqual(['high', 'mid', 'low'])
  for (const bucket of ['mid', 'low']) {
    expect(snapshots.get(bucket)).toEqual(highSnapshot)
  }

  const zoomIn = page.getByRole('button', { name: 'Zoom In' })
  for (let attempt = 0; attempt < 16 && await canvas.getAttribute('data-zoom-bucket') !== 'high'; attempt += 1) {
    await zoomIn.click()
    await page.waitForTimeout(40)
  }
  await expect(canvas).toHaveAttribute('data-zoom-bucket', 'high')
  expect(await geometrySnapshot(node, ports)).toEqual(highSnapshot)

  for (let attempt = 0; attempt < 16 && await canvas.getAttribute('data-zoom-bucket') !== 'low'; attempt += 1) {
    await zoomOut.click()
    await page.waitForTimeout(40)
  }
  await expect(canvas).toHaveAttribute('data-zoom-bucket', 'low')
  await page.getByRole('button', { name: '更多工具' }).click()
  await page.getByText('画布设置', { exact: true }).click()
  const settingsPanel = page.getByLabel('交互设置')
  const contextualZoom = settingsPanel.getByText('contextualZoom', { exact: true }).locator('xpath=../..').getByRole('switch')
  await contextualZoom.click()
  await expect(canvas).toHaveAttribute('data-zoom-bucket', 'high')
  expect(await geometrySnapshot(node, ports)).toEqual(highSnapshot)
  await contextualZoom.click()
  await expect(canvas).toHaveAttribute('data-zoom-bucket', 'low')
  expect(await geometrySnapshot(node, ports)).toEqual(snapshots.get('low'))

  // CSS-composition fixture: the imported workflow has no deterministic selected+error+locked node.
  await node.evaluate((element) => {
    element.dataset.testFixture = 'selected-error-locked-css'
    element.dataset.selected = 'true'
    element.dataset.status = 'error'
    element.dataset.packageState = 'locked'
  })
  await expect(node.locator('.workflow-node-surface')).toBeVisible()
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await testInfo.attach('workflow-node-light-selected-error-locked.png', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })

  await canvas.evaluate((element) => { element.dataset.wiringState = 'wiring' })
  await expect(node.locator('.workflow-port-name').first()).toHaveCSS('opacity', '1')
  await canvas.evaluate((element) => { element.dataset.wiringState = 'reconnecting' })
  await expect(node.locator('.workflow-port-name').first()).toHaveCSS('opacity', '1')

  await addCatalogNode(page, 'Image Generation')
  const imageNode = page.locator('[data-workflow-node="true"]').filter({ hasText: 'Image Generation' }).last()
  await expect(imageNode).toBeVisible()
  await imageNode.click()
  await page.getByRole('button', { name: 'Configure' }).click()
  const generationAction = page.getByRole('link', { name: /Create Image Canvas|Open Image Studio/ })
  await expect(generationAction).toBeVisible()
  const generationUrl = new URL(await generationAction.getAttribute('href'), page.url())
  expect(generationUrl.pathname).toBe('/studio/workflow/image')
  expect(generationUrl.searchParams.get('node')).toBe(await imageNode.getAttribute('data-node-id'))
  expect(generationUrl.searchParams.has('workspace')).toBe(true)
  expect(generationUrl.searchParams.has('project')).toBe(true)
  expect(generationUrl.searchParams.has('document')).toBe(true)

  await addCatalogNode(page, 'Image Asset')
  const assetNode = page.locator('[data-workflow-node="true"]').filter({ hasText: 'Image Asset' }).last()
  await expect(assetNode).toBeVisible()
  await assetNode.click()
  await page.getByRole('button', { name: 'Configure' }).click()
  const assetAction = page.getByRole('link', { name: 'Select Workspace Assets' })
  await expect(assetAction).toBeVisible()
  const assetUrl = new URL(await assetAction.getAttribute('href'), page.url())
  expect(assetUrl.searchParams.get('node')).toBe(await assetNode.getAttribute('data-node-id'))
  expect(assetUrl.searchParams.get('mode')).toBe('gallery')
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await testInfo.attach('workflow-node-dark-inspector-image-actions.png', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })
})
