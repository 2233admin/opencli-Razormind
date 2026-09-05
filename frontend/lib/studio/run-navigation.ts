export type RunNavigationContext = {
  workspace?: string
  project?: string
  workflow?: string
  run?: string
  trace?: string
}

export function parseRunNavigation(search: URLSearchParams): RunNavigationContext {
  return {
    workspace: search.get('workspace')?.trim() || undefined,
    project: search.get('project')?.trim() || undefined,
    workflow: search.get('workflow')?.trim() || undefined,
    run: search.get('run')?.trim() || undefined,
    trace: search.get('trace')?.trim() || undefined,
  }
}

export function buildRunUrl(section: 'operations' | 'evidence' | 'data' | 'workflow', context: RunNavigationContext, projectId?: string) {
  const query = new URLSearchParams()
  for (const key of ['workspace', 'project', 'workflow', 'run', 'trace'] as const) {
    const value = context[key]
    if (value) query.set(key, value)
  }
  const project = projectId ?? context.project
  if (section === 'workflow') return `/studio/workflow?${query.toString()}`
  if (section === 'operations') return project ? `/studio/projects/${project}/operations?${query.toString()}` : null
  return project ? `/studio/projects/${project}/${section}?${query.toString()}` : null
}
