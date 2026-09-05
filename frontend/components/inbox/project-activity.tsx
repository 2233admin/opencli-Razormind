'use client'

import Link from 'next/link'
import { CheckCircle2 } from 'lucide-react'

import type { OperationsWorkItem } from '@/lib/api/types'
import { inboxOriginActions, readInboxOrigin } from '@/lib/inbox/origin-navigation'
import { formatRelative } from '@/lib/format'

export function ProjectActivity({ items }: { items: OperationsWorkItem[] }) {
  if (!items.length) return <p className="p-5 text-sm text-muted-foreground">当前工作区还没有已完成的 Agent 提案结果。</p>
  return <div className="divide-y" aria-label="项目动态">
    {items.map((item) => {
      const origin = readInboxOrigin(item.evidence, item.workspace_id)
      const result = item.evidence.execution && typeof item.evidence.execution === 'object'
        ? (item.evidence.execution as Record<string, unknown>).result : null
      const summary = result && typeof result === 'object' && typeof (result as Record<string, unknown>).summary === 'string'
        ? (result as Record<string, unknown>).summary as string
        : item.reason ?? 'Agent 提案已完成。'
      return <article key={item.id} className="p-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground"><CheckCircle2 className="size-4 text-success" />已完成 · {formatRelative(item.updated_at)}</div>
        <p className="mt-2 text-sm leading-6">{summary}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {inboxOriginActions(origin).map((action) => <Link key={action.href} href={action.href} className="text-xs font-medium text-primary hover:underline">{action.label}</Link>)}
        </div>
      </article>
    })}
  </div>
}
