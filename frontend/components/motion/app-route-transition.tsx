'use client'

import { Ssgoi, type SsgoiConfig } from '@ssgoi/react'
import { axis, drill } from '@ssgoi/react/view-transitions'
import { useReducedMotion } from 'motion/react'
import { usePathname } from 'next/navigation'

const APP_ROUTES = [
  '/dashboard',
  '/studio',
  '/studio/workflow',
  '/sources',
  '/sources/*',
  '/records',
  '/agents',
  '/operations-agents',
  '/skills',
  '/providers',
  '/nodes',
  '/workers',
  '/control/kill-switch',
  '/control/advisory-report',
  '/control/odp-state',
  '/canvas',
] as const

const MOTION_CONFIG: SsgoiConfig = {
  transitions: [
    { from: '/studio', to: '/studio/workflow', transition: drill({ type: 'parallax' }) },
    { from: '/sources', to: '/sources/*', transition: drill({ type: 'slide' }) },
    { ordered: APP_ROUTES, transition: axis({ type: 'x', variant: 'snappy' }) },
  ],
}

const STATIC_CONFIG: SsgoiConfig = { transitions: [] }
/** Keeps persistent application chrome outside the routed animation boundary. */
export function AppRouteTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const prefersReducedMotion = useReducedMotion()
  // All Action Center sibling tabs share /inbox, so this boundary never remounts
  // or runs a pathname-level transition while their query state changes.
  const transitionKey = pathname === '/inbox' ? '/inbox' : pathname

  return (
    <Ssgoi config={prefersReducedMotion ? STATIC_CONFIG : MOTION_CONFIG}>
      <div
        key={transitionKey}
        data-ssgoi-transition={transitionKey}
        className="h-full min-h-full bg-background"
      >
        {children}
      </div>
    </Ssgoi>
  )
}
