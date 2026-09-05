import { expect, test } from '@playwright/test'

const TOKEN_KEY = 'opencli.bootstrapIdentityToken'

const identity = {
  subject: 'route-motion-e2e',
  email: null,
  name: 'Route Motion E2E',
  username: 'route-motion-e2e',
  picture: null,
  is_platform_admin: true,
  auth_method: 'bootstrap',
}

const systemConfig = {
  app_name: 'OpenCLI Admin',
  app_env: 'test',
  debug: false,
  collection_mode: 'local',
  collection_orchestrator: 'admin',
  task_executor: 'local',
  local_max_concurrent_pipelines: 2,
  opencli_timeout: 300,
  default_timezone: 'Asia/Shanghai',
  public_url: 'http://127.0.0.1:8030',
  fleet_network_provider: 'lan',
  netbird_mode: 'off',
  opencli_cdp_endpoint: 'http://127.0.0.1:9222',
  agent_pool_endpoints: [],
  effective_cdp_endpoints: [],
  llm_request_timeout_seconds: 60,
  llm_max_concurrency: 2,
  control_mode: 'advisory',
  control_kill_switch: false,
  image_tag: '0.4.1',
  database_kind: 'sqlite',
  api_auth_configured: true,
  oidc_configured: false,
  smtp_configured: false,
  credential_encryption_configured: true,
}

const sidebarEntries = [
  { label: '概览', href: '/dashboard' },
  { label: '任务与通知', href: '/inbox?tab=pending' },
  { label: '项目', href: '/studio' },
  { label: '插件中心', href: '/plugins' },
  { label: '自动化与智能体', href: '/operations-agents' },
  { label: '成果与数据', href: '/records' },
  { label: '执行资源', href: '/nodes' },
  { label: '模型与连接', href: '/providers' },
  { label: '控制与安全', href: '/control/kill-switch' },
  { label: '系统与运维', href: '/system' },
]

const response = (data) => ({
  contentType: 'application/json',
  body: JSON.stringify({ success: true, data }),
})

async function installApiFixtures(page) {
  await page.addInitScript(({ key }) => sessionStorage.setItem(key, 'route-motion-test-token'), { key: TOKEN_KEY })
  await page.addInitScript(() => {
    const calls = []
    const nativeAnimate = Element.prototype.animate
    const recordRouteAnimation = function (keyframes, options) {
      calls.push({
        targetBoundary: this.matches('[data-ssgoi-transition]')
          ? this.getAttribute('data-ssgoi-transition')
          : null,
        targetClass: this.className,
        duration: typeof options === 'object' ? options?.duration ?? null : options,
        easing: typeof options === 'object' ? options?.easing ?? null : null,
        keyframes,
      })
      return nativeAnimate.call(this, keyframes, options)
    }
    Element.prototype.animate = recordRouteAnimation
    if (HTMLElement.prototype.animate !== recordRouteAnimation) HTMLElement.prototype.animate = recordRouteAnimation
    globalThis.__routeMotionAnimationCalls = calls
    globalThis.__routeMotionAnimateHookActive = Element.prototype.animate === recordRouteAnimation
    globalThis.__routeMotionNavigationEvents = []
    window.navigation?.addEventListener('navigate', (event) => {
      globalThis.__routeMotionNavigationEvents.push({
        type: event.navigationType,
        from: window.navigation.currentEntry?.url,
        to: event.destination.url,
        fromIndex: window.navigation.currentEntry?.index,
        toIndex: event.destination.index,
      })
    })
  })
  await page.route('**/health', (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) }),
  )

  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) return route.fulfill(response(identity))
    if (path.endsWith('/system/config')) return route.fulfill(response(systemConfig))
    if (path.endsWith('/control/kill-switch')) {
      return route.fulfill(response({ engaged: false, runtime_override: null, config_default: false }))
    }
    if (path.endsWith('/control/odp-state')) {
      return route.fulfill(response({ ingest: {}, stream: {}, dlq: {}, store: {}, outbox: {} }))
    }
    if (path.includes('/control/advisory-report')) {
      return route.fulfill(response({
        buckets: [],
        totals: {
          total: 0,
          pending: 0,
          evaluated: 0,
          recovered: 0,
          persisted: 0,
          insufficient_data: 0,
          recovery_rate: null,
        },
        mode_breakdown: {},
        evaluation: {
          evaluated: 0,
          recovered: 0,
          persisted: 0,
          insufficient_data: 0,
          still_pending: 0,
        },
      }))
    }
    if (path.endsWith('/governance/workspaces')) {
      return route.fulfill(response([{
        id: 'route-motion-workspace',
        name: 'Route Motion Workspace',
        slug: 'route-motion-workspace',
        active: true,
        created_at: '2026-09-05T00:00:00Z',
        updated_at: '2026-09-05T00:00:00Z',
      }]))
    }
    if (path.endsWith('/dashboard/stats')) {
      return route.fulfill(response({
        sources: { total: 0, enabled: 0, disabled: 0 },
        tasks: { total: 0, running: 0, failed: 0 },
        runs: { total: 0, success: 0, failed: 0, success_rate: 0 },
        records: { total: 0, ai_processed: 0 },
        delivery: {
          attempts: 0,
          submitted: 0,
          awaiting_ack: 0,
          confirmed: 0,
          ack_failed: 0,
          submission_failed: 0,
          ack_not_required: 0,
          window: 'all',
          since: null,
          until: null,
        },
        recent_runs: [],
      }))
    }
    if (path.endsWith('/dashboard/opinion-monitor')) {
      return route.fulfill(response({
        window: { range: '7d', since: null, until: null },
        summary: {
          records: 0,
          ai_processed: 0,
          notification_sent: 0,
          notification_failed: 0,
          active_sources: 0,
          active_notification_rules: 0,
          active_notification_channels: [],
        },
        tags: [],
        sentiment: [],
        sources: [],
        recent: [],
      }))
    }
    return route.fulfill(response([]))
  })
}

