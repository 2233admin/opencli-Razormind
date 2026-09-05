import {
  Animation as SsgoiAnimation,
  type NavigationDirection,
  type Pose,
  type Timeline,
  type TransitionConfig,
} from '@ssgoi/react'

const ROUTE_DISTANCE_PX = 8
const FALLBACK_DURATION_MS = 320
const FALLBACK_EASING = 'cubic-bezier(0.32, 0.72, 0, 1)'

type RouteSide = 'in' | 'out'
type RouteFrame = { opacity: number; transform: string }
type ControlledStyle = Pick<CSSStyleDeclaration, 'opacity' | 'pointerEvents' | 'transform' | 'willChange'>
type RouteTransitionExtras = {
  direction: NavigationDirection
  fromStyle: ControlledStyle
  toStyle: ControlledStyle
}
type RoutePose = Pose & { routeFrame: RouteFrame; routeSide: RouteSide }

type SideState = {
  element: HTMLElement
  matchedStartFrame: RouteFrame | null
  native: globalThis.Animation | null
  originalStyle: ControlledStyle
  progress: number
  role: RouteSide
  runDurationMs: number
  runFrom: number
  runId: number
  runTo: 0 | 1
  settled: boolean
  velocity: number
}

function captureStyle(element: HTMLElement): ControlledStyle {
  return {
    opacity: element.style.opacity,
    pointerEvents: element.style.pointerEvents,
    transform: element.style.transform,
    willChange: element.style.willChange,
  }
}

function clamp(value: number) {
  return Math.max(0, Math.min(1, value))
}

function parseCssTime(value: string) {
  const match = value.trim().toLowerCase().match(/^(-?(?:\d+\.?\d*|\.\d+))(ms|s)$/)
  const amount = match ? Number(match[1]) : Number.NaN
  if (!match || !Number.isFinite(amount) || amount < 0) return FALLBACK_DURATION_MS
  return match[2] === 's' ? amount * 1_000 : amount
}

function readMotionTokens() {
  if (typeof document === 'undefined' || typeof getComputedStyle === 'undefined') {
    return { durationMs: FALLBACK_DURATION_MS, easing: FALLBACK_EASING }
  }
  const styles = getComputedStyle(document.documentElement)
  return {
    durationMs: parseCssTime(styles.getPropertyValue('--motion-duration-spatial')),
    easing: styles.getPropertyValue('--motion-ease-spatial').trim() || FALLBACK_EASING,
  }
}

function reducedMotionQuery() {
  return typeof window === 'undefined' ? null : window.matchMedia('(prefers-reduced-motion: reduce)')
}

/** Reports the browser's intent without taking over navigation from Next or SSGOI. */
export function observeRouteDirection(
  callback: (direction: NavigationDirection | undefined) => void,
): () => void {
  if (typeof window === 'undefined') return () => undefined

  const navigation = window.navigation
  if (navigation) {
    let navigationId = 0
    const handleNavigate = (event: NavigateEvent) => {
      const current = navigation.currentEntry
      const destination = event.destination
      const currentUrl = current?.url ? new URL(current.url) : null
      const destinationUrl = destination.url ? new URL(destination.url) : null

      // Next may replace only the current entry's metadata after a traversal.
      // That event must not erase the still-pending direction for the route pair.
      if (
        event.navigationType === 'replace' &&
        currentUrl &&
        destinationUrl &&
        currentUrl.href === destinationUrl.href
      ) return

      const id = ++navigationId

      if (
        !current ||
        !currentUrl ||
        !destinationUrl ||
        currentUrl.origin !== destinationUrl.origin ||
        currentUrl.pathname === destinationUrl.pathname
      ) {
        callback(undefined)
      } else if (event.navigationType === 'traverse') {
        callback(destination.index < current.index ? 'backward' : 'forward')
      } else {
        callback('forward')
      }

      event.signal.addEventListener(
        'abort',
        () => {
          if (navigationId !== id) return
          const committedUrl = navigation.currentEntry?.url
          if (!committedUrl || !destinationUrl || new URL(committedUrl).href !== destinationUrl.href) {
            callback(undefined)
          }
        },
        { once: true },
      )
    }
    navigation.addEventListener('navigate', handleNavigate)
    return () => {
      navigationId += 1
      navigation.removeEventListener('navigate', handleNavigate)
    }
  }

  const handleClick = (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
    if (!anchor || anchor.download || (anchor.target && anchor.target !== '_self')) return
    const destination = new URL(anchor.href, window.location.href)
    if (destination.origin !== window.location.origin) return
    callback(destination.pathname === window.location.pathname ? undefined : 'forward')
  }
  const handlePopState = () => callback(undefined)
  document.addEventListener('click', handleClick, true)
  window.addEventListener('popstate', handlePopState)
  return () => {
    document.removeEventListener('click', handleClick, true)
    window.removeEventListener('popstate', handlePopState)
  }
}

