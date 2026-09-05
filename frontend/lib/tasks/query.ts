const TASK_STATUSES = new Set(['running', 'completed', 'failed', 'pending'])

export function normalizeTaskStatus(value: string | null): string {
  return value && TASK_STATUSES.has(value) ? value : ''
}

export function normalizeTaskPage(value: string | null): number {
  const page = Number(value)
  return Number.isInteger(page) && page > 0 ? page : 1
}

export function queryForTaskStatus(currentQuery: string, nextStatus: string): string {
  const params = new URLSearchParams(currentQuery)
  const status = normalizeTaskStatus(nextStatus)
  if (status) params.set('status', status)
  else params.delete('status')
  params.delete('page')
  return params.toString()
}

export function queryForTaskPage(currentQuery: string, nextPage: number): string {
  const params = new URLSearchParams(currentQuery)
  const page = Number.isInteger(nextPage) && nextPage > 1 ? nextPage : 1
  if (page > 1) params.set('page', String(page))
  else params.delete('page')
  return params.toString()
}

export function pathWithQuery(pathname: string, query: string): string {
  return query ? `${pathname}?${query}` : pathname
}

export function taskDetailPath(taskId: string, returnTo: string): string {
  const params = new URLSearchParams({ returnTo: normalizeTaskReturnPath(returnTo) })
  return `/tasks/${encodeURIComponent(taskId)}?${params.toString()}`
}

export function normalizeTaskReturnPath(value: string | null): string {
  if (!value?.startsWith('/')) return '/tasks'
  const base = new URL('https://opencli.local/tasks')
  const target = new URL(value, base)
  if (target.origin !== base.origin) return '/tasks'
  const isTaskInbox = target.pathname === '/inbox' &&
    target.searchParams.getAll('tab').length === 1 && target.searchParams.get('tab') === 'tasks'
  if (target.pathname !== '/tasks' && !isTaskInbox) return '/tasks'
  return `${target.pathname}${target.search}`
}