async function assertShellReady(page) {
  await expect(page.locator('[data-slot="sidebar"]').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '当前视图无法显示', exact: true })).toHaveCount(0)
  await expect(page.locator('h1:visible').first()).toBeVisible()
  await expect(page.locator('[data-ssgoi-transition]')).toHaveCount(1)
}

async function captureMotion(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-ssgoi-transition]')
    const rootStyle = root ? getComputedStyle(root) : null
    const tokenStyle = getComputedStyle(document.documentElement)
    const animations = document.getAnimations({ subtree: true }).map((animation) => {
      const effect = animation.effect
      const timing = effect?.getComputedTiming?.()
      const keyframes = effect?.getKeyframes?.() ?? []
      const target = effect?.target
      const targetStyle = target instanceof Element ? getComputedStyle(target) : null
      const pseudoElement = effect?.pseudoElement ?? null
      const isNativeViewTransition = pseudoElement?.includes('view-transition') ?? false
      const targetBoundary = target instanceof Element
        ? target.matches('[data-ssgoi-transition]')
          ? target.getAttribute('data-ssgoi-transition')
          : target.closest('[data-ssgoi-transition]')?.getAttribute('data-ssgoi-transition') ?? null
        : null
      const keyframeProperties = [...new Set(keyframes.flatMap((frame) => Object.keys(frame)))].sort()
      const isBoundaryMotion = Boolean(
        target instanceof Element &&
          target.matches('[data-ssgoi-transition]') &&
          keyframeProperties.includes('transform') &&
          keyframeProperties.includes('opacity'),
      )
      return {
        animationName: targetStyle?.animationName ?? null,
        pseudoElement,
        targetTag: target instanceof Element ? target.tagName : null,
        targetClass: target instanceof Element ? target.className : null,
        targetBoundary,
        owner: isNativeViewTransition ? 'native-view-transition' : isBoundaryMotion ? 'route-adapter' : null,
        isRouteAnimation: isNativeViewTransition || isBoundaryMotion,
        durationMs: typeof timing?.duration === 'number' ? timing.duration : null,
        delayMs: typeof timing?.delay === 'number' ? timing.delay : null,
        easing: timing?.easing ?? null,
        iterations: timing?.iterations ?? null,
        currentTimeMs: typeof animation.currentTime === 'number' ? animation.currentTime : null,
        playState: animation.playState,
        pending: animation.pending,
        keyframeCount: keyframes.length,
        keyframeProperties,
        keyframeTransforms: keyframes.map((frame) => frame.transform).filter(Boolean),
      }
    })
    return {
      transitionKey: root?.getAttribute('data-ssgoi-transition') ?? null,
      rootAnimationName: rootStyle?.animationName ?? null,
      rootAnimationDuration: rootStyle?.animationDuration ?? null,
      rootTransitionDuration: rootStyle?.transitionDuration ?? null,
      responseToken: tokenStyle.getPropertyValue('--motion-duration-response').trim(),
      spatialToken: tokenStyle.getPropertyValue('--motion-duration-spatial').trim(),
      spatialEasing: tokenStyle.getPropertyValue('--motion-ease-spatial').trim(),
      animations,
      routeAnimations: animations.filter((animation) => animation.isRouteAnimation),
    }
  })
}

