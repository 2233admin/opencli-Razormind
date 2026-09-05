'use client'

import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useLayoutEffect, useRef, useState, type MutableRefObject } from 'react'
import { toast } from 'sonner'

import { NotificationRuleFormDialog } from '@/components/notifications/notification-rule-form-dialog'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { StatusBadge } from '@/components/shell/status-badge'
import { Badge } from '@/components/ui/badge'
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
import { useDeleteNotificationRule, useNotificationRules } from '@/lib/api/hooks'
import type { NotificationRule } from '@/lib/api/types'
import { notificationChannelLabel } from '@/lib/notification-channels'

export function NotificationsPane({ scrollTopRef }: { scrollTopRef: MutableRefObject<number> }) {
  const { data, isLoading, isError, error } = useNotificationRules()
  const rules = data?.data ?? []
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const deleteMutation = useDeleteNotificationRule()
  const regionRef = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    regionRef.current?.scrollTo({ top: scrollTopRef.current })
  }, [scrollTopRef])

  const handleDelete = (rule: NotificationRule) => {
    if (confirmDeleteId !== rule.id) {
      setConfirmDeleteId(rule.id)
      return
    }
    deleteMutation.mutate(rule.id, {
      onSuccess: () => {
        toast.success('已删除通知规则')
        setConfirmDeleteId(null)
      },
      onError: (deleteError: Error) => toast.error(deleteError.message),
    })
  }

  return (
    <section
      ref={regionRef}
      aria-label="通知规则"
      className="min-h-0 flex-1 overflow-auto p-4"
      onScroll={(event) => {
        scrollTopRef.current = event.currentTarget.scrollTop
      }}
    >
      <div className="mb-4 flex justify-end">
        <NotificationRuleFormDialog
          mode="create"
          triggerLabel="创建规则"
          triggerIcon={<Plus className="size-4" />}
        />
      </div>

      {isLoading ? (
        <LoadingState />
      ) : isError ? (
        <ErrorState message={(error as Error)?.message} hint={BACKEND_HINT} />
      ) : rules.length === 0 ? (
        <EmptyState title="暂无通知规则" description="创建规则以在采集事件发生时收到通知。" />
      ) : (
        <Card className="overflow-hidden py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>触发事件</TableHead>
                <TableHead>通知方式</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell className="font-medium">{rule.name}</TableCell>
                  <TableCell>
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                      {rule.trigger_event}
                    </code>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{notificationChannelLabel(rule.notifier_type)}</Badge>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={rule.enabled ? 'enabled' : 'disabled'} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <NotificationRuleFormDialog
                        mode="edit"
                        rule={rule}
                        triggerLabel=""
                        triggerIcon={<Pencil className="size-3.5" />}
                        triggerVariant="ghost"
                        triggerSize="icon-sm"
                        triggerAriaLabel="编辑规则"
                      />
                      <Button
                        variant={confirmDeleteId === rule.id ? 'destructive' : 'ghost'}
                        size="icon-sm"
                        aria-label={confirmDeleteId === rule.id ? `确认删除 ${rule.name}` : '删除规则'}
                        disabled={deleteMutation.isPending}
                        onClick={() => handleDelete(rule)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </section>
  )
}
