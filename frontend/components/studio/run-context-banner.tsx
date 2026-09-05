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
  return <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-3 text-xs"><span>从 Run {context.run ?? '上下文'} 跳转；当前数据按项目展示，未按此运行筛选。{context.trace ? ` trace ${context.trace}` : ''}</span>{operations ? <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={operations}>返回此运行 Trace</Link> : null}{agent ? <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={agent}>讨论本次结果</Link> : null}</div>
}
