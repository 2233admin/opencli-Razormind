'use client'

import { Ssgoi, type NavigationDirection, type SsgoiConfig } from '@ssgoi/react'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { createRouteTransition, observeRouteDirection } from './route-transition'

/** Keeps persistent application chrome outside the routed animation boundary. */
export function AppRouteTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const direction = useRef<NavigationDirection | undefined>(undefined)
  // SSGOI must retain one context across renders and preference changes.
  const [config] = useState<SsgoiConfig>(() => ({
    transitions: [{
      on: '/**',
      transition: createRouteTransition(() => {
        const pendingDirection = direction.current
        direction.current = undefined
        return pendingDirection
      }),
    }],
  }))

  useEffect(() => observeRouteDirection((nextDirection) => {
    direction.current = nextDirection
  }), [])

  // Query-only tabs and filters update in place without replaying page entry.
  const transitionKey = pathname

  return (
    <Ssgoi config={config}>
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