class RouteAnimation extends SsgoiAnimation {
  private readonly direction: NavigationDirection
  private readonly durationMs: number
  private readonly easing: string
  private readonly motionQuery: MediaQueryList | null
  private settled = false
  private state: 'idle' | 'paused' | 'playing' | 'reversing' = 'idle'
  private readonly sides: SideState[]

  constructor(
    from: HTMLElement,
    to: HTMLElement,
    direction: NavigationDirection,
    durationMs: number,
    easing: string,
    fromStyle: ControlledStyle,
    toStyle: ControlledStyle,
    motionQuery: MediaQueryList | null,
  ) {
    super()
    this.direction = direction
    this.durationMs = durationMs
    this.easing = easing
    this.motionQuery = motionQuery
    this.sides = [this.createSide(from, 'out', fromStyle), this.createSide(to, 'in', toStyle)]
    this.motionQuery?.addEventListener('change', this.handleMotionPreference)
  }

  play() {
    if (this.state === 'paused') {
      this.beginRun('playing')
      for (const side of this.sides) this.startToward(side, 1)
      return
    }
    this.beginRun('playing')
    for (const side of this.sides) this.startToward(side, 1)
  }

  reverse() {
    this.beginRun('reversing')
    for (const side of this.sides) this.startToward(side, 0)
  }

  pause() {
    if (this.settled) return
    this.state = 'paused'
    for (const side of this.sides) {
      this.captureProgress(side)
      side.native?.pause()
    }
  }

  complete() {
    if (this.settled) return
    for (const side of this.sides) {
      this.stopNative(side)
      side.progress = 1
      side.velocity = 0
      side.settled = true
      this.applyFrame(side, this.frameAt(side, 1))
    }
    this.finishIfReady()
  }

  get isAnimating() {
    return this.sides.some((side) => Boolean(side.native)) && this.state !== 'paused' && !this.settled
  }

  get isPaused() {
    return this.state === 'paused'
  }

  get isComplete() {
    return this.settled
  }

  get isReversing() {
    return this.state === 'reversing'
  }

  get progress() {
    for (const side of this.sides) this.captureProgress(side)
    return this.sides.reduce((total, side) => total + side.progress, 0) / this.sides.length
  }

  findTimeForProgress(threshold: number) {
    return this.durationMs * clamp(threshold)
  }

  get playbackRate() {
    return super.playbackRate
  }

  set playbackRate(rate: number) {
    super.playbackRate = rate
    for (const side of this.sides) {
      if (side.native) side.native.playbackRate = rate
    }
  }

  getPose(): Pose[] {
    return this.sides.map((side) => {
      this.captureProgress(side)
      return {
        element: side.element,
        routeFrame: this.frameAt(side, side.progress),
        routeSide: side.role,
        value: side.progress,
        velocity: side.velocity,
      } as RoutePose
    })
  }

  getTimeline(): Timeline[] {
    return this.sides.map((side) => ({
      element: side.element,
      frames: [
        { time: 0, value: side.progress, velocity: side.velocity, style: this.frameAt(side, side.progress) },
        {
          time: this.durationMs * Math.abs(1 - side.progress),
          value: 1,
          velocity: 0,
          style: this.frameAt(side, 1),
        },
      ],
    }))
  }

  matchInto(poses: Pose[]) {
    for (const side of this.sides) {
      const pose = poses.find((candidate) => candidate.element === side.element) as RoutePose | undefined
      if (!pose) continue
      if (pose.routeSide && pose.routeSide !== side.role && pose.routeFrame) {
        side.progress = 0
        side.velocity = 0
        side.matchedStartFrame = pose.routeFrame
        this.applyFrame(side, pose.routeFrame)
      } else {
        side.progress = clamp(pose.value)
        side.velocity = pose.velocity
        this.applyFrame(side, this.frameAt(side, side.progress))
      }
    }
  }

  private createSide(element: HTMLElement, role: RouteSide, originalStyle: ControlledStyle): SideState {
    return {
      element,
      matchedStartFrame: null,
      native: null,
      originalStyle,
      progress: 0,
      role,
      runDurationMs: 0,
      runFrom: 0,
      runId: 0,
      runTo: 1,
      settled: false,
      velocity: 0,
    }
  }

  private beginRun(state: 'playing' | 'reversing') {
    this.settled = false
    this.state = state
    for (const side of this.sides) side.settled = false
  }

