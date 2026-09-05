'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { buttonVariants } from '@/components/ui/button'
import { buildRunUrl, buildScopedAgentUrl, type RunNavigationContext } from '@/lib/studio/run-navigation'
import { cn } from '@/lib/utils'

export function RunContextBanner({ context, projectId }: { context: RunNavigationContext; projectId: string }) {
  const pathname = usePathname()
  if (!context.run && !context.trace) return null
  const operations = context.workspace && context.workflow && context.run && (!context.project || context.project === projectId)
    ? buildRunUrl('operations', { ...context, project: projectId }, projectId)
    : null
  const agent = buildScopedAgentUrl(pathname, { ...context, project: projectId })
  return <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-3 text-xs"><span>{context.run ? `当前页面已按 Run ${context.run} 筛选。` : '当前页面使用项目范围，未提供具体运行。'}{context.trace ? ` trace ${context.trace}` : ''}</span>{operations ? <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={operations}>返回此运行 Trace</Link> : null}{agent ? <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={agent}>讨论本次结果</Link> : null}</div>
}
