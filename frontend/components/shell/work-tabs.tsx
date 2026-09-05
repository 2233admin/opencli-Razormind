'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { useAuth } from '@/components/auth/auth-provider'
import { closeWorkTab, openWorkTab, restoreWorkTabs, workTabFromHref, type WorkTab } from '@/lib/work-tabs'
import { cn } from '@/lib/utils'

export function WorkTabs() {
  const { identity, status } = useAuth()
  const workspace = useSearchParams().get('workspace')
  if (status !== 'authenticated' || !identity || !workspace) return null
  const scope = JSON.stringify([identity.auth_method, identity.subject, workspace])
  return <ScopedWorkTabs key={scope} workspace={workspace} storageKey={`opencli:work-tabs:v1:${scope}`} />
}

function ScopedWorkTabs({ workspace, storageKey }: { workspace: string; storageKey: string }) {
  const pathname = usePathname()
  const search = useSearchParams().toString()
  const router = useRouter()
  const href = `${pathname}?${search}`
  const current = workTabFromHref(href, workspace)
  const [tabs, setTabs] = useState<WorkTab[]>([])
  const snapshot = useRef<WorkTab[] | null>(null)

  function save(next: WorkTab[]) {
    snapshot.current = next
    setTabs(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Navigation still works without storage. */ }
  }

  useEffect(() => {
    let previous = snapshot.current
    if (!previous) {
      try { previous = restoreWorkTabs(localStorage.getItem(storageKey), workspace) } catch { previous = [] }
    }
    const tab = workTabFromHref(href, workspace)
    const next = tab ? openWorkTab(previous, tab) : previous
    snapshot.current = next
    setTabs(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Optional persistence. */ }
  }, [href, storageKey, workspace])

  if (!tabs.length) return null
  return (
    <nav aria-label="工作标签" className="flex min-w-0 items-center gap-1 overflow-x-auto border-b bg-muted/20 px-3 py-1">
      {tabs.map((tab) => (
        <div key={tab.id} className={cn('flex shrink-0 items-center rounded-md border border-transparent text-xs', current?.id === tab.id && 'border-border bg-background shadow-sm')}>
          <Link href={tab.href} prefetch={false} scroll={false} aria-current={current?.id === tab.id ? 'page' : undefined} title={tab.href} className="max-w-52 truncate rounded px-2.5 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{tab.label}</Link>
          <button type="button" aria-label={`关闭工作标签：${tab.label}`} className="mr-1 rounded p-1.5 text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => {
            const result = closeWorkTab(snapshot.current ?? tabs, tab.id, current?.id)
            save(result.tabs)
            if (current?.id === tab.id) router.replace(result.next?.href ?? `/studio?${new URLSearchParams({ workspace })}`, { scroll: false })
          }}><X className="size-3.5" aria-hidden /></button>
        </div>
      ))}
    </nav>
  )
}
