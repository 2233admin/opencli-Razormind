'use client'

import { Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'

import { AppRouteTransition } from '@/components/motion/app-route-transition'
import { AppHeader } from '@/components/shell/app-header'
import { AppSidebar } from '@/components/shell/app-sidebar'
import { CommandPalette } from '@/components/shell/command-palette'
import { GlobalAgentDock } from '@/components/shell/global-agent-dock'
import { WorkTabs } from '@/components/shell/work-tabs'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { useAuth } from '@/components/auth/auth-provider'

export function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const [commandOpen, setCommandOpen] = useState(false)
  const [agentOpen, setAgentOpen] = useState(false)
  useEffect(() => {
    if (status !== 'authenticated') {
      setCommandOpen(false)
      setAgentOpen(false)
    }
  }, [status])
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <AppHeader
          onOpenAgent={() => setAgentOpen(true)}
          onOpenCommand={() => setCommandOpen(true)}
        />
        <Suspense fallback={null}><WorkTabs /></Suspense>
        <div className="relative z-0 flex-1 overflow-auto overflow-x-clip bg-background [scrollbar-gutter:stable]">
          <AppRouteTransition>{children}</AppRouteTransition>
        </div>
      </SidebarInset>
      <CommandPalette open={commandOpen} onOpenChange={setCommandOpen} />
      <Suspense fallback={null}><AgentUrlIntent enabled={status === 'authenticated'} onOpen={() => setAgentOpen(true)} /></Suspense>
      <Suspense fallback={null}>
        <GlobalAgentDock open={agentOpen} onOpenChange={setAgentOpen} />
      </Suspense>
    </SidebarProvider>
  )
}

function AgentUrlIntent({ enabled, onOpen }: { enabled: boolean; onOpen: () => void }) {
  const searchParams = useSearchParams()
  const openedIntent = useRef<string | null>(null)
  useEffect(() => {
    if (!enabled) {
      openedIntent.current = null
      return
    }
    const intent = searchParams.get('agent') === '1'
      ? `${searchParams.get('conversation') ?? ''}:${searchParams.get('workspace') ?? ''}`
      : null
    if (intent && intent !== openedIntent.current) onOpen()
    openedIntent.current = intent
  }, [enabled, onOpen, searchParams])
  return null
}