async function takeAnimationCalls(page) {
  return page.evaluate(() => {
    const calls = globalThis.__routeMotionAnimationCalls ?? []
    return calls.splice(0, calls.length)
  })
}

async function sampleDuringTransition(page) {
  const samples = []
  let previousMs = 0
  for (const waitMs of [0, 24, 72, 144, 260, 420]) {
    if (waitMs > previousMs) await page.waitForTimeout(waitMs - previousMs)
    samples.push(await captureMotion(page))
    previousMs = waitMs
  }
  return samples
}

function routeKey(href) {
  return new URL(href, 'http://route-motion.test').pathname
}

function routeUrlMatches(href) {
  const expected = new URL(href, 'http://route-motion.test')
  return (url) => {
    const actual = new URL(url)
    return actual.pathname === expected.pathname && actual.search === expected.search
  }
}

async function navigateFromSidebar(page, entry) {
  const link = page.locator('[data-slot="sidebar"]').first().getByRole('link', { name: entry.label, exact: true })
  await expect(link).toBeVisible()
  await Promise.all([
    page.waitForURL(routeUrlMatches(entry.href)),
    link.click(),
  ])
  await expect(page.locator('h1:visible').first()).toBeVisible()
}

async function navigateViaRouteTab(page, label, href, fromPath) {
  const link = page.getByRole('navigation', { name: '相关视图' }).getByRole('link', { name: label, exact: true })
  const availableTabs = await page.getByRole('navigation', { name: '相关视图' }).getByRole('link').allTextContents()
  await expect(link, `available related tabs while requesting ${label}: ${JSON.stringify(availableTabs)}`).toBeVisible()
  await Promise.all([
    page.waitForURL(routeUrlMatches(href), { timeout: 30_000 }),
    link.click(),
  ])
  await expect(page.locator('h1:visible').first()).toBeVisible()
  const samples = await sampleDuringTransition(page)
  const animationCalls = await takeAnimationCalls(page)
  const summary = summarizeSamples(samples)
  console.log(`[route-motion] tab ${href} ${JSON.stringify(summary)}`)
  assertRouteAdapterMeasurement(summary, animationCalls, fromPath, routeKey(href))
  await assertShellReady(page)
  await expect(page.locator('[data-ssgoi-transition]')).toHaveAttribute('data-ssgoi-transition', routeKey(href))
}

function summarizeSamples(samples) {
  const observed = samples.flatMap((sample) => sample.routeAnimations)
  const uniqueObserved = [...new Map(
    observed.map((animation) => [
      [
        animation.pseudoElement,
        animation.animationName,
        animation.durationMs,
        animation.delayMs,
        animation.keyframeCount,
        animation.keyframeProperties.join(','),
        animation.keyframeTransforms.join(','),
        animation.owner,
      ].join('|'),
      animation,
    ]),
  ).values()]
  return {
    tokenPair: [samples[0]?.responseToken, samples[0]?.spatialToken],
    spatialEasing: samples[0]?.spatialEasing,
    keys: [...new Set(samples.map((sample) => sample.transitionKey))],
    routeAnimationCount: uniqueObserved.length,
    adapterAnimationCount: uniqueObserved.filter((animation) => animation.owner === 'route-adapter').length,
    ownerKinds: [...new Set(uniqueObserved.map((animation) => animation.owner))],
    routeAnimationTimings: uniqueObserved.map((animation) => ({
      owner: animation.owner,
      pseudoElement: animation.pseudoElement,
      animationName: animation.animationName,
      targetClass: animation.targetClass,
      targetBoundary: animation.targetBoundary,
      durationMs: animation.durationMs,
      delayMs: animation.delayMs,
      easing: animation.easing,
      keyframeCount: animation.keyframeCount,
      keyframeProperties: animation.keyframeProperties,
      keyframeTransforms: animation.keyframeTransforms,
    })),
  }
}

