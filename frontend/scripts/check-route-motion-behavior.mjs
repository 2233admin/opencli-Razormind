import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createRouteTransition,
  observeRouteDirection,
} from '../components/motion/route-transition.ts'

const motionTokens = new Map([
  ['--motion-duration-spatial', '320ms'],
  ['--motion-ease-spatial', 'cubic-bezier(0.32, 0.72, 0, 1)'],
])

class MockMediaQuery {
  listeners = new Set()
  matches = false

  addEventListener(_type, listener) {
    this.listeners.add(listener)
  }

  removeEventListener(_type, listener) {
    this.listeners.delete(listener)
  }

  setMatches(matches) {
    this.matches = matches
    for (const listener of [...this.listeners]) listener({ matches })
  }
}

let mediaQuery = new MockMediaQuery()

globalThis.document = { documentElement: {} }
globalThis.getComputedStyle = () => ({
  getPropertyValue: (name) => motionTokens.get(name) ?? '',
})
globalThis.window = { matchMedia: () => mediaQuery }

test.beforeEach(() => {
  mediaQuery = new MockMediaQuery()
})

class MockNativeAnimation {
  computedProgress = null
  currentTime = 0
  effect = {
    getComputedTiming: () => ({ progress: this.computedProgress }),
  }
  oncancel = null
  onfinish = null
  playbackRate = 1
  cancelled = false
  paused = false

  cancel() {
    this.cancelled = true
  }

  pause() {
    this.paused = true
  }

  play() {
    this.paused = false
  }

  finish() {
    this.onfinish?.()
  }
}

class MockElement {
  animations = []
  style = {
    opacity: '',
    pointerEvents: '',
    transform: '',
    willChange: '',
  }

  animate(keyframes, options) {
    const animation = new MockNativeAnimation()
    this.animations.push({ animation, keyframes, options })
    return animation
  }
}

function transitionContext(direction) {
  return {
    direction,
    scrollOffset: { x: 0, y: 0 },
    from: { scroll: { x: 0, y: 0 } },
    to: { scroll: { x: 0, y: 0 } },
    scrollingElement: new MockElement(),
    positionedParent: new MockElement(),
  }
}

async function buildAnimation(
  direction = 'forward',
  from = new MockElement(),
  to = new MockElement(),
  getDirection,
) {
  const transition = createRouteTransition(getDirection)
  const context = transitionContext(direction)
  const extras = await transition.prepare({
    from: Promise.resolve(from),
    to: Promise.resolve(to),
    context,
    createElement: () => new MockElement(),
  })
  const animation = transition.animation({ from, to, context, ...extras })
  return { animation, from, to }
}

class MockNavigation {
  currentEntry = { index: 0, url: 'https://opencli.test/dashboard' }
  listeners = new Set()

  addEventListener(_type, listener) {
    this.listeners.add(listener)
  }

  removeEventListener(_type, listener) {
    this.listeners.delete(listener)
  }

  navigate(navigationType, index, url) {
    const abortListeners = new Set()
    const event = {
      destination: { index, url },
      navigationType,
      signal: {
        addEventListener: (_type, listener) => abortListeners.add(listener),
      },
    }
    for (const listener of [...this.listeners]) listener(event)
    return () => {
      for (const listener of abortListeners) listener()
    }
  }
}

test('navigation observer distinguishes push, back, forward, query-only, and cleanup', () => {
  const navigation = new MockNavigation()
  globalThis.window.navigation = navigation
  const directions = []
  const stop = observeRouteDirection((direction) => directions.push(direction))

  navigation.navigate('push', -1, 'https://opencli.test/workflows')
  navigation.currentEntry = { index: 2, url: 'https://opencli.test/workflows' }
  navigation.navigate('traverse', 1, 'https://opencli.test/dashboard')
  navigation.currentEntry = { index: 1, url: 'https://opencli.test/dashboard' }
  const abortForwardTraversal = navigation.navigate('traverse', 2, 'https://opencli.test/workflows')
  navigation.currentEntry = { index: 2, url: 'https://opencli.test/workflows' }
  navigation.navigate('replace', -1, 'https://opencli.test/workflows')
  abortForwardTraversal()
  navigation.navigate('replace', -1, 'https://opencli.test/settings')
  navigation.currentEntry = { index: 3, url: 'https://opencli.test/settings' }
  navigation.navigate('push', -1, 'https://opencli.test/settings?tab=account')

  assert.deepEqual(directions, ['forward', 'backward', 'forward', 'forward', undefined])
  assert.equal(navigation.listeners.size, 1)
  stop()
  assert.equal(navigation.listeners.size, 0)
  navigation.navigate('push', -1, 'https://opencli.test/runs')
  assert.equal(directions.length, 5)
})

