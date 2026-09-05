'use client'

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import {
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  AlertCircle,
  Bell,
  CheckCircle2,
  Clock3,
  Inbox,
  ListFilter,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react'

import { TasksPane } from '@/components/action-center/tasks-pane'
import { NotificationsPane } from '@/components/action-center/notifications-pane'
import { ControlActionsLedger } from '@/components/control/control-actions-ledger'
import { ApprovalQueueDetail, QueueDetail } from '@/components/inbox/queue-detail'
import { BACKEND_HINT, ErrorState, LoadingState } from '@/components/shell/data-states'
import { ACTION_CENTER_TABS, RouteTabs } from '@/components/shell/route-tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  useGovernedWorkspaces,
  useInfiniteControlActions,
  useInfiniteNotificationLogs,
  useInfiniteTasks,
  useOperationsInbox,
} from '@/lib/api/hooks'
import type {
  CollectionTask,
  ControlActionRecord,
  NotificationLog,
  OperationsWorkItem,
} from '@/lib/api/types'
import { resolveApprovalAvailability, shouldIgnoreInboxShortcut } from '@/lib/inbox/workbench-state'
import { formatRelative } from '@/lib/format'
import { cn } from '@/lib/utils'

type QueueSection = 'blocked' | 'waiting' | 'review'
type QueueFilter = 'all' | QueueSection
type ActionCenterTab = 'pending' | 'tasks' | 'notifications' | 'controls'

function isActionCenterTab(value: string | null): value is ActionCenterTab {
  return value === 'pending' || value === 'tasks' || value === 'notifications' || value === 'controls'
}

interface QueueItem {
  id: string
  groupKey: string
  section: QueueSection
  eyebrow: string
  title: string
  summary: string
  status: string
  createdAt: string
  href: string
  hrefLabel: string
  sourceId?: string
  sourceName?: string
  occurrenceCount: number
  detailLabel: string
  detailValue: string
  approval?: OperationsWorkItem
}

const SECTION_META: Record<
  QueueSection,
  { label: string; description: string; icon: ReactNode; dot: string; iconTone: string }
> = {
  blocked: {
    label: '阻塞',
    description: '执行已经中断，需要先排除错误。',
    icon: <TriangleAlert className="size-4" />,
    dot: 'bg-destructive',
    iconTone: 'bg-destructive/10 text-destructive',
  },
  waiting: {
    label: '等待',
    description: '任务或通知尚未完成，需要继续推进。',
    icon: <Clock3 className="size-4" />,
    dot: 'bg-warning',
    iconTone: 'bg-warning/10 text-warning',
  },
  review: {
    label: '复核',
    description: '控制建议还没有形成恢复结论。',
    icon: <ShieldCheck className="size-4" />,
    dot: 'bg-info',
    iconTone: 'bg-info/10 text-info',
  },
}

const SECTION_ORDER: QueueSection[] = ['blocked', 'waiting', 'review']

function isQueueFilter(value: string | null): value is QueueFilter {
  return value === 'all' || value === 'blocked' || value === 'waiting' || value === 'review'
}

function isNotificationAttention(log: NotificationLog) {
  const delivery = log.status.toLowerCase()
  return delivery.includes('fail') || delivery.includes('error') || log.ack_status === 'pending'
}

function compact(value?: string | null, fallback = '暂无补充信息') {
  return value?.replace(/\s+/g, ' ').trim() || fallback
}