function cssTimeToMs(value) {
  const match = /^(-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(ms|s)$/i.exec(value)
  if (!match) throw new Error(`unsupported CSS time token: ${value}`)
  return Number(match[1]) * (match[2] === 's' ? 1_000 : 1)
}

function normalizedCubicBezier(value) {
  expect(value).toMatch(/^cubic-bezier\(.+\)$/)
  return JSON.stringify(value.slice(13, -1).split(',').map(Number))
}

async function computedTransitionDurations(page, selector) {
  return page.locator(selector).evaluateAll((elements) =>
    elements.map((element) => getComputedStyle(element).transitionDuration),
  )
}

function assertTokenDuration(durations, expected) {
  expect(durations.length).toBeGreaterThan(0)
  const expectedMs = cssTimeToMs(expected)
  const matches = durations.every((duration) =>
    duration.split(',').every((item) => cssTimeToMs(item.trim()) === expectedMs),
  )
  expect(matches, `computed transition durations ${JSON.stringify(durations)} did not match ${expected}`).toBe(true)
}

function transformDistances(transforms) {
  return transforms.flatMap((transform) =>
    [...transform.matchAll(/translate3d\((-?[\d.]+)px/gu)].map((match) => Math.abs(Number(match[1]))),
  )
}

function assertAdapterCalls(calls, fromPath, toPath, summary) {
  expect(calls).toHaveLength(2)
  expect(new Set(calls.map((call) => call.targetBoundary))).toEqual(new Set([fromPath, toPath]))
  const spatialMs = cssTimeToMs(summary.tokenPair[1])
  expect(calls.every((call) => call.duration === spatialMs)).toBe(true)
  expect(calls.every((call) => normalizedCubicBezier(call.easing) === normalizedCubicBezier(summary.spatialEasing))).toBe(true)
  const transforms = calls.flatMap((call) =>
    (Array.isArray(call.keyframes) ? call.keyframes : [])
      .map((frame) => frame.transform)
      .filter(Boolean),
  )
  expect(transformDistances(transforms)).toContain(8)
}

function assertInboundDirection(summary, toPath, expectedDistance) {
  const inbound = summary.routeAnimationTimings.find(
    ({ owner, targetBoundary }) => owner === 'route-adapter' && targetBoundary === toPath,
  )
  expect(inbound).toBeTruthy()
  expect(inbound.keyframeTransforms).toContain(`translate3d(${expectedDistance}px, 0px, 0px)`)
}

function assertRouteAdapterMeasurement(summary, calls, fromPath, toPath, expectedDistance = 8) {
  expect(summary.keys).toEqual([toPath])
  // CSS optimizers may serialize 180ms as .18s; compare the actual values.
  expect(summary.tokenPair.map(cssTimeToMs)).toEqual([180, 320])
  expect(normalizedCubicBezier(summary.spatialEasing)).toBe('[0.32,0.72,0,1]')
  expect(summary.routeAnimationCount).toBeGreaterThan(0)
  expect(summary.ownerKinds).toEqual(['route-adapter'])
  // The incoming boundary remains in the document; the outgoing boundary may
  // be a detached clone, so its direct Element.animate call is authoritative.
  expect(summary.adapterAnimationCount).toBeGreaterThan(0)
  const adapterTimings = summary.routeAnimationTimings.filter(({ owner }) => owner === 'route-adapter')
  const spatialMs = cssTimeToMs(summary.tokenPair[1])
  expect(adapterTimings.every(({ durationMs }) => durationMs === spatialMs)).toBe(true)
  expect(adapterTimings.every(({ easing }) => normalizedCubicBezier(easing) === normalizedCubicBezier(summary.spatialEasing))).toBe(true)
  expect(transformDistances(adapterTimings.flatMap(({ keyframeTransforms }) => keyframeTransforms))).toContain(8)
  assertInboundDirection(summary, toPath, expectedDistance)
  assertAdapterCalls(calls, fromPath, toPath, summary)
}

test('sidebar route changes animate consistently in both directions with one persistent shell', async ({ page }) => {
  test.setTimeout(150_000)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)
  await page.goto('/dashboard')
  await assertShellReady(page)
  expect(await page.evaluate(() => globalThis.__routeMotionAnimateHookActive === true)).toBe(true)
  assertTokenDuration(await computedTransitionDurations(page, '[data-slot="sidebar"] a'), '0.18s')

  const shellHandle = await page.locator('[data-slot="sidebar"]').first().elementHandle()
  expect(shellHandle).toBeTruthy()
  await takeAnimationCalls(page)
  let currentPath = '/dashboard'
  const transitionSequence = [
    ...sidebarEntries.slice(1),
    ...sidebarEntries.slice(0, -1).reverse(),
  ]
  const measurements = []

  for (const entry of transitionSequence) {
    await navigateFromSidebar(page, entry)
    const samples = await sampleDuringTransition(page)
    const animationCalls = await takeAnimationCalls(page)
    const summary = summarizeSamples(samples)
    measurements.push({ href: entry.href, summary })
    console.log(`[route-motion] ${entry.href} ${JSON.stringify(summary)}`)

    assertRouteAdapterMeasurement(summary, animationCalls, currentPath, routeKey(entry.href))
    assertTokenDuration(await computedTransitionDurations(page, '[data-slot="sidebar"] a'), '0.18s')
    await expect(page.locator('[data-ssgoi-transition]')).toHaveCount(1)
    await expect(page.locator('[data-ssgoi-transition]')).toHaveAttribute('data-ssgoi-transition', routeKey(entry.href))
    await assertShellReady(page)
    expect(await shellHandle.evaluate((element) => element.isConnected)).toBe(true)
    currentPath = routeKey(entry.href)
  }

  expect(pageErrors, `page errors during route motion: ${pageErrors.join('; ')}`).toEqual([])
  expect(measurements).toHaveLength(18)
})

test('related view tabs use the same measured route adapter across sibling destinations', async ({ page }) => {
  // Twelve transitions plus five fresh page loads on the development server.
  test.setTimeout(150_000)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)

  const groups = [
    { start: '/records', links: [['关系图谱', '/records/graph'], ['数据表', '/records']] },
    { start: '/nodes', links: [['Worker', '/workers'], ['Chrome 池', '/browsers'], ['浏览器节点', '/nodes']] },
    { start: '/providers', links: [['Provider 目录', '/providers/catalog'], ['快速设置', '/providers']] },
    {
      start: '/control/kill-switch',
      links: [['建议报告', '/control/advisory-report'], ['ODP 状态', '/control/odp-state'], ['熔断开关', '/control/kill-switch']],
    },
    { start: '/agents', links: [['技能', '/skills'], ['自动化与智能体', '/operations-agents']] },
  ]

  for (const group of groups) {
    await page.goto(group.start)
    await assertShellReady(page)
    assertTokenDuration(
      await computedTransitionDurations(page, '[data-slot="sidebar"] a, nav[aria-label="相关视图"] a'),
      '0.18s',
    )
    await takeAnimationCalls(page)
    let currentPath = group.start
    for (const [label, href] of group.links) {
      await navigateViaRouteTab(page, label, href, currentPath)
      currentPath = routeKey(href)
      assertTokenDuration(
        await computedTransitionDurations(page, '[data-slot="sidebar"] a, nav[aria-label="相关视图"] a'),
        '0.18s',
      )
    }
  }

  expect(pageErrors, `page errors during related view motion: ${pageErrors.join('; ')}`).toEqual([])
})

