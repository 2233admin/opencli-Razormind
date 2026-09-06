export type RunNavigationContext = {
  workspace?: string
  project?: string
  workflow?: string
  run?: string
  trace?: string
  conversation?: string
}

type RunNavigationSection = 'operations' | 'evidence' | 'data' | 'workflow'
type ProjectNavigationSection = 'overview' | 'orchestration' | 'data' | 'evidence' | 'relationships' | 'apiAccess' | 'operations'

type ProjectNavigationScope = {
  workspace?: string | null
  project?: string | null
  workflow?: string | null
}

export type TraceHistoryEntry = {
  sourceHref: string
  targetHref: string
}

export function parseRunNavigation(search: URLSearchParams): RunNavigationContext {
  return {
    workspace: search.get('workspace')?.trim() || undefined,
    project: search.get('project')?.trim() || undefined,
    workflow: search.get('workflow')?.trim() || undefined,
    run: search.get('run')?.trim() || undefined,
    trace: search.get('trace')?.trim() || undefined,
    ...(search.get('conversation')?.trim() ? { conversation: search.get('conversation')!.trim() } : {}),
  }
}

export function buildRunUrl(section: RunNavigationSection, context: RunNavigationContext, projectId?: string) {
  const project = projectId ?? context.project
  const projectPath = project ? encodeURIComponent(project) : null
  const scopedContext = { ...context, project }
  const query = buildNavigationSearch(scopedContext)
  const suffix = query.size ? `?${query.toString()}` : ''
  if (section === 'workflow') return `/studio/workflow${suffix}`
  if (section === 'operations') return projectPath ? `/studio/projects/${projectPath}/operations${suffix}` : null
  return projectPath ? `/studio/projects/${projectPath}/${section}${suffix}` : null
}

/** Opens the shared Agent Dock with only route-derived, scoped identifiers. */
export function buildScopedAgentUrl(pathname: string, context: RunNavigationContext) {
  if (!context.workspace || !context.project || !context.workflow || !context.run) return null
  const query = new URLSearchParams({
    agent: '1',
    workspace: context.workspace,
    project: context.project,
    workflow: context.workflow,
    run: context.run,
  })
  return `${pathname}?${query.toString()}`
}

export function buildProjectNavigationUrl(
  section: ProjectNavigationSection,
  destination: ProjectNavigationScope,
  current: RunNavigationContext,
) {
  const project = destination.project?.trim()
  const workspace = destination.workspace?.trim()
  const workflow = destination.workflow?.trim()
  if (!project || !workspace) return null
  const projectPath = encodeURIComponent(project)

  const context: RunNavigationContext = { workspace, project, workflow }
  if (hasMatchingRunScope(current, context)) {
    context.run = current.run
    context.trace = current.trace
    context.conversation = current.conversation
  }

  const query = buildNavigationSearch(context).toString()
  if (section === 'overview') return `/studio/projects/${projectPath}?${query}`
  if (section === 'orchestration') return buildRunUrl('workflow', context)
  if (section === 'apiAccess') return `/studio/projects/${projectPath}/api?${query}`
  if (section === 'relationships') return `/studio/projects/${projectPath}/relationships?${query}`
  return buildRunUrl(section, context)
}

export function hasMatchingRunScope(current: RunNavigationContext, destination: RunNavigationContext) {
  if (!current.run && !current.trace) return false
  return Boolean(
    current.workspace && destination.workspace && current.workspace === destination.workspace
    && current.project && destination.project && current.project === destination.project
    && current.workflow && destination.workflow && current.workflow === destination.workflow,
  )
}

export function clearRunNavigation(search: URLSearchParams) {
  const next = new URLSearchParams(search)
  next.delete('run')
  next.delete('trace')
  return next
}

export function traceCloseAction(currentHref: string, entry: TraceHistoryEntry | null) {
  return entry?.targetHref === currentHref ? 'back' as const : 'replace' as const
}

export function shouldDiscardTraceHistoryEntry(currentHref: string, entry: TraceHistoryEntry | null) {
  return Boolean(entry && currentHref !== entry.sourceHref && currentHref !== entry.targetHref)
}

function buildNavigationSearch(context: RunNavigationContext) {
  const query = new URLSearchParams()
  for (const key of ['workspace', 'project', 'workflow', 'run', 'trace', 'conversation'] as const) {
    const value = context[key]?.trim()
    if (value) query.set(key, value)
  }
  return query
}
