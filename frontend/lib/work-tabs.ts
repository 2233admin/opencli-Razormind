/** Navigation bookmarks only: the server remains the authority for every object.
 * Design reference: OpenAlice 52b51f2, tabs/store.ts and TabHost.tsx.
 */
export type WorkTab = { id: string; href: string; label: string }
export const MAX_WORK_TABS = 16
const sections: Record<string, string> = {
  overview: '项目', operations: '运行', data: '数据', evidence: '证据',
  relationships: '关系', api: 'API', workflow: '编排',
}
const contextKeys = ['workspace', 'project', 'workflow', 'run', 'trace', 'conversation', 'record', 'artifact']
const dataViews = new Set(['dataset', 'profile', 'quality', 'files', 'artifacts'])

export function workTabFromHref(href: string, workspace: string): WorkTab | null {
  // Stored navigation is untrusted. Accept only known local product routes.
  if (!href.startsWith('/') || href.startsWith('//') || href.includes('\\')) return null
  let url: URL
  try { url = new URL(href, 'https://opencli.invalid') } catch { return null }
  if (url.origin !== 'https://opencli.invalid' || url.searchParams.get('workspace') !== workspace) return null
  const match = url.pathname.match(/^\/studio\/projects\/([^/]+)(?:\/(operations|data|evidence|relationships|api))?$/)
  const editor = url.pathname === '/studio/workflow'
  if (!match && !editor) return null
  let project: string
  try { project = match ? decodeURIComponent(match[1]) : url.searchParams.get('project') ?? '' } catch { return null }
  if (!project || (editor && !url.searchParams.get('workflow'))) return null
  const query = new URLSearchParams()
  for (const key of contextKeys) {
    const value = url.searchParams.get(key)
    if (value) query.set(key, value)
  }
  query.set('project', project)
  const view = url.searchParams.get('view')
  if (match?.[2] === 'data' && view && dataViews.has(view)) query.set('view', view)
  if (query.has('conversation') && url.searchParams.get('agent') === '1') query.set('agent', '1')
  query.sort()
  const section = editor ? 'workflow' : match?.[2] ?? 'overview'
  // A trace cursor is presentation state, not a second copy of the same run.
  const id = JSON.stringify([workspace, project, section, query.get('workflow'), query.get('run'), query.get('conversation'), query.get('record'), query.get('artifact')])
  const subject = query.get('artifact') ?? query.get('conversation') ?? query.get('run') ?? project
  const label = section === 'data' && query.has('artifact') ? '产物' : sections[section]
  return { id, href: `${url.pathname}?${query}`, label: `${label} · ${subject.slice(0, 12)}` }
}

export function openWorkTab(tabs: WorkTab[], tab: WorkTab): WorkTab[] {
  const index = tabs.findIndex((entry) => entry.id === tab.id)
  if (index < 0) return [...tabs, tab].slice(-MAX_WORK_TABS)
  return tabs.map((entry, position) => position === index ? tab : entry)
}

export function restoreWorkTabs(raw: string | null, workspace: string): WorkTab[] {
  try {
    const parsed: unknown = JSON.parse(raw ?? 'null')
    if (!Array.isArray(parsed)) return []
    return parsed.slice(-MAX_WORK_TABS).reduce<WorkTab[]>((tabs, entry) => {
      const tab = typeof entry?.href === 'string' ? workTabFromHref(entry.href, workspace) : null
      return tab ? openWorkTab(tabs, tab) : tabs
    }, [])
  } catch { return [] }
}

export function closeWorkTab(tabs: WorkTab[], id: string, activeId?: string) {
  const index = tabs.findIndex((tab) => tab.id === id)
  const remaining = tabs.filter((tab) => tab.id !== id)
  return { tabs: remaining, next: id === activeId ? remaining[index] ?? remaining[index - 1] ?? null : null }
}