test('query tabs keep the inbox transition boundary and do not start a route transition', async ({ page }) => {
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)
  await page.goto('/inbox?tab=pending')
  await assertShellReady(page)
  assertTokenDuration(
    await computedTransitionDurations(page, '[data-slot="sidebar"] a, nav[aria-label="相关视图"] a'),
    '0.18s',
  )
  const boundary = await page.locator('[data-ssgoi-transition]').elementHandle()
  const shell = await page.locator('[data-slot="sidebar"]').first().elementHandle()
  const tab = page.getByRole('navigation', { name: '相关视图' }).getByRole('link', { name: '工作项', exact: true })
  await takeAnimationCalls(page)

  await Promise.all([
    page.waitForURL(routeUrlMatches('/inbox?tab=tasks')),
    tab.click(),
  ])
  await expect(page.locator('h1:visible').first()).toBeVisible()
  const samples = await sampleDuringTransition(page)
  const summary = summarizeSamples(samples)
  const animationCalls = await takeAnimationCalls(page)
  console.log(`[route-motion] query-tab ${JSON.stringify(summary)}`)

  expect(summary.keys).toEqual(['/inbox'])
  expect(summary.routeAnimationCount, `query tab unexpectedly started a route transition: ${JSON.stringify(samples)}`).toBe(0)
  expect(animationCalls).toHaveLength(0)
  expect(await boundary.evaluate((element) => element.isConnected)).toBe(true)
  expect(await shell.evaluate((element) => element.isConnected)).toBe(true)
  expect(await page.locator('[data-ssgoi-transition]').count()).toBe(1)
  expect(pageErrors).toEqual([])
})

