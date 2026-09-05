'use client'

import { BrainCircuit, Braces, ChartNoAxesCombined, Database, LayoutDashboard, Network, Settings2, Workflow } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'

import { buildProjectNavigationUrl, parseRunNavigation } from '@/lib/studio/run-navigation'
import { cn } from '@/lib/utils'

export type ProjectNavigationSection =
  | 'overview'
  | 'orchestration'
  | 'data'
  | 'evidence'
  | 'relationships'
  | 'apiAccess'
  | 'operations'

const PROJECT_SECTIONS = [
  { id: 'overview', label: '概览', icon: LayoutDashboard },
  { id: 'orchestration', label: '业务编排', icon: Workflow },
  { id: 'data', label: '数据工作台', icon: Database },
  { id: 'evidence', label: '逻辑与证据', icon: BrainCircuit },
  { id: 'relationships', label: '证据关系', icon: Network },
  { id: 'apiAccess', label: 'API / MCP', icon: Braces },
  { id: 'operations', label: '运行记录', icon: ChartNoAxesCombined },
  { id: 'settings', label: '设置', icon: Settings2 },
] as const

export function ProjectNavigation({
  active,
  workspaceId,
  projectId,
  workflowId,
}: {
  active: ProjectNavigationSection
  workspaceId: string | null
  projectId: string | null
  workflowId?: string | null
}) {
  const searchParams = useSearchParams()
  const current = parseRunNavigation(searchParams)
  current.project ??= projectId ?? undefined
  const scope = { workspace: workspaceId, project: projectId, workflow: workflowId }
  const overviewHref = buildProjectNavigationUrl('overview', scope, current)
  const orchestrationHref = buildProjectNavigationUrl('orchestration', scope, current)
  const dataHref = buildProjectNavigationUrl('data', scope, current)
  const evidenceHref = buildProjectNavigationUrl('evidence', scope, current)
  const relationshipsHref = buildProjectNavigationUrl('relationships', scope, current)
  const apiAccessHref = buildProjectNavigationUrl('apiAccess', scope, current)
  const operationsHref = buildProjectNavigationUrl('operations', scope, current)
  const sectionHrefs = {
    overview: overviewHref,
    orchestration: orchestrationHref,
    data: dataHref,
    evidence: evidenceHref,
    relationships: relationshipsHref,
    apiAccess: apiAccessHref,
    operations: operationsHref,
    settings: null,
  } satisfies Record<(typeof PROJECT_SECTIONS)[number]['id'], string | null>

  return (
    <nav className="-mx-1 flex min-w-0 items-center gap-1 overflow-x-auto px-1" aria-label="项目导航">
      {PROJECT_SECTIONS.map((section) => {
        const href = sectionHrefs[section.id]
        const isActive = section.id === active
        const Icon = section.icon
        const className = cn(
          'inline-flex h-11 shrink-0 items-center rounded-xs px-3 text-xs transition-colors',
          isActive ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground',
          href && !isActive && 'hover:bg-muted/60 hover:text-foreground',
          !href && 'cursor-not-allowed opacity-45',
        )

        return href ? (
          <Link key={section.id} href={href} aria-current={isActive ? 'page' : undefined} className={className}>
            <Icon className="mr-1.5 size-3.5" aria-hidden />{section.label}
          </Link>
        ) : (
          <span key={section.id} className={className} aria-disabled="true" title="项目范围能力将在后续生命周期接线中开放">
            <Icon className="mr-1.5 size-3.5" aria-hidden />{section.label}
          </span>
        )
      })}
    </nav>
  )
}
