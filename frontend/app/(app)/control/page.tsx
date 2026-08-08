'use client'

import { useState } from 'react'

import {
  useAdvisoryReport,
  useControlActions,
  useKillSwitch,
  useOdpState,
  useSetKillSwitch,
} from '@/lib/api/hooks'
import { BACKEND_HINT, EmptyState, ErrorState, LoadingState } from '@/components/shell/data-states'
import { PageContainer } from '@/components/shell/page-container'
import { StatusBadge } from '@/components/shell/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatRelative } from '@/lib/format'
import type { AdvisoryReport, OdpSystemState } from '@/lib/api/types'

/** 毫秒 → 可读时长（<1s 显示 ms，否则 s/min）。 */
function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms / 60_000)}min`
}

/** 大数千分位。 */
function formatNum(n: number): string {
  return n.toLocaleString('en-US')
}

/* ────────────────────────────────────────────────────────────────
 * Status strip — the glanceable Monitor layer.
 * Four compact cells: kill state, automation gate, ODP availability,
 * ledger volume. No card chrome, just label + value + dot.
 * ──────────────────────────────────────────────────────────────── */

function StripCell({
  label,
  value,
  dot,
  dotTone,
}: {
  label: string
  value: string
  dot?: boolean
  dotTone?: 'good' | 'warn' | 'bad' | 'muted'
}) {
  const dotClass =
    dotTone === 'good'
      ? 'bg-success'
      : dotTone === 'bad'
        ? 'bg-destructive'
        : dotTone === 'warn'
          ? 'bg-warning'
          : 'bg-muted-foreground/50'
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3">
      {dot ? <span className={`size-2 shrink-0 rounded-full ${dotClass}`} /> : null}
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-3xs uppercase tracking-wide text-muted-foreground">{label}</span>
        <span className="truncate font-mono text-sm font-medium">{value}</span>
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────
 * Kill-switch — the Operate layer's primary action. Rendered as a
 * raised ops-panel cockpit (dark surface, distinct from cards) with
 * a large action button, not a buried mini-switch.
 * ──────────────────────────────────────────────────────────────── */

function KillCockpit({
  engaged,
  runtimeOverride,
  configDefault,
  isPending,
  onToggle,
}: {
  engaged: boolean
  runtimeOverride: boolean | null
  configDefault: boolean
  isPending: boolean
  onToggle: (engaged: boolean) => void
}) {
  const source = runtimeOverride != null ? '运行期覆盖' : configDefault ? '配置默认 · 启用' : '配置默认 · 停用'
  return (
    <div className="rounded-xl border border-ops-line bg-ops-panel p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          {engaged ? (
            <Badge variant="destructive" className="h-7 px-3 text-sm">
              ● 已熔断
            </Badge>
          ) : (
            <Badge variant="secondary" className="h-7 px-3 text-sm">
              ● 未熔断
            </Badge>
          )}
          <div className="flex flex-col gap-0.5">
            <span className="text-3xs uppercase tracking-wide text-muted-foreground">生效来源</span>
            <span className="font-mono text-sm">{source}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {engaged ? (
            <Button variant="secondary" onClick={() => onToggle(false)} disabled={isPending}>
              {isPending ? '执行中…' : '解除熔断'}
            </Button>
          ) : (
            <Button variant="destructive" onClick={() => onToggle(true)} disabled={isPending}>
              {isPending ? '执行中…' : '熔断全部自动执行'}
            </Button>
          )}
        </div>
      </div>
      {engaged ? (
        <p className="mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          所有 automatic 模式的 Control Cycle 执行将在下一次 tick 被无条件短路。
          运行期覆盖在进程重启后被清除，恢复为配置默认值。
        </p>
      ) : (
        <p className="mt-4 text-xs text-muted-foreground">
          熔断关闭不代表自动模式已开启——仍需 <code className="font-mono">CONTROL_MODE=automatic</code> 及全部门禁通过才会执行。
        </p>
      )}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────
 * ODP data plane — Monitor layer, compact. Five small cells, each
 * degrades independently with the backend's reason surfaced.
 * ──────────────────────────────────────────────────────────────── */

function OdpCell({
  title,
  state,
  children,
}: {
  title: string
  state: { available: boolean; error?: string | null }
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-3xs uppercase tracking-wide text-muted-foreground">{title}</span>
        {state.available ? (
          <span className="size-1.5 rounded-full bg-success" />
        ) : (
          <span className="size-1.5 rounded-full bg-muted-foreground/50" />
        )}
      </div>
      {state.available ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">{children}</div>
      ) : (
        <p className="truncate text-3xs text-muted-foreground" title={state.error ?? undefined}>
          {state.error || '不可用'}
        </p>
      )}
    </div>
  )
}

function OdpGrid({ state }: { state: OdpSystemState }) {
  const availableCount = [
    state.ingest.available,
    state.stream.available,
    state.dlq.available,
    state.store.available,
    state.outbox.available,
  ].filter(Boolean).length
  return (
    <div className="flex flex-col gap-2">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <OdpCell title="Ingest" state={state.ingest}>
          <span className="text-xs">
            {state.ingest.healthy === true ? '健康' : state.ingest.healthy === false ? '异常' : '未知'}
          </span>
        </OdpCell>
        <OdpCell title="Stream 消费组" state={state.stream}>
          <span className="font-mono text-xs">{state.stream.group || '—'}</span>
          <span className="font-mono text-xs text-muted-foreground">
            lag {state.stream.lag == null ? '—' : formatNum(state.stream.lag)}
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            pend {state.stream.pending == null ? '—' : formatNum(state.stream.pending)}
          </span>
          <span className="font-mono text-xs text-muted-foreground">
            idle{' '}
            {state.stream.oldest_pending_idle_ms == null
              ? '—'
              : formatMs(state.stream.oldest_pending_idle_ms)}
          </span>
        </OdpCell>
        <OdpCell title="DLQ 死信" state={state.dlq}>
          <span className="font-mono text-xs">{state.dlq.total == null ? '—' : formatNum(state.dlq.total)}</span>
          <span className="font-mono text-xs text-muted-foreground">
            24h {state.dlq.last_24h == null ? '—' : formatNum(state.dlq.last_24h)}
          </span>
        </OdpCell>
        <OdpCell title="Store 存储" state={state.store}>
          <span className="font-mono text-xs">
            {state.store.heartbeat_age_seconds == null
              ? '—'
              : `${state.store.heartbeat_age_seconds}s`}
          </span>
          {state.store.note ? (
            <span className="truncate text-3xs text-muted-foreground" title={state.store.note}>
              {state.store.note}
            </span>
          ) : null}
        </OdpCell>
        <OdpCell title="Outbox" state={state.outbox}>
          <span className="font-mono text-xs">
            {state.outbox.unpublished == null ? '—' : formatNum(state.outbox.unpublished)}
          </span>
          {state.outbox.note ? (
            <span className="truncate text-3xs text-muted-foreground" title={state.outbox.note}>
              {state.outbox.note}
            </span>
          ) : null}
        </OdpCell>
      </div>
      <span className="text-3xs text-muted-foreground">
        可用区块 {availableCount}/5 · 采集于 {formatRelative(state.collected_at)}
      </span>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────
 * Advisory report — the automation-gate data. Buckets carry a gate
 * badge: mostly-recovered buckets must NOT be automated, mostly-
 * persisted ones qualify.
 * ──────────────────────────────────────────────────────────────── */

function gateEligible(bucket: AdvisoryReport['buckets'][number]): boolean {
  if (bucket.recovery_rate == null) return false
  return bucket.recovery_rate < 0.8 && bucket.persisted > 0
}

function AdvisoryTotalsRow({ report }: { report: AdvisoryReport }) {
  const t = report.totals
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">总数 </span>
        {formatNum(t.total)}
      </span>
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">待评估 </span>
        {formatNum(t.pending)}
      </span>
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">已恢复 </span>
        <span className="text-success">{formatNum(t.recovered)}</span>
      </span>
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">已固化 </span>
        {formatNum(t.persisted)}
      </span>
      <span className="font-mono text-sm">
        <span className="text-muted-foreground">恢复率 </span>
        {t.recovery_rate == null ? '—' : `${(t.recovery_rate * 100).toFixed(1)}%`}
      </span>
      {Object.entries(report.mode_breakdown).map(([mode, count]) => (
        <Badge key={mode} variant={mode === 'automatic' ? 'default' : 'outline'}>
          {mode === 'automatic' ? '自动' : '建议'} × {formatNum(count)}
        </Badge>
      ))}
    </div>
  )
}

const PAGE_SIZE = 10

function AuditLedger() {
  const [page, setPage] = useState(1)
  const { data, isLoading, isError, error, refetch, isFetching } = useControlActions({
    page,
    limit: PAGE_SIZE,
  })
  const actions = data?.data ?? []
  const meta = data?.meta

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-4">
        <div>
          <CardTitle className="text-base">审计台账（控制动作）</CardTitle>
          <CardDescription>每一次建议与执行的证据账本。</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? '刷新中…' : '刷新'}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {isLoading ? (
          <LoadingState rows={4} />
        ) : isError ? (
          <ErrorState message={(error as Error)?.message} hint={BACKEND_HINT} />
        ) : actions.length === 0 ? (
          <EmptyState
            title="暂无控制动作"
            description="控制器产生建议或执行动作后，记录会显示在此。"
          />
        ) : (
          <>
            <Card className="overflow-hidden py-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>动作类型</TableHead>
                    <TableHead>状态类别</TableHead>
                    <TableHead>模式</TableHead>
                    <TableHead>执行</TableHead>
                    <TableHead>结果</TableHead>
                    <TableHead>原因</TableHead>
                    <TableHead>时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {actions.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="font-mono text-xs font-medium">{a.action_type}</TableCell>
                      <TableCell>
                        <StatusBadge status={a.state} />
                      </TableCell>
                      <TableCell>
                        <Badge variant={a.mode === 'automatic' ? 'default' : 'outline'}>
                          {a.mode === 'automatic' ? '自动' : '建议'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {a.executed ? (
                          <Badge variant="secondary">已执行</Badge>
                        ) : (
                          <span className="text-muted-foreground">未执行</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {a.outcome ? (
                          <StatusBadge status={a.outcome} />
                        ) : (
                          <span className="text-muted-foreground">待评估</span>
                        )}
                      </TableCell>
                      <TableCell className="max-w-48 truncate text-xs text-muted-foreground">
                        {a.reason || '—'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatRelative(a.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
            {meta && meta.pages > 1 ? (
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  共 {formatNum(meta.total)} 条 · 第 {meta.page}/{meta.pages} 页
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1 || isFetching}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!meta || page >= meta.pages || isFetching}
                    onClick={() => setPage((p) => Math.min(meta?.pages ?? p, p + 1))}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function ControlCenterPage() {
  const kill = useKillSwitch({ refetchInterval: 30_000 })
  const setKill = useSetKillSwitch()
  const advisory = useAdvisoryReport({ refetchInterval: 60_000 })
  const odp = useOdpState({ refetchInterval: 15_000 })
  const ledger = useControlActions({ page: 1, limit: PAGE_SIZE })
  const [confirmOpen, setConfirmOpen] = useState(false)

  // 打开熔断是危险动作：弹确认；关闭熔断是恢复安全态：直接执行。
  const handleKillToggle = (engaged: boolean) => {
    if (engaged) {
      setConfirmOpen(true)
    } else {
      setKill.mutate(false)
    }
  }

  const confirmEngage = () => {
    setKill.mutate(true)
    setConfirmOpen(false)
  }

  const odpAvailable = odp.data
    ? [odp.data.ingest.available, odp.data.stream.available, odp.data.dlq.available, odp.data.store.available, odp.data.outbox.available].filter(Boolean).length
    : null
  const qualifiedBuckets = advisory.data?.buckets.filter(gateEligible).length ?? null
  const ledgerTotal = ledger.data?.meta?.total ?? null

  return (
    <PageContainer
      title="控制中心"
      eyebrow="CONTROL PLANE"
      description="执行熔断、自动模式门禁、共享数据面与审计台账的系统级控制与观测。"
    >
      {/* ── Status strip (Monitor) ──────────────────────────────────────── */}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <StripCell
          label="执行熔断开关"
          value={kill.data?.engaged ? '已熔断' : '未熔断'}
          dot
          dotTone={kill.data?.engaged ? 'bad' : 'good'}
        />
        <StripCell
          label="自动化门禁"
          value={qualifiedBuckets == null ? '—' : `${qualifiedBuckets} 类可自动化`}
          dotTone={qualifiedBuckets && qualifiedBuckets > 0 ? 'warn' : 'muted'}
        />
        <StripCell
          label="ODP 数据面"
          value={odpAvailable == null ? '—' : `${odpAvailable}/5 区块可用`}
          dot
          dotTone={odpAvailable == null ? 'muted' : odpAvailable === 5 ? 'good' : odpAvailable > 0 ? 'warn' : 'bad'}
        />
        <StripCell
          label="审计台账"
          value={ledgerTotal == null ? '—' : `${formatNum(ledgerTotal)} 条`}
        />
      </div>

      {/* ── Kill switch cockpit (Operate) ───────────────────────────────── */}
      {kill.isError ? (
        <ErrorState message={(kill.error as Error)?.message} hint={BACKEND_HINT} />
      ) : kill.isLoading ? (
        <LoadingState rows={1} />
      ) : kill.data ? (
        <KillCockpit
          engaged={kill.data.engaged}
          runtimeOverride={kill.data.runtime_override}
          configDefault={kill.data.config_default}
          isPending={setKill.isPending}
          onToggle={handleKillToggle}
        />
      ) : null}

      {/* ── ODP data plane (Monitor) ───────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">ODP 数据面状态</CardTitle>
          <CardDescription>
            共享数据平面（Redis 消费组 / 死信队列 / 存储心跳）的系统级健康，与单数据源无关。任一环节不可用只降级自身区块。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {odp.isLoading ? (
            <LoadingState rows={3} />
          ) : odp.isError ? (
            <ErrorState message={(odp.error as Error)?.message} hint={BACKEND_HINT} />
          ) : odp.data ? (
            <OdpGrid state={odp.data} />
          ) : null}
        </CardContent>
      </Card>

      {/* ── Advisory report (gate data) ─────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">咨询报告（Advisory Report）</CardTitle>
          <CardDescription>
            control_actions 证据台账的收敛/恢复统计。某 (state, action_type) 组合的建议大多「已恢复」说明过度建议，不应自动化；大多「已固化」才具备翻转 automatic 的门禁资格。读取时自动完成一次懒评估。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {advisory.isLoading ? (
            <LoadingState rows={3} />
          ) : advisory.isError ? (
            <ErrorState message={(advisory.error as Error)?.message} hint={BACKEND_HINT} />
          ) : advisory.data ? (
            <>
              <AdvisoryTotalsRow report={advisory.data} />
              {advisory.data.buckets.length === 0 ? (
                <EmptyState
                  title="暂无台账数据"
                  description="控制器产生建议或执行动作后，分桶统计会显示在此。"
                />
              ) : (
                <Card className="overflow-hidden py-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>状态类别</TableHead>
                        <TableHead>动作类型</TableHead>
                        <TableHead className="text-right">总数</TableHead>
                        <TableHead className="text-right">待评估</TableHead>
                        <TableHead className="text-right">已恢复</TableHead>
                        <TableHead className="text-right">已固化</TableHead>
                        <TableHead className="text-right">恢复率</TableHead>
                        <TableHead>门禁</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {advisory.data.buckets.map((b) => (
                        <TableRow key={`${b.state}/${b.action_type}`}>
                          <TableCell>
                            <StatusBadge status={b.state} />
                          </TableCell>
                          <TableCell className="font-mono text-xs font-medium">{b.action_type}</TableCell>
                          <TableCell className="text-right font-mono text-xs">{formatNum(b.total)}</TableCell>
                          <TableCell className="text-right font-mono text-xs text-muted-foreground">
                            {formatNum(b.pending)}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs text-success">
                            {formatNum(b.recovered)}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs">{formatNum(b.persisted)}</TableCell>
                          <TableCell className="text-right font-mono text-xs">
                            {b.recovery_rate == null ? '—' : `${(b.recovery_rate * 100).toFixed(1)}%`}
                          </TableCell>
                          <TableCell>
                            {gateEligible(b) ? (
                              <Badge variant="outline" className="text-success">
                                可自动化
                              </Badge>
                            ) : (
                              <span className="text-3xs text-muted-foreground">不宜自动化</span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Card>
              )}
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* ── Audit ledger (Command/Inspect) ──────────────────────────────── */}
      <AuditLedger />

      {/* ── Kill-switch engage confirmation ─────────────────────────────── */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认熔断全部自动执行？</DialogTitle>
            <DialogDescription>
              <span className="text-destructive font-medium">
                此操作会无条件短路 Control Cycle 在 automatic 模式下的全部执行
              </span>
              ，下一次 tick 立即生效。运行期覆盖在进程重启后会被清除，恢复为配置默认值。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmEngage} disabled={setKill.isPending}>
              {setKill.isPending ? '执行中…' : '确认熔断'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageContainer>
  )
}