test('navigation observer clears an uncommitted navigation when it is aborted', () => {
  const navigation = new MockNavigation()
  globalThis.window.navigation = navigation
  const directions = []
  const stop = observeRouteDirection((direction) => directions.push(direction))

  const abort = navigation.navigate('push', -1, 'https://opencli.test/workflows')
  abort()

  assert.deepEqual(directions, ['forward', undefined])
  stop()
})

test('navigation observer fallback reports plain local links and delegates popstate direction', () => {
  const documentListeners = new Map()
  const windowListeners = new Map()
  globalThis.document = {
    documentElement: {},
    addEventListener: (type, listener) => documentListeners.set(type, listener),
    removeEventListener: (type, listener) => {
      if (documentListeners.get(type) === listener) documentListeners.delete(type)
    },
  }
  globalThis.window = {
    addEventListener: (type, listener) => windowListeners.set(type, listener),
    location: {
      href: 'https://opencli.test/dashboard',
      origin: 'https://opencli.test',
      pathname: '/dashboard',
    },
    matchMedia: () => mediaQuery,
    removeEventListener: (type, listener) => {
      if (windowListeners.get(type) === listener) windowListeners.delete(type)
    },
  }
  const directions = []
  const stop = observeRouteDirection((direction) => directions.push(direction))
  const link = {
    closest: () => ({
      download: '',
      href: 'https://opencli.test/workflows',
      target: '',
    }),
  }

  documentListeners.get('click')({
    altKey: false,
    button: 0,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    target: link,
  })
  windowListeners.get('popstate')()

  assert.deepEqual(directions, ['forward', undefined])
  stop()
  assert.equal(documentListeners.size, 0)
  assert.equal(windowListeners.size, 0)
})

test('resolved browser intent is consumed once in prepare and reused for the animation pair', async () => {
  let calls = 0
  let resolveFrom
  let resolveTo
  const from = new MockElement()
  const to = new MockElement()
  const transition = createRouteTransition(() => {
    calls += 1
    return 'forward'
  })
  const context = transitionContext('backward')
  const prepared = transition.prepare({
    from: new Promise((resolve) => {
      resolveFrom = resolve
    }),
    to: new Promise((resolve) => {
      resolveTo = resolve
    }),
    context,
    createElement: () => new MockElement(),
  })

  assert.equal(calls, 1)
  resolveFrom(from)
  resolveTo(to)
  const extras = await prepared
  const animation = transition.animation({ from, to, context, ...extras })
  assert.equal(to.style.transform, 'translate3d(8px, 0, 0)')
  animation.play()
  assert.equal(calls, 1)
  assert.equal(to.animations[0].keyframes[0].transform, 'translate3d(8px, 0, 0)')
})