test('reduced motion disables route transitions while preserving the routed shell', async ({ page }) => {
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installApiFixtures(page)
  await page.goto('/dashboard')
  await assertShellReady(page)
  const reducedSidebarDurations = await computedTransitionDurations(page, '[data-slot="sidebar"] a')
  console.log(`[route-motion] reduced-sidebar-durations ${JSON.stringify(reducedSidebarDurations)}`)
  assertTokenDuration(reducedSidebarDurations, '0.00001s')
  const shell = await page.locator('[data-slot="sidebar"]').first().elementHandle()
  await takeAnimationCalls(page)

  await navigateFromSidebar(page, sidebarEntries[1])
  const samples = await sampleDuringTransition(page)
  const summary = summarizeSamples(samples)
  const animationCalls = await takeAnimationCalls(page)
  console.log(`[route-motion] reduced ${JSON.stringify(summary)}`)

  expect(summary.keys).toEqual(['/inbox'])
  expect(summary.routeAnimationCount).toBe(0)
  expect(animationCalls).toHaveLength(0)
  expect(await page.locator('[data-ssgoi-transition]').count()).toBe(1)
  expect(await shell.evaluate((element) => element.isConnected)).toBe(true)
  expect(pageErrors).toEqual([])
})

test('history back and forward retain the route adapter contract', async ({ page }) => {
  test.setTimeout(45_000)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)
  await page.goto('/dashboard')
  await assertShellReady(page)
  const shell = await page.locator('[data-slot="sidebar"]').first().elementHandle()
  await takeAnimationCalls(page)

  await navigateFromSidebar(page, sidebarEntries[2])
  await page.waitForTimeout(420)
  await takeAnimationCalls(page)

  await Promise.all([
    page.waitForURL(routeUrlMatches('/dashboard')),
    page.goBack(),
  ])
  await expect(page.locator('h1:visible').first()).toBeVisible()
  const backSummary = summarizeSamples(await sampleDuringTransition(page))
  const backCalls = await takeAnimationCalls(page)
  console.log(`[route-motion] history-back ${JSON.stringify(backSummary)}`)
  assertRouteAdapterMeasurement(backSummary, backCalls, '/studio', '/dashboard', -8)
  expect(await shell.evaluate((element) => element.isConnected)).toBe(true)

  await Promise.all([
    page.waitForURL(routeUrlMatches('/studio')),
    page.goForward(),
  ])
  await expect(page.locator('h1:visible').first()).toBeVisible()
  const forwardSummary = summarizeSamples(await sampleDuringTransition(page))
  const forwardCalls = await takeAnimationCalls(page)
  console.log(`[route-motion] history-forward ${JSON.stringify(forwardSummary)}`)
  console.log(`[route-motion] history-events ${JSON.stringify(await page.evaluate(() => globalThis.__routeMotionNavigationEvents))}`)
  assertRouteAdapterMeasurement(forwardSummary, forwardCalls, '/dashboard', '/studio')
  expect(await shell.evaluate((element) => element.isConnected)).toBe(true)
  expect(pageErrors).toEqual([])
})

