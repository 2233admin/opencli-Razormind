'use client'

import Link from 'next/link'
import { buttonVariants } from '@/components/ui/button'
import { buildRunUrl, type RunNavigationContext } from '@/lib/studio/run-navigation'
import { cn } from '@/lib/utils'

export function RunContextBanner({ context, projectId }: { context: RunNavigationContext; projectId: string }) {
  if (!context.workflow && !context.run && !context.trace) return null
  const operations = context.workspace ? buildRunUrl('operations', { workspace: context.workspace, project: projectId, workflow: context.workflow }, projectId) : null
  return <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 p-3 text-xs"><span>从 Run {context.run ?? '上下文'} 跳转；当前为项目级视图，未按 Run 筛选。{context.trace ? ` trace ${context.trace}` : ''}</span>{operations ? <Link className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))} href={operations}>返回运行列表</Link> : null}</div>
}