  private startToward(side: SideState, target: 0 | 1) {
    this.captureProgress(side)
    this.stopNative(side)
    side.runFrom = side.progress
    side.runTo = target
    side.runDurationMs = this.durationMs * Math.abs(side.runTo - side.runFrom)
    if (side.runDurationMs <= 0) {
      side.progress = target
      side.velocity = 0
      side.settled = true
      this.applyFrame(side, this.frameAt(side, target))
      this.finishIfReady()
      return
    }

    side.element.style.willChange = 'transform, opacity'
    if (side.role === 'out') side.element.style.pointerEvents = 'none'
    const startFrame = side.matchedStartFrame ?? this.frameAt(side, side.runFrom)
    side.matchedStartFrame = null
    const runId = ++side.runId
    try {
      const native = side.element.animate([startFrame, this.frameAt(side, side.runTo)], {
        duration: side.runDurationMs,
        easing: this.easing,
        fill: 'both',
      })
      native.playbackRate = this.playbackRate
      native.onfinish = () => this.finishSide(side, runId, target)
      native.oncancel = null
      side.native = native
    } catch {
      this.finishSide(side, runId, target)
    }
  }

  private finishSide(side: SideState, runId: number, target: 0 | 1) {
    if (runId !== side.runId) return
    side.progress = target
    side.velocity = 0
    side.settled = true
    this.applyFrame(side, this.frameAt(side, target))
    this.stopNative(side)
    this.finishIfReady()
  }

  private finishIfReady() {
    if (this.settled || this.sides.some((side) => !side.settled)) return
    this.settled = true
    this.state = 'idle'
    this.motionQuery?.removeEventListener('change', this.handleMotionPreference)
    for (const side of this.sides) {
      this.stopNative(side)
      Object.assign(side.element.style, side.originalStyle)
    }
    this.onComplete?.()
  }

  private captureProgress(side: SideState) {
    if (!side.native || side.runDurationMs <= 0 || side.settled) return
    const computedProgress = side.native.effect?.getComputedTiming().progress
    const currentTime = side.native.currentTime
    const fraction =
      typeof computedProgress === 'number'
        ? clamp(computedProgress)
        : typeof currentTime === 'number'
          ? clamp(currentTime / side.runDurationMs)
          : null
    if (fraction === null) return
    side.progress = side.runFrom + (side.runTo - side.runFrom) * fraction
    side.velocity = ((side.runTo - side.runFrom) * 1_000) / side.runDurationMs
  }

  private stopNative(side: SideState) {
    const native = side.native
    side.native = null
    side.runId += 1
    if (!native) return
    native.onfinish = null
    native.oncancel = null
    native.cancel()
  }

  private frameAt(side: SideState, progress: number): RouteFrame {
    if (side.role === 'out') return { opacity: 1 - progress, transform: 'translate3d(0, 0, 0)' }
    const direction = this.direction === 'forward' ? 1 : -1
    return {
      opacity: progress,
      transform: `translate3d(${direction * ROUTE_DISTANCE_PX * (1 - progress)}px, 0, 0)`,
    }
  }

  private applyFrame(side: SideState, frame: RouteFrame) {
    side.element.style.opacity = String(frame.opacity)
    side.element.style.transform = frame.transform
  }

  private readonly handleMotionPreference = (event: MediaQueryListEvent) => {
    if (event.matches) this.complete()
  }
}

/** SSGOI keeps ownership of pairing, detached-node lifetime, interruption, and scroll policy. */
export function createRouteTransition(
  getDirection?: () => NavigationDirection | undefined,
): TransitionConfig<RouteTransitionExtras> {
  return {
    prepare: async ({ from, to, context }) => {
      const routeDirection = getDirection?.() ?? context.direction
      const [fromElement, toElement] = await Promise.all([from, to])
      const fromStyle = captureStyle(fromElement)
      const toStyle = captureStyle(toElement)
      const direction = routeDirection === 'forward' ? 1 : -1
      if (!reducedMotionQuery()?.matches) {
        fromElement.style.pointerEvents = 'none'
        fromElement.style.willChange = 'transform, opacity'
        toElement.style.opacity = '0'
        toElement.style.transform = `translate3d(${direction * ROUTE_DISTANCE_PX}px, 0, 0)`
        toElement.style.willChange = 'transform, opacity'
      }
      return { direction: routeDirection, fromStyle, toStyle }
    },
    animation: ({ from, to, direction, fromStyle, toStyle }) => {
      const { durationMs, easing } = readMotionTokens()
      const motionQuery = reducedMotionQuery()
      return new RouteAnimation(
        from,
        to,
        direction,
        motionQuery?.matches ? 0 : durationMs,
        easing,
        fromStyle,
        toStyle,
        motionQuery,
      )
    },
  }
}
