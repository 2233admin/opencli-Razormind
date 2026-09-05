export type NavigationBreadcrumb = { label: string; href?: string }

const PROJECT_PAGES: Record<string, string> = {
  data: '数据工作台',
  evidence: '逻辑与证据',
  relationships: '证据关系',
  api: 'API / MCP',
  operations: '运行记录',
}

/** Parent links deliberately return to a broader scope without a stale run filter. */
export function navigationBreadcrumbs(
  pathname: string,
  search: URLSearchParams,
  routeLabels: Record<string, string>,
): NavigationBreadcrumb[] {
  if (pathname === '/dashboard') return [{ label: '概览' }]
  const crumbs: NavigationBreadcrumb[] = [{ label: '概览', href: '/dashboard' }]
  const workspace = search.get('workspace')?.trim()
  const workspaceQuery = new URLSearchParams()
  if (workspace) workspaceQuery.set('workspace', workspace)
  const suffix = workspaceQuery.size ? `?${workspaceQuery}` : ''
  const projectMatch = pathname.match(/^\/studio\/projects\/([^/]+)(?:\/([^/]+))?\/?$/)
  const isEditor = pathname === '/studio/workflow'

  if (projectMatch || isEditor || pathname === '/studio/new') {
    crumbs.push({ label: '项目', href: `/studio${suffix}` })
    const project = projectMatch?.[1] ?? (isEditor ? search.get('project')?.trim() : undefined)
    const section = isEditor ? '业务编排' : projectMatch?.[2] ? PROJECT_PAGES[projectMatch[2]] ?? '项目详情' : null
    if (project && workspace) {
      // A project captured from a pathname is already URL encoded.
      const segment = projectMatch ? project : encodeURIComponent(project)
      crumbs.push({ label: '项目概览', ...(section ? { href: `/studio/projects/${segment}${suffix}` } : {}) })
    }
    if (section) crumbs.push({ label: section })
    if (pathname === '/studio/new') crumbs.push({ label: '创建项目' })
    if (projectMatch && !workspace && !section) crumbs.push({ label: '项目概览' })
    return crumbs
  }

  if (routeLabels[pathname]) return [...crumbs, { label: routeLabels[pathname] }]
  const parent = Object.keys(routeLabels)
    .sort((left, right) => right.length - left.length)
    .find((href) => pathname.startsWith(`${href}/`))
  if (parent) crumbs.push({ label: routeLabels[parent], href: parent }, { label: '详情' })
  return crumbs
}
