'use client'

import { useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * A compact, responsive page split inspired by OpenAlice's PageSidebarLayout.
 * It keeps the product's current CSS grid and avoids a second router or panel
 * dependency: the contextual workspace remains visible beside the task on wide
 * screens and becomes the first reading region on narrow screens.
 */
export function ContextPageLayout({
  children,
  context,
  className,
}: {
  children: ReactNode
  context: ReactNode
  className?: string
}) {
  const [mobileView, setMobileView] = useState<'canvas' | 'context'>('context')
  return (
    <section className={cn('grid min-h-0 overflow-hidden rounded-md border bg-card/25 xl:min-h-[760px] xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,0.55fr)]', className)} aria-label="项目草稿工作台">
      <div className="col-span-full flex border-b xl:hidden" aria-label="工作台视图">
        <button type="button" aria-pressed={mobileView === 'canvas'} className="min-h-11 flex-1 text-xs font-medium aria-pressed:border-b-2 aria-pressed:border-foreground" onClick={() => setMobileView('canvas')}>工作流方案</button>
        <button type="button" aria-pressed={mobileView === 'context'} className="min-h-11 flex-1 text-xs font-medium aria-pressed:border-b-2 aria-pressed:border-foreground" onClick={() => setMobileView('context')}>Agent 对话</button>
      </div>
      <div className={cn('min-w-0 xl:row-start-1', mobileView === 'canvas' ? 'block' : 'hidden xl:block')}>{children}</div>
      <aside className={cn('flex min-h-0 min-w-0 flex-col border-b xl:row-start-1 xl:border-b-0 xl:border-l', mobileView === 'context' ? 'block' : 'hidden xl:flex')} aria-label="Agent 对话与项目上下文">
        {context}
      </aside>
    </section>
  )
}
