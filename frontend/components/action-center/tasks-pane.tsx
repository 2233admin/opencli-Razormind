'use client'

import { ArrowUpRight } from 'lucide-react'
import Link from 'next/link'
import { useLayoutEffect, useRef, type MutableRefObject } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'

import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { StatusBadge } from '@/components/shell/status-badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useTasks } from '@/lib/api/hooks'
import { formatRelative } from '@/lib/format'

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'running', label: '运行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
  { key: 'pending', label: '等待中' },
]

export function TasksPane({ scrollTopRef }: { scrollTopRef: MutableRefObject<number> }) {
  const pathname = usePathname()
  const router = useRouter()
  const searchParams = useSearchParams()
  const searchParamsKey = searchParams.toString()
  const requestedStatus = searchParams.get('status') ?? ''
  const status = STATUS_FILTERS.some((filter) => filter.key === requestedStatus) ? requestedStatus : ''
  const regionRef = useRef<HTMLElement>(null)
  const { data, isLoading, isError, error } = useTasks(status ? { status } : undefined)
  const tasks = data?.data ?? []

  useLayoutEffect(() => {
    regionRef.current?.scrollTo({ top: scrollTopRef.current })
  }, [scrollTopRef])

  return (
    <section
      ref={regionRef}
      aria-label="任务历史"
      className="min-h-0 flex-1 overflow-auto p-4"
      onScroll={(event) => {
        scrollTopRef.current = event.currentTarget.scrollTop
      }}
    >
      <div className="mb-4 flex items-center gap-1 overflow-x-auto rounded-md border p-0.5">
        {STATUS_FILTERS.map((filter) => (
          <Button
            key={filter.key}
            size="sm"
            variant={status === filter.key ? 'secondary' : 'ghost'}
            className="h-7 shrink-0"
            onClick={() => {
              const params = new URLSearchParams(searchParamsKey)
              if (filter.key) params.set('status', filter.key)
              else params.delete('status')
              params.set('tab', 'tasks')
              router.replace(`${pathname}?${params.toString()}`, { scroll: false })
            }}
          >
            {filter.label}
          </Button>
        ))}
      </div>

      {isLoading ? (
        <LoadingState />
      ) : isError ? (
        <ErrorState message={(error as Error)?.message} hint={BACKEND_HINT} />
      ) : tasks.length === 0 ? (
        <EmptyState title="暂无任务" description="触发采集后，任务会显示在此。" />
      ) : (
        <Card className="overflow-hidden py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>数据源</TableHead>
                <TableHead>触发方式</TableHead>
                <TableHead>优先级</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>创建时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tasks.map((task) => (
                <TableRow key={task.id} className="group">
                  <TableCell className="font-medium">
                    <Link
                      href={`/tasks/${task.id}?returnTo=${encodeURIComponent(`${pathname}?${searchParamsKey}`)}`}
                      className="flex items-center gap-2 hover:underline"
                    >
                      <span>{task.source_name ?? task.source_id}</span>
                      <ArrowUpRight className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{task.trigger_type}</TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">{task.priority}</TableCell>
                  <TableCell>
                    <StatusBadge status={task.status} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatRelative(task.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </section>
  )
}