test('rapid sidebar A-B-C navigation leaves one final route boundary', async ({ page }) => {
  test.setTimeout(45_000)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)
  await page.goto('/dashboard')
  await assertShellReady(page)
  const shell = await page.locator('[data-slot="sidebar"]').first().elementHandle()
  await takeAnimationCalls(page)
  await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
  await expect(page.locator('[data-slot="sidebar"]').first()).toHaveAttribute('data-collapsible', 'icon')
  await page.waitForTimeout(250)
  assertTokenDuration(await computedTransitionDurations(page, '[data-slot="sidebar"] a'), '0.18s')

  const sidebar = page.locator('[data-slot="sidebar"]').first()
  await sidebar.getByRole('link', { name: '任务与通知', exact: true }).click({ noWaitAfter: true, force: true })
  await page.waitForTimeout(16)
  await sidebar.getByRole('link', { name: '项目', exact: true }).click({ noWaitAfter: true, force: true })
  await page.waitForTimeout(16)
  await sidebar.getByRole('link', { name: '插件中心', exact: true }).click({ noWaitAfter: true, force: true })
  await page.waitForURL(routeUrlMatches('/plugins'))
  await expect(page.locator('h1:visible').first()).toBeVisible()
  const animationCalls = await takeAnimationCalls(page)
  const samples = await sampleDuringTransition(page)
  const summary = summarizeSamples(samples)
  console.log(`[route-motion] rapid-a-b-c ${JSON.stringify(summary)}`)

  expect(summary.keys.at(-1)).toBe('/plugins')
  expect(summary.routeAnimationCount).toBeGreaterThan(0)
  expect(summary.ownerKinds).toEqual(['route-adapter'])
  expect(summary.adapterAnimationCount).toBeGreaterThan(0)
  expect(await page.locator('[data-ssgoi-transition]')).toHaveCount(1)
  await expect(page.locator('[data-ssgoi-transition]')).toHaveAttribute('data-ssgoi-transition', '/plugins')
  expect(await shell.evaluate((element) => element.isConnected)).toBe(true)
  expect(animationCalls.length).toBeGreaterThanOrEqual(2)
  expect(animationCalls.every(({ duration, easing }) => duration === 320 && normalizedCubicBezier(easing) === normalizedCubicBezier(summary.spatialEasing))).toBe(true)
  expect(new Set(animationCalls.map(({ targetBoundary }) => targetBoundary))).toContain('/plugins')
  expect(pageErrors).toEqual([])
})

test('runtime reduced-motion changes stop the active route and restore the next route transition', async ({ page }) => {
  test.setTimeout(45_000)
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await installApiFixtures(page)
  await page.goto('/dashboard')
  await assertShellReady(page)
  await takeAnimationCalls(page)

  const inboxLink = page.locator('[data-slot="sidebar"]').first().getByRole('link', { name: '任务与通知', exact: true })
  await inboxLink.click({ noWaitAfter: true })
  await expect.poll(async () => (await captureMotion(page)).routeAnimations.length, {
    timeout: 15_000,
    intervals: [10, 20, 40],
  }).toBeGreaterThan(0)
  const activeBeforeReduce = await captureMotion(page)
  expect(activeBeforeReduce.routeAnimations.length).toBeGreaterThan(0)
  // MediaQueryList change delivery follows the browser's rendering cycle.
  // Observe that cycle instead of racing it with a host-side 50 ms timeout.
  await page.evaluate(() => {
    const query = matchMedia('(prefers-reduced-motion: reduce)')
    globalThis.__routeMotionReducedFrame = new Promise((resolve) => {
      query.addEventListener('change', () => {
        requestAnimationFrame(() => resolve({
          reduced: query.matches,
          activeRoots: document.getAnimations().filter((animation) =>
            animation.effect?.target?.matches?.('[data-ssgoi-transition]'),
          ).length,
        }))
      }, { once: true })
    })
  })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const reducedFrame = await page.evaluate(() => globalThis.__routeMotionReducedFrame)
  expect(reducedFrame).toEqual({ reduced: true, activeRoots: 0 })
  await expect.poll(() => new URL(page.url()).search).toBe('?tab=pending')
  const afterReduce = await captureMotion(page)
  console.log(`[route-motion] runtime-reduce ${JSON.stringify({
    before: activeBeforeReduce.routeAnimations,
    reducedFrame,
    after: afterReduce.routeAnimations,
  })}`)
  expect(afterReduce.routeAnimations.length).toBe(0)
  expect(await page.locator('[data-ssgoi-transition]').count()).toBe(1)
  await expect(page.locator('[data-ssgoi-transition]')).toHaveAttribute('data-ssgoi-transition', '/inbox')
  assertTokenDuration(await computedTransitionDurations(page, '[data-slot="sidebar"] a'), '0.00001s')

  await takeAnimationCalls(page)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await navigateFromSidebar(page, sidebarEntries[2])
  const samples = await sampleDuringTransition(page)
  const animationCalls = await takeAnimationCalls(page)
  const summary = summarizeSamples(samples)
  console.log(`[route-motion] runtime-recovered ${JSON.stringify(summary)}`)
  assertRouteAdapterMeasurement(summary, animationCalls, '/inbox', '/studio')
  assertTokenDuration(await computedTransitionDurations(page, '[data-slot="sidebar"] a'), '0.18s')
  expect(pageErrors).toEqual([])
})
