'use client'

import { use } from 'react'
import { useSearchParams } from 'next/navigation'
import { ProjectGraphExplorer } from '@/components/records/project-graph-explorer'
import { RunContextBanner } from '@/components/studio/run-context-banner'
import { parseRunNavigation } from '@/lib/studio/run-navigation'

export default function ProjectEvidencePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params)
  const context = parseRunNavigation(useSearchParams())
  return <div className="space-y-3"><RunContextBanner context={context} projectId={projectId} /><ProjectGraphExplorer projectId={projectId} mode="galaxy" /></div>
}
