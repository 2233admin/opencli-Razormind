'use client'

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useBootstrapWorkspaceProject } from '@/lib/api/hooks'
import type { WorkflowProject } from '@/lib/workflow/schema'
import type { ProjectAppType } from '@/lib/api/types'
import { studioAppTypeForTemplate, studioGraphForTemplate, studioSlug, type StudioTemplateId } from '@/lib/workflow/studio-templates'

type CreatedProject = Awaited<ReturnType<ReturnType<typeof useBootstrapWorkspaceProject>['mutateAsync']>>

export function useProjectCreation() {
  const bootstrap = useBootstrapWorkspaceProject()
  return {
    isPending: bootstrap.isPending,
    create: ({ workspaceId, name, description, graph, appType }: {
      workspaceId: string
      name: string
      description: string
      graph: WorkflowProject
      appType?: ProjectAppType
    }): Promise<CreatedProject> => bootstrap.mutateAsync({
      workspaceId,
      data: {
        project: { name, slug: `${studioSlug(name)}-${Date.now().toString(36)}`, description, app_type: appType ?? 'workflow' },
        workflow: { name, description, graph },
      },
    }),
  }
}

export function ProjectCreateForm({
  workspaceId,
  template,
  initialName = '',
  onCreated,
  onCancel,
}: {
  workspaceId: string
  template: StudioTemplateId
  initialName?: string
  onCreated: (result: CreatedProject) => void
  onCancel: () => void
}) {
  const creation = useProjectCreation()
  const [name, setName] = useState(initialName)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    const trimmed = name.trim()
    if (!trimmed) {
      setError('请输入项目名称。')
      return
    }
    setError(null)
    try {
      const result = await creation.create({
        workspaceId,
        name: trimmed,
        description: '由工作区模板创建',
        appType: studioAppTypeForTemplate(template),
        graph: studioGraphForTemplate(template, trimmed),
      })
      onCreated(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建失败')
    }
  }

  return <form onSubmit={(event) => { event.preventDefault(); void submit() }} className="space-y-4">
    <label className="block space-y-2 text-sm"><span>项目名称</span><Input value={name} onChange={(event) => setName(event.target.value)} autoFocus disabled={creation.isPending} /></label>
    {error ? <p className="text-xs text-destructive" role="alert">{error}</p> : null}
    <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onCancel} disabled={creation.isPending}>取消</Button><Button type="submit" disabled={creation.isPending}>{creation.isPending ? '正在创建…' : '创建并打开'}</Button></div>
  </form>
}
