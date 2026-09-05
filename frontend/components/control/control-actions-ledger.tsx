'use client'

import { useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'

import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { StatusBadge } from '@/components/shell/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useControlActions } from '@/lib/api/hooks'
import { formatRelative } from '@/lib/format'

type ControlActionQuery = {
  source_id?: string
  mode?: string
  outcome?: string
  page?: number
  limit?: number
}

export function ControlActionsLedger() {
  const searchParams = useSearchParams()
  const searchParamsKey = searchParams.toString()
  const params = useMemo<ControlActionQuery>(() => {
    const query = new URLSearchParams(searchParamsKey)
    const page = Number(query.get('page'))
    const limit = Number(query.get('limit'))
    return {
      source_id: query.get('source_id') ?? undefined,
      mode: query.get('mode') ?? undefined,
      outcome: query.get('outcome') ?? undefined,
      page: Number.isInteger(page) && page > 0 ? page : undefined,
      limit: Number.isInteger(limit) && limit > 0 ? limit : undefined,
    }
  }, [searchParamsKey])
  const { data, isLoading, isError, error, refetch } = useControlActions(params)
  const actions = data?.data ?? []
  const [isRetrying, setIsRetrying] = useState(false)
  const retry = () => {
    setIsRetrying(true)
    void refetch().finally(() => setIsRetrying(false))
  }

  if (isError || isRetrying) {
    return (
      <ErrorState
        message={(error as Error)?.message}
        hint={BACKEND_HINT}
        action={
          <Button
            onClick={retry}
            disabled={isRetrying}
            aria-busy={isRetrying}
          >
            {isRetrying ? '重新加载中…' : '重新加载'}
          </Button>
        }
      />
    )
  }
  if (isLoading) return <LoadingState />
  if (actions.length === 0) {
    return <EmptyState title="暂无控制动作" description="控制器产生建议或执行动作后，记录会显示在此。" />
  }

  return (
    <Card className="overflow-hidden py-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>动作类型</TableHead>
            <TableHead>状态类别</TableHead>
            <TableHead>模式</TableHead>
            <TableHead>是否执行</TableHead>
            <TableHead>结果</TableHead>
            <TableHead>时间</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {actions.map((action) => (
            <TableRow key={action.id}>
              <TableCell className="font-mono text-xs font-medium">{action.action_type}</TableCell>
              <TableCell>
                <StatusBadge status={action.state} />
              </TableCell>
              <TableCell>
                <Badge variant={action.mode === 'automatic' ? 'default' : 'outline'}>
                  {action.mode === 'automatic' ? '自动' : '建议'}
                </Badge>
              </TableCell>
              <TableCell>
                {action.executed ? (
                  <Badge variant="secondary">已执行</Badge>
                ) : (
                  <span className="text-muted-foreground">未执行</span>
                )}
              </TableCell>
              <TableCell>
                {action.outcome ? (
                  <StatusBadge status={action.outcome} />
                ) : (
                  <span className="text-muted-foreground">待评估</span>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">{formatRelative(action.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}