test('route transition reads the shared spatial clock and applies the restrained forward axis', async () => {
  const { animation, from, to } = await buildAnimation('forward')
  let completed = 0
  animation.onComplete = () => completed += 1

  assert.equal(to.style.transform, 'translate3d(8px, 0, 0)')
  assert.equal(to.style.opacity, '0')

  animation.play()

  assert.equal(animation.isAnimating, true)
  assert.equal(from.animations.length, 1)
  assert.equal(to.animations.length, 1)
  for (const { options } of [from.animations[0], to.animations[0]]) {
    assert.equal(options.duration, 320)
    assert.equal(options.easing, 'cubic-bezier(0.32, 0.72, 0, 1)')
    assert.equal(options.fill, 'both')
  }
  assert.deepEqual(from.animations[0].keyframes, [
    { opacity: 1, transform: 'translate3d(0, 0, 0)' },
    { opacity: 0, transform: 'translate3d(0, 0, 0)' },
  ])
  assert.deepEqual(to.animations[0].keyframes, [
    { opacity: 0, transform: 'translate3d(8px, 0, 0)' },
    { opacity: 1, transform: 'translate3d(0px, 0, 0)' },
  ])

  from.animations[0].animation.finish()
  assert.equal(completed, 0)
  to.animations[0].animation.finish()
  assert.equal(completed, 1)
  assert.equal(animation.isComplete, true)
  assert.deepEqual(from.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
  assert.deepEqual(to.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
})

test('backward navigation enters from the opposite eight-pixel edge', async () => {
  const { animation, to } = await buildAnimation('backward')
  animation.play()

  assert.equal(to.animations[0].keyframes[0].transform, 'translate3d(-8px, 0, 0)')
  assert.equal(to.animations[0].keyframes[1].transform, 'translate3d(0px, 0, 0)')
})

test('pause and reverse continue from the captured midpoint without leaving an active prior animation', async () => {
  const { animation, from, to } = await buildAnimation('forward')
  animation.play()
  const firstFromRun = from.animations[0].animation
  const firstToRun = to.animations[0].animation
  firstFromRun.currentTime = 160
  firstToRun.currentTime = 160
  firstFromRun.computedProgress = 0.25
  firstToRun.computedProgress = 0.25

  animation.pause()
  assert.equal(animation.isPaused, true)
  assert.equal(firstFromRun.paused, true)
  assert.equal(firstToRun.paused, true)

  animation.reverse()
  assert.equal(animation.isReversing, true)
  assert.equal(firstFromRun.cancelled, true)
  assert.equal(firstToRun.cancelled, true)
  assert.equal(from.animations[1].options.duration, 80)
  assert.equal(to.animations[1].options.duration, 80)
  assert.deepEqual(from.animations[1].keyframes, [
    { opacity: 0.75, transform: 'translate3d(0, 0, 0)' },
    { opacity: 1, transform: 'translate3d(0, 0, 0)' },
  ])
  assert.deepEqual(to.animations[1].keyframes, [
    { opacity: 0.25, transform: 'translate3d(6px, 0, 0)' },
    { opacity: 0, transform: 'translate3d(8px, 0, 0)' },
  ])
})

test('play after pausing a reverse run heads forward from the eased current pose', async () => {
  const { animation, from, to } = await buildAnimation('forward')
  animation.play()
  from.animations[0].animation.computedProgress = 0.5
  to.animations[0].animation.computedProgress = 0.5
  animation.reverse()
  from.animations[1].animation.computedProgress = 0.5
  to.animations[1].animation.computedProgress = 0.5

  animation.pause()
  animation.play()

  assert.equal(animation.isReversing, false)
  assert.equal(from.animations[1].animation.cancelled, true)
  assert.equal(to.animations[1].animation.cancelled, true)
  assert.equal(from.animations[2].options.duration, 240)
  assert.equal(to.animations[2].options.duration, 240)
  assert.deepEqual(from.animations[2].keyframes[0], {
    opacity: 0.75,
    transform: 'translate3d(0, 0, 0)',
  })
  assert.deepEqual(to.animations[2].keyframes[0], {
    opacity: 0.25,
    transform: 'translate3d(6px, 0, 0)',
  })
})

test('resuming after one side has finished retains that completion', async () => {
  const { animation, from, to } = await buildAnimation('forward')
  let completed = 0
  animation.onComplete = () => completed += 1
  animation.play()

  from.animations[0].animation.finish()
  animation.pause()
  animation.play()
  assert.equal(to.animations[0].animation.cancelled, true)
  to.animations[1].animation.finish()

  assert.equal(completed, 1)
  assert.equal(animation.isComplete, true)
})

test('reduced motion skips both the prepared hidden state and native WAAPI', async () => {
  mediaQuery.matches = true
  const { animation, from, to } = await buildAnimation('forward')
  let completed = 0
  animation.onComplete = () => completed += 1

  assert.deepEqual(from.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
  assert.deepEqual(to.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
  animation.play()

  assert.equal(from.animations.length, 0)
  assert.equal(to.animations.length, 0)
  assert.equal(animation.isComplete, true)
  assert.equal(completed, 1)
  assert.equal(mediaQuery.listeners.size, 0)
})

test('a runtime reduced-motion change completes active WAAPI and removes its listener', async () => {
  const { animation, from, to } = await buildAnimation('forward')
  let completed = 0
  animation.onComplete = () => completed += 1
  animation.play()
  assert.equal(mediaQuery.listeners.size, 1)

  mediaQuery.setMatches(true)

  assert.equal(animation.isComplete, true)
  assert.equal(completed, 1)
  assert.equal(from.animations[0].animation.cancelled, true)
  assert.equal(to.animations[0].animation.cancelled, true)
  assert.equal(mediaQuery.listeners.size, 0)
  assert.deepEqual(from.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
  assert.deepEqual(to.style, { opacity: '', pointerEvents: '', transform: '', willChange: '' })
})

test('pose matching preserves the rendered frame when an entering page is interrupted and becomes outgoing', async () => {
  const pageA = new MockElement()
  const pageB = new MockElement()
  const pageC = new MockElement()
  const first = await buildAnimation('forward', pageA, pageB)
  first.animation.play()
  pageA.animations[0].animation.currentTime = 160
  pageB.animations[0].animation.currentTime = 160
  pageA.animations[0].animation.computedProgress = 0.25
  pageB.animations[0].animation.computedProgress = 0.25
  const poses = first.animation.getPose()

  const second = await buildAnimation('forward', pageB, pageC)
  first.animation.complete()
  second.animation.matchInto(poses)
  second.animation.play()
  assert.equal(pageB.animations[1].options.duration, 320)
  assert.deepEqual(pageB.animations[1].keyframes[0], {
    opacity: 0.25,
    transform: 'translate3d(6px, 0, 0)',
  })
  assert.deepEqual(pageB.animations[1].keyframes[1], {
    opacity: 0,
    transform: 'translate3d(0, 0, 0)',
  })

  let completed = 0
  second.animation.onComplete = () => completed += 1
  second.animation.complete()
  second.animation.complete()

  assert.equal(completed, 1)
  assert.equal(pageB.animations[1].animation.cancelled, true)
  assert.equal(pageC.animations[0].animation.cancelled, true)
})
