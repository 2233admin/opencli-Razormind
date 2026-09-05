'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { motion } from 'motion/react'

import { Ripple } from '@/components/motion/ripple'
import { cn } from '@/lib/utils'

export type RouteTab = {
  href: string
  label: string
  exact?: boolean
  preserveSearchParams?: boolean
}

/**
 * M3-style segmented route tabs linking sibling views (e.g. 任务/记录/通知).
 * The active pill slides between tabs via a shared layout animation.
 */
export function RouteTabs({ tabs, className }: { tabs: RouteTab[]; className?: string }) {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const searchParamsKey = searchParams.toString()

  return (
    <nav
      aria-label="相关视图"
      className={cn(
        'inline-flex w-fit items-center gap-1 rounded-full bg-muted p-1',
        className,
      )}
    >
      {tabs.map((tab) => {
        const [targetPath, targetQuery] = tab.href.split('?')
        const targetParams = new URLSearchParams(targetQuery)
        const destinationParams = new URLSearchParams(searchParamsKey)
        if (tab.preserveSearchParams) {
          for (const [key, value] of targetParams) destinationParams.set(key, value)
        }
        const destinationQuery = destinationParams.toString()
        const destinationHref = tab.preserveSearchParams
          ? destinationQuery
            ? `${targetPath}?${destinationQuery}`
            : targetPath
          : tab.href
        const pathMatches = tab.exact
          ? pathname === targetPath
          : pathname === targetPath || pathname.startsWith(`${targetPath}/`)
        const queryMatches = [...targetParams.entries()].every(
          ([key, value]) =>
            searchParams.get(key) === value ||
            (key === 'tab' && value === 'pending' && searchParams.get(key) === null),
        )
        const active = pathMatches && queryMatches

        return (
          <Link
            key={tab.href}
            href={destinationHref}
            scroll={false}
            aria-current={active ? 'page' : undefined}
            onClick={(event) => {
              if (
                !tab.preserveSearchParams ||
                event.defaultPrevented ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey ||
                event.button !== 0
              ) {
                return
              }

              event.preventDefault()
              router.replace(destinationHref, { scroll: false })
            }}
            className={cn(
              'relative overflow-hidden rounded-full px-4 py-1.5 text-sm font-medium transition-colors',
              active ? 'text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {active ? (
              <motion.span
                layoutId="route-tab-pill"
                className="absolute inset-0 rounded-full bg-primary"
                transition={{ type: 'spring', stiffness: 460, damping: 38, mass: 0.6 }}
              />
            ) : null}
            <span className="relative">{tab.label}</span>
            <Ripple />
          </Link>
        )
      })}
    </nav>
  )
}

/** Shared tab sets for related views. */
export const ACTION_CENTER_TABS: RouteTab[] = [
  { href: '/inbox?tab=pending', label: '待处理', exact: true, preserveSearchParams: true },
  { href: '/inbox?tab=tasks', label: '工作项', exact: true, preserveSearchParams: true },
  { href: '/inbox?tab=notifications', label: '通知规则', exact: true, preserveSearchParams: true },
  { href: '/inbox?tab=controls', label: '控制记录', exact: true, preserveSearchParams: true },
]

export const AUTOMATION_TABS: RouteTab[] = [
  { href: '/operations-agents', label: '自动化与智能体' },
  { href: '/agents', label: 'Agent' },
  { href: '/skills', label: '技能' },
]

export const DATA_EXPLORER_TABS: RouteTab[] = [
  { href: '/records', label: '数据表', exact: true },
  { href: '/records/graph', label: '关系图谱' },
]

export const COMPUTE_TABS: RouteTab[] = [
  { href: '/nodes', label: '浏览器节点' },
  { href: '/workers', label: 'Worker' },
  { href: '/browsers', label: 'Chrome 池' },
]

export const MODEL_SETTINGS_TABS: RouteTab[] = [
  { href: '/providers', label: '快速设置', exact: true },
  { href: '/providers/catalog', label: 'Provider 目录' },
]

export const CONTROL_TABS: RouteTab[] = [
  { href: '/control/kill-switch', label: '熔断开关' },
  { href: '/control/advisory-report', label: '建议报告' },
  { href: '/control/odp-state', label: 'ODP 状态' },
]