function signature(value?: string | null) {
  return compact(value, 'none').toLowerCase().slice(0, 120)
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

function evidenceText(evidence: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = evidence[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function taskToQueueItem(task: CollectionTask, waiting = false): QueueItem {
  const title = task.source_name ?? `数据源 ${shortId(task.source_id)}`
  const summary = waiting
    ? '任务仍在队列中，请确认执行节点和并发容量。'
    : compact(task.error_message, '任务执行失败，请打开工作项查看运行上下文。')

  return {
    id: `task-${task.id}`,
    groupKey: waiting
      ? `task:pending:${task.source_name ?? task.source_id}`
      : `task:failed:${task.source_name ?? task.source_id}`,
    section: waiting ? 'waiting' : 'blocked',
    eyebrow: waiting ? '等待任务' : '失败运行',
    title,
    summary,
    status: task.status,
    createdAt: task.updated_at,
    href: `/tasks/${task.id}`,
    hrefLabel: '打开工作项',
    sourceId: task.source_id,
    sourceName: task.source_name,
    occurrenceCount: 1,
    detailLabel: '触发方式',
    detailValue: task.trigger_type,
  }
}

function notificationToQueueItem(log: NotificationLog): QueueItem {
  const failed = /fail|error/i.test(log.status)
  const summary = failed
    ? compact(log.error_message, '通知投递失败，请检查通知规则和目标连接。')
    : '通知已送达，但仍在等待业务确认。'

  return {
    id: `notification-${log.id}`,
    groupKey: `notification:${log.rule_id}:${failed ? 'failed' : 'pending'}:${signature(log.error_message)}`,
    section: failed ? 'blocked' : 'waiting',
    eyebrow: failed ? '通知失败' : '等待确认',
    title: failed ? '通知投递失败' : '通知等待确认',
    summary,
    status: failed ? 'failed' : log.ack_status,
    createdAt: log.created_at,
    href: '/inbox?tab=notifications',
    hrefLabel: '打开通知规则',
    occurrenceCount: 1,
    detailLabel: '规则',
    detailValue: shortId(log.rule_id),
  }
}

function controlToQueueItem(action: ControlActionRecord): QueueItem {
  return {
    id: `control-${action.id}`,
    groupKey: `control:${action.source_id}:${action.action_type}:${signature(action.reason)}`,
    section: 'review',
    eyebrow: action.executed ? '控制动作' : '控制建议',
    title: action.action_type,
    summary: compact(action.reason, '控制动作尚未形成恢复结果，需要继续观察并复核。'),
    status: action.state,
    createdAt: action.created_at,
    href: '/inbox?tab=controls',
    hrefLabel: '打开控制证据',
    sourceId: action.source_id,
    occurrenceCount: 1,
    detailLabel: '执行模式',
    detailValue: action.executed ? '已执行，等待结果' : '建议，尚未执行',
  }
}

function approvalToQueueItem(approval: OperationsWorkItem, workspaceName: string): QueueItem {
  const title =
    evidenceText(approval.evidence, ['title', 'action', 'operation']) ??
    'Operations Agent 请求批准'
  const summary =
    compact(approval.reason, '') ||
    evidenceText(approval.evidence, ['summary', 'description', 'reason']) ||
    '智能体已暂停执行，等待人工决定。'

  return {
    id: `approval-${approval.id}`,
    groupKey: `approval:${approval.id}`,
    section: 'review',
    eyebrow: `人工审批 · ${workspaceName}`,
    title,
    summary,
    status: approval.status,
    createdAt: approval.created_at,
    href: '/operations-agents',
    hrefLabel: '打开智能体',
    sourceName: workspaceName,
    occurrenceCount: 1,
    detailLabel: '风险级别',
    detailValue: `${approval.priority} · ${approval.severity}`,
    approval,
  }
}

function groupQueueItems(items: QueueItem[]) {
  const grouped = new Map<string, QueueItem>()

  for (const item of items) {
    const current = grouped.get(item.groupKey)
    if (!current) {
      grouped.set(item.groupKey, { ...item })
      continue
    }

    const occurrenceCount = current.occurrenceCount + item.occurrenceCount
    const newest =
      new Date(item.createdAt).getTime() > new Date(current.createdAt).getTime() ? item : current
    grouped.set(item.groupKey, { ...newest, occurrenceCount })
  }

  return [...grouped.values()].sort((left, right) => {
    const sectionDelta = SECTION_ORDER.indexOf(left.section) - SECTION_ORDER.indexOf(right.section)
    if (sectionDelta !== 0) return sectionDelta
    return new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime()
  })
}

function QueueRow({
  item,
  selected,
  onSelect,
}: {
  item: QueueItem
  selected: boolean
  onSelect: () => void
}) {
  const meta = SECTION_META[item.section]

  return (
    <button
      id={`inbox-row-${item.id}`}
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onSelect}
      onFocus={onSelect}
      className={cn(
        'group relative grid min-h-16 w-full grid-cols-[auto_minmax(0,1fr)_auto] gap-2.5 border-b px-3 py-2.5 text-left outline-none transition-colors',
        'touch-manipulation [contain-intrinsic-size:72px] [content-visibility:auto]',
        'hover:bg-muted/45 focus-visible:z-10 focus-visible:ring-1 focus-visible:ring-ring/45 focus-visible:ring-inset',
        selected && 'bg-muted/70',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-2 left-0 w-0.5 rounded-r-full',
          meta.dot,
          selected ? 'opacity-100' : 'opacity-0',
        )}
      />
      <span className={cn('mt-0.5 grid size-7 place-items-center rounded-md', meta.iconTone)}>
        {meta.icon}
      </span>
      <span className="min-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{item.title}</span>
          {item.occurrenceCount > 1 ? (
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
              ×{item.occurrenceCount}
            </span>
          ) : null}
        </span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{item.summary}</span>
        <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="shrink-0 font-medium text-foreground/65">{item.eyebrow}</span>
          <span aria-hidden="true">·</span>
          <span className="truncate">
            {item.sourceName ?? (item.sourceId ? shortId(item.sourceId) : item.detailValue)}
          </span>
        </span>
      </span>
      <span className="pt-0.5 text-[11px] tabular-nums text-muted-foreground">
        {formatRelative(item.createdAt)}
      </span>
    </button>
  )
}



function InboxLoadingFallback() {
  return (
    <div
      data-testid="inbox-workbench"
      className="flex min-h-[calc(100dvh-3.5rem)] w-full flex-col lg:h-[calc(100dvh-3.5rem)] lg:overflow-hidden"
    >
      <div className="flex min-h-14 items-center border-b px-4">
        <h1 className="text-base font-semibold">任务与通知</h1>
      </div>
      <div className="flex-1 p-4">
        <LoadingState rows={5} />
      </div>
    </div>
  )
}

function InboxContent() {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const searchParamsKey = searchParams.toString()
  const requestedTab = searchParams.get('tab')
  const activeTab = isActionCenterTab(requestedTab) ? requestedTab : 'pending'
  const pendingActive = activeTab === 'pending'
  const searchRef = useRef<HTMLInputElement>(null)
  const initialView = searchParams.get('view')
  const [filter, setFilter] = useState<QueueFilter>(isQueueFilter(initialView) ? initialView : 'all')
  const [search, setSearch] = useState(searchParams.get('q') ?? '')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const pendingScrollTopRef = useRef(0)
  const tasksScrollTopRef = useRef(0)
  const notificationsScrollTopRef = useRef(0)
  const controlsScrollTopRef = useRef(0)
  const controlsRegionRef = useRef<HTMLElement>(null)
  const workspaces = useGovernedWorkspaces({ enabled: pendingActive })
  const requestedWorkspaceId = searchParams.get('workspace')
  const workspaceId =
    workspaces.data?.find((workspace) => workspace.id === requestedWorkspaceId)?.id ??
    workspaces.data?.[0]?.id ??
    null
  const workspaceName =
    workspaces.data?.find((workspace) => workspace.id === workspaceId)?.name ?? 'Workspace'
  const operationsInbox = useOperationsInbox(workspaceId, 'open', { enabled: pendingActive })
  const approvalAvailability = resolveApprovalAvailability({
    workspaceLoading: workspaces.isLoading,
    workspaceError: workspaces.isError,
    workspaceCount: workspaces.data?.length ?? 0,
    workspaceId,
    inboxLoading: operationsInbox.isLoading,
    inboxError: operationsInbox.isError,
  })

  const failedTasks = useInfiniteTasks({ status: 'failed', limit: 100 }, { enabled: pendingActive })
  const pendingTasks = useInfiniteTasks({ status: 'pending', limit: 100 }, { enabled: pendingActive })
  const notificationLogs = useInfiniteNotificationLogs({ limit: 100 }, { enabled: pendingActive })
  const pendingControlActions = useInfiniteControlActions(
    { outcome: 'pending', limit: 100 },
    { enabled: pendingActive },
  )

  const failed = useMemo(
    () => failedTasks.data?.pages.flatMap((page) => page.data) ?? [],
    [failedTasks.data?.pages],
  )
  const pending = useMemo(
    () => pendingTasks.data?.pages.flatMap((page) => page.data) ?? [],
    [pendingTasks.data?.pages],
  )
  const notifications = useMemo(
    () =>
      (notificationLogs.data?.pages.flatMap((page) => page.data) ?? []).filter(
        isNotificationAttention,
      ),
    [notificationLogs.data?.pages],
  )
  const controls = useMemo(
    () => pendingControlActions.data?.pages.flatMap((page) => page.data) ?? [],
    [pendingControlActions.data?.pages],
  )
  const approvals = useMemo(
    () => operationsInbox.data?.data?.filter((item) => item.type === 'approval') ?? [],
    [operationsInbox.data],
  )

  const rawCounts: Record<QueueFilter, number> = {
    all: failed.length + pending.length + notifications.length + controls.length + approvals.length,
    blocked: failed.length + notifications.filter((log) => /fail|error/i.test(log.status)).length,
    waiting: pending.length + notifications.filter((log) => !/fail|error/i.test(log.status)).length,
    review: controls.length + approvals.length,
  }

  const queueItems = useMemo(
    () =>
      groupQueueItems([
        ...failed.map((task) => taskToQueueItem(task)),
        ...pending.map((task) => taskToQueueItem(task, true)),
        ...notifications.map(notificationToQueueItem),
        ...controls.map(controlToQueueItem),
        ...approvals.map((approval) => approvalToQueueItem(approval, workspaceName)),
      ]),
    [approvals, controls, failed, notifications, pending, workspaceName],
  )

  const filteredItems = useMemo(() => {
    const query = search.trim().toLowerCase()
    return queueItems.filter((item) => {
      if (filter !== 'all' && item.section !== filter) return false
      if (!query) return true
      return [item.title, item.summary, item.eyebrow, item.sourceName, item.sourceId, item.detailValue]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(query))
    })
  }, [filter, queueItems, search])

  const selectedItem = filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? null

  const replaceQueueQuery = useCallback(
    (nextFilter: QueueFilter, nextSearch: string) => {
      const params = new URLSearchParams(searchParamsKey)
      if (nextFilter === 'all') params.delete('view')
      else params.set('view', nextFilter)
      if (nextSearch.trim()) params.set('q', nextSearch.trim())
      else params.delete('q')

      const currentQuery = searchParamsKey
      const nextQuery = params.toString()
      if (currentQuery === nextQuery) return
      router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false })
    },
    [pathname, router, searchParamsKey],
  )

  const selectWorkspace = useCallback(
    (nextWorkspaceId: string) => {
      const params = new URLSearchParams(searchParamsKey)
      params.set('workspace', nextWorkspaceId)
      const nextQuery = params.toString()
      router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false })
    },
    [pathname, router, searchParamsKey],
  )

  useEffect(() => {
    if (!pendingActive) return
    const params = new URLSearchParams(searchParamsKey)
    const view = params.get('view')
    const nextFilter = isQueueFilter(view) ? view : 'all'
    const nextSearch = params.get('q') ?? ''
    setFilter((current) => (current === nextFilter ? current : nextFilter))
    setSearch((current) => (current === nextSearch ? current : nextSearch))
  }, [pendingActive, searchParamsKey])

  useEffect(() => {
    if (isActionCenterTab(requestedTab)) return
    const params = new URLSearchParams(searchParamsKey)
    params.set('tab', 'pending')
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }, [pathname, requestedTab, router, searchParamsKey])

  useEffect(() => {
    if (!pendingActive) return
    const timeout = window.setTimeout(() => replaceQueueQuery(filter, search), 180)
    return () => window.clearTimeout(timeout)
  }, [filter, pendingActive, replaceQueueQuery, search])

  useEffect(() => {
    if (!pendingActive) return
    if (selectedItem && selectedItem.id !== selectedId) setSelectedId(selectedItem.id)
    if (!selectedItem && selectedId) setSelectedId(null)
  }, [pendingActive, selectedId, selectedItem])

  useEffect(() => {
    if (!pendingActive || !selectedItem) return
    document
      .getElementById(`inbox-row-${selectedItem.id}`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [pendingActive, selectedItem])

  useLayoutEffect(() => {
    if (!pendingActive) return
    const viewport = document.querySelector<HTMLElement>(
      '[data-testid="inbox-queue-scroll"] [data-slot="scroll-area-viewport"]',
    )
    if (viewport) viewport.scrollTop = pendingScrollTopRef.current
  }, [pendingActive])

  useLayoutEffect(() => {
    if (activeTab !== 'controls') return
    controlsRegionRef.current?.scrollTo({ top: controlsScrollTopRef.current })
  }, [activeTab])

  useEffect(() => {
    if (!pendingActive) return
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null
      const targetElement = target instanceof HTMLElement ? target : null
      const withinQueueOption = Boolean(
        target?.closest('[role="listbox"][aria-label="待处理信号"] [role="option"]'),
      )
      const isInteractive =
        !withinQueueOption &&
        shouldIgnoreInboxShortcut({
          tagName: target?.tagName,
          isContentEditable: targetElement?.isContentEditable,
          withinInteractive: Boolean(
            target?.closest(
              'a,button,input,select,textarea,summary,[contenteditable="true"],[role="button"],[role="link"],[role="menuitem"],[role="option"],[role="tab"],[tabindex]:not([tabindex="-1"])',
            ),
          ),
        })

      if (isInteractive) {
        if (event.key === 'Escape' && target === searchRef.current) {
          setSearch('')
          searchRef.current?.blur()
        }
        return
      }

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select()
        return
      }

      const currentIndex = selectedItem
        ? filteredItems.findIndex((item) => item.id === selectedItem.id)
        : -1
      const moveDown = event.key.toLowerCase() === 'j' || event.key === 'ArrowDown'
      const moveUp = event.key.toLowerCase() === 'k' || event.key === 'ArrowUp'

      if ((moveDown || moveUp) && filteredItems.length > 0) {
        event.preventDefault()
        const delta = moveDown ? 1 : -1
        const nextIndex = Math.min(Math.max(currentIndex + delta, 0), filteredItems.length - 1)
        setSelectedId(filteredItems[nextIndex].id)
      }

      if (event.key === 'Enter' && selectedItem && !selectedItem.approval) {
        event.preventDefault()
        router.push(selectedItem.href)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [filteredItems, pendingActive, router, selectedItem])

  const queries = [failedTasks, pendingTasks, notificationLogs, pendingControlActions]
  const isInitialLoading =
    queries.every((query) => query.isLoading) &&
    (approvalAvailability !== 'ready' || approvals.length === 0)
  const isTotalFailure =
    queries.every((query) => query.isError) &&
    (approvalAvailability === 'workspace_error' ||
      approvalAvailability === 'inbox_error' ||
      approvalAvailability === 'no_workspace')
  const partialFailures = [
    failedTasks.isError ? '失败任务' : null,
    pendingTasks.isError ? '等待任务' : null,
    notificationLogs.isError ? '通知记录' : null,
    pendingControlActions.isError ? '控制结果' : null,
    workspaces.isError ? 'Workspace' : null,
    operationsInbox.isError ? '人工审批' : null,
  ].filter(Boolean)
  const hasMoreSignals = queries.some((query) => query.hasNextPage)
  const isFetchingNextPage = queries.some((query) => query.isFetchingNextPage)

  const refetchAll = () => {
    void Promise.all([
      ...queries.map((query) => query.refetch()),
      workspaces.refetch(),
      operationsInbox.refetch(),
    ])
  }

  const loadMoreSignals = () => {
    void Promise.all(
      queries.map((query) =>
        query.hasNextPage ? query.fetchNextPage() : Promise.resolve(undefined),
      ),
    )
  }

  const visibleOccurrences = filteredItems.reduce((total, item) => total + item.occurrenceCount, 0)
  const filters: Array<{ key: QueueFilter; label: string }> = [
    { key: 'all', label: '全部' },
    { key: 'blocked', label: '阻塞' },
    { key: 'waiting', label: '等待' },
    { key: 'review', label: '复核' },
  ]
  const approvalNotice = pendingActive
    ? approvalAvailability === 'loading'
      ? '正在读取 Workspace 人工审批…'
      : approvalAvailability === 'no_workspace'
        ? '尚未加入 Workspace，人工审批目前不可用。'
        : null
    : null

  return (
    <div
      data-testid="inbox-workbench"
      className="flex min-h-[calc(100dvh-3.5rem)] w-full flex-col bg-background lg:h-[calc(100dvh-3.5rem)] lg:overflow-hidden"
    >
      <header
        data-testid="action-center-header"
        className="flex shrink-0 flex-col gap-2 border-b px-3 py-2 md:min-h-14 md:flex-row md:items-center md:gap-3 md:px-4 md:py-0"
      >
        <div className="flex min-w-0 items-baseline gap-2">
          <h1 className="truncate text-base font-semibold">任务与通知</h1>
          {pendingActive ? (
            <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
              {rawCounts.all}
            </span>
          ) : null}
        </div>

        <RouteTabs
          tabs={ACTION_CENTER_TABS}
          className="order-3 rounded-md bg-transparent p-0 md:order-none md:ml-1 [&_a]:rounded-md [&_a]:px-3 [&_a]:py-1.5 [&_a]:text-xs"
        />

        {activeTab === 'pending' ? (
          <div className="ml-auto flex w-full items-center gap-1.5 md:w-auto">
            {workspaceId ? (
              <select
                value={workspaceId}
                onChange={(event) => selectWorkspace(event.target.value)}
                aria-label="选择人工审批 Workspace"
                className="h-9 max-w-40 shrink-0 rounded-md border bg-background px-2 text-xs md:h-8"
              >
                {workspaces.data?.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            ) : null}
            <div className="relative min-w-0 flex-1 md:w-64 md:flex-none">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                ref={searchRef}
                name="inbox-search"
                autoComplete="off"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索队列…"
                aria-label="搜索当前队列"
                className="h-9 rounded-md border-transparent bg-muted/55 pr-8 pl-8 text-xs focus-visible:border-ring md:h-8"
              />
              {search ? (
                <button
                  type="button"
                  aria-label="清除搜索"
                  onClick={() => {
                    setSearch('')
                    searchRef.current?.focus()
                  }}
                  className="absolute top-1/2 right-1.5 grid size-7 -translate-y-1/2 place-items-center rounded hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:size-6"
                >
                  <X aria-hidden="true" className="size-3" />
                </button>
              ) : (
                <Kbd className="absolute top-1/2 right-1.5 hidden h-4 min-w-4 -translate-y-1/2 px-1 text-[9px] lg:inline-flex">
                  Ctrl F
                </Kbd>
              )}
            </div>

            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="同步任务与通知"
              onClick={refetchAll}
              className="shrink-0"
            >
              <RefreshCw aria-hidden="true" className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </header>

      {pendingActive && partialFailures.length > 0 ? (
        <div className="flex shrink-0 items-center gap-2 border-b bg-destructive/5 px-4 py-2 text-xs text-destructive">
          <AlertCircle aria-hidden="true" className="size-3.5 shrink-0" />
          {partialFailures.join('、')}暂时无法读取，其余信号仍可处理。
        </div>
      ) : null}

      {pendingActive && approvalNotice ? (
        <div role="status" className="shrink-0 border-b bg-muted/25 px-4 py-2 text-xs text-muted-foreground">
          {approvalNotice}
        </div>
      ) : null}

      {activeTab === 'tasks' ? (
        <TasksPane scrollTopRef={tasksScrollTopRef} />
      ) : activeTab === 'notifications' ? (
        <NotificationsPane scrollTopRef={notificationsScrollTopRef} />
      ) : activeTab === 'controls' ? (
        <section
          ref={controlsRegionRef}
          aria-label="控制记录"
          className="min-h-0 flex-1 overflow-auto p-4"
          onScroll={(event) => {
            controlsScrollTopRef.current = event.currentTarget.scrollTop
          }}
        >
          <ControlActionsLedger />
        </section>
      ) : isInitialLoading ? (
        <div className="min-h-0 flex-1 p-4">
          <LoadingState rows={5} />
        </div>
      ) : isTotalFailure ? (
        <div className="grid min-h-0 flex-1 place-items-center p-4">
          <ErrorState
            message="待处理信号暂时无法读取。"
            hint={BACKEND_HINT}
            action={
              <Button variant="outline" size="sm" onClick={refetchAll}>
                重新读取
              </Button>
            }
          />
        </div>
      ) : (
        <main
          aria-label="任务与通知处理台"
          className="grid min-h-0 flex-1 lg:grid-cols-[minmax(21rem,0.84fr)_minmax(0,1.65fr)]"
        >
          <section aria-label="待处理信号队列" className="flex min-h-[30rem] min-w-0 flex-col border-b lg:min-h-0 lg:border-r lg:border-b-0">
            <div className="flex min-h-12 shrink-0 items-center gap-1 overflow-x-auto border-b px-2">
              <ListFilter aria-hidden="true" className="mx-1 size-4 shrink-0 text-muted-foreground" />
              {filters.map((item) => (
                <Button
                  key={item.key}
                  size="sm"
                  variant={filter === item.key ? 'secondary' : 'ghost'}
                  aria-pressed={filter === item.key}
                  className="h-10 shrink-0 gap-1.5 rounded-md px-2.5 text-xs md:h-8"
                  onClick={() => setFilter(item.key)}
                >
                  {item.key !== 'all' ? (
                    <span
                      aria-hidden="true"
                      className={cn('size-1.5 rounded-full', SECTION_META[item.key].dot)}
                    />
                  ) : null}
                  {item.label}
                  <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                    {rawCounts[item.key]}
                  </span>
                </Button>
              ))}
            </div>

            <div className="flex min-h-11 shrink-0 items-center justify-between gap-3 border-b px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">
                  已加载 {visibleOccurrences} 条信号
                  <span className="ml-1.5 font-normal text-muted-foreground">
                    · {filteredItems.length} 个主题
                  </span>
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  按严重程度排列，重复信号自动合并
                </p>
              </div>
              <Inbox aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
            </div>

            <ScrollArea
              data-testid="inbox-queue-scroll"
              className="min-h-0 flex-1 overscroll-contain"
              onScrollCapture={(event) => {
                pendingScrollTopRef.current = (event.target as HTMLElement).scrollTop
              }}
            >
              {filteredItems.length === 0 ? (
                <div className="grid min-h-80 place-items-center px-6 text-center">
                  <div>
                    <span className="mx-auto grid size-10 place-items-center rounded-full bg-muted text-muted-foreground">
                      <CheckCircle2 aria-hidden="true" className="size-5" />
                    </span>
                    <p className="mt-3 text-sm font-medium">当前视图已经清空</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {search ? '换个关键词，或清除搜索条件。' : '没有需要处理的信号。'}
                    </p>
                  </div>
                </div>
              ) : (
                <div role="listbox" aria-label="待处理信号">
                  {SECTION_ORDER.map((section) => {
                    const sectionItems = filteredItems.filter((item) => item.section === section)
                    if (sectionItems.length === 0) return null
                    const meta = SECTION_META[section]

                    return (
                      <div key={section}>
                        <div className="sticky top-0 z-[1] flex h-8 items-center gap-2 border-b bg-background/95 px-3 backdrop-blur">
                          <span aria-hidden="true" className={cn('size-1.5 rounded-full', meta.dot)} />
                          <span className="text-[11px] font-semibold text-muted-foreground">
                            {meta.label}
                          </span>
                          <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                            {sectionItems.reduce((total, item) => total + item.occurrenceCount, 0)}
                          </span>
                        </div>
                        {sectionItems.map((item) => (
                          <QueueRow
                            key={item.id}
                            item={item}
                            selected={selectedItem?.id === item.id}
                            onSelect={() => setSelectedId(item.id)}
                          />
                        ))}
                      </div>
                    )
                  })}

                  {hasMoreSignals ? (
                    <div className="border-t p-3 text-center">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={loadMoreSignals}
                        disabled={isFetchingNextPage}
                        className="min-w-36"
                      >
                        {isFetchingNextPage ? (
                          <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
                        ) : null}
                        {isFetchingNextPage ? '正在加载…' : '加载更多信号'}
                      </Button>
                    </div>
                  ) : null}
                </div>
              )}
            </ScrollArea>
          </section>

          <aside
            aria-label="所选信号详情"
            className="flex min-h-[32rem] min-w-0 flex-col bg-muted/10 lg:min-h-0"
          >
            {selectedItem ? (
              selectedItem.approval ? (
                <ApprovalQueueDetail key={selectedItem.id} item={selectedItem} />
              ) : (
                <QueueDetail
                  item={selectedItem}
                  meta={SECTION_META[selectedItem.section]}
                  createdAtLabel={formatRelative(selectedItem.createdAt)}
                />
              )
            ) : (
              <div className="grid min-h-80 flex-1 place-items-center px-6 text-center">
                <div>
                  <Bell aria-hidden="true" className="mx-auto size-5 text-muted-foreground" />
                  <p className="mt-3 text-sm font-medium">选择一个主题查看上下文</p>
                  <p className="mt-1 text-xs text-muted-foreground">详情和可执行入口会显示在这里。</p>
                </div>
              </div>
            )}
          </aside>
        </main>
      )}
    </div>
  )
}

export default function InboxPage() {
  return (
    <Suspense fallback={<InboxLoadingFallback />}>
      <InboxContent />
    </Suspense>
  )
}
