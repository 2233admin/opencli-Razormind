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
import { Switch } from '@/components/ui/switch'
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

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'good' | 'bad' | 'muted'
}) {
  const toneClass =
    tone === 'good' ? 'text-success' : tone === 'bad' ? 'text-destructive' : 'text-muted-foreground'
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`font-mono text-sm font-medium ${toneClass}`}>{value}</span>
    </div>
  )
}

function OdpSection({
  title,
  state,
  children,
}: {
  title: string
  state: { available: boolean; error?: string | null }
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">{title}</span>
        {state.available ? (
          <StatusBadge status="healthy" />
        ) : (
          <StatusBadge status="offline" />
        )}
      </div>
      {state.available ? (
        <div className="grid grid-cols-2 gap-2">{children}</div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {state.error || '当前不可用（依赖的 Redis / 数据面未部署）'}
        </p>
      )}
    </div>
  )
}

function OdpPanels({ state }: { state: OdpSystemState }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <OdpSection title="Ingest 入口" state={state.ingest}>
        <Metric
          label="健康"
          value={
            state.ingest.healthy === true
              ? '健康'
              : state.ingest.healthy === false
                ? '异常'
                : '未知'
          }
          tone={
            state.ingest.healthy === true
              ? 'good'
              : state.ingest.healthy === false
                ? 'bad'
                : 'muted'
          }
        />
      </OdpSection>
      <OdpSection title="Stream 消费组" state={state.stream}>
        <Metric label="消费组" value={state.stream.group || '—'} />
        <Metric label="Lag" value={state.stream.lag == null ? '—' : formatNum(state.stream.lag)} />
        <Metric
          label="Pending"
          value={state.stream.pending == null ? '—' : formatNum(state.stream.pending)}
        />
        <Metric
          label="最旧滞留"
          value={
            state.stream.oldest_pending_idle_ms == null
              ? '—'
              : formatMs(state.stream.oldest_pending_idle_ms)
          }
        />
      </OdpSection>
      <OdpSection title="DLQ 死信" state={state.dlq}>
        <Metric
          label="总量"
          value={state.dlq.total == null ? '—' : formatNum(state.dlq.total)}
        />
        <Metric
          label="近 24h"
          value={state.dlq.last_24h == null ? '—' : formatNum(state.dlq.last_24h)}
        />
      </OdpSection>
      <OdpSection title="Store 存储" state={state.store}>
        <Metric
          label="心跳年龄"
          value={
            state.store.heartbeat_age_seconds == null
              ? '—'
              : `${state.store.heartbeat_age_seconds}s`
          }
        />
        {state.store.note ? (
          <span className="col-span-2 text-[11px] text-muted-foreground">{state.store.note}</span>
        ) : null}
      </OdpSection>
      <OdpSection title="Outbox" state={state.outbox}>
        <Metric
          label="未发布"
          value={state.outbox.unpublished == null ? '—' : formatNum(state.outbox.unpublished)}
        />
        {state.outbox.note ? (
          <span className="col-span-2 text-[11px] text-muted-foreground">{state.outbox.note}</span>
        ) : null}
      </OdpSection>
    </div>
  )
}

function AdvisoryTotalsRow({ report }: { report: AdvisoryReport }) {
  const t = report.totals
  return (
    <div className="flex flex-wrap gap-6">
      <Metric label="总数" value={formatNum(t.total)} />
      <Metric label="待评估" value={formatNum(t.pending)} />
      <Metric label="已评估" value={formatNum(t.evaluated)} />
      <Metric label="已恢复" value={formatNum(t.recovered)} tone="good" />
      <Metric label="已固化" value={formatNum(t.persisted)} />
      <Metric
        label="恢复率"
        value={t.recovery_rate == null ? '—' : `${(t.recovery_rate * 100).toFixed(1)}%`}
        tone={t.recovery_rate != null && t.recovery_rate >= 0.8 ? 'bad' : 'muted'}
      />
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
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>审计台账（控制动作）</CardTitle>
            <CardDescription>
              控制器的完整证据账本——每一次建议与执行，按状态类别 / 模式 / 结果过滤。
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? '刷新中…' : '刷新'}
          </Button>
        </div>
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
                  共 {meta.total} 条 · 第 {meta.page}/{meta.pages} 页
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

  return (
    <PageContainer
      title="控制中心"
      eyebrow="CONTROL PLANE"
      description="执行熔断、自动模式门禁、共享数据面与审计台账的系统级控制与观测。"
    >
      {/* ── Panel 1: Kill switch ─────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <div>
              <CardTitle>执行熔断开关（Kill Switch）</CardTitle>
              <CardDescription>
                engaged 时无条件短路 Control Cycle 在 automatic 模式下的全部执行，下一次 tick 立即生效。
              </CardDescription>
            </div>
            <Switch
              checked={Boolean(kill.data?.engaged)}
              onCheckedChange={handleKillToggle}
              disabled={setKill.isPending}
              aria-label="执行熔断开关"
            />
          </div>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-6">
          {kill.isLoading ? (
            <LoadingState rows={1} />
          ) : kill.isError ? (
            <ErrorState message={(kill.error as Error)?.message} hint={BACKEND_HINT} />
          ) : kill.data ? (
            <>
              <div className="flex items-center gap-2">
                {kill.data.engaged ? (
                  <Badge variant="destructive">已熔断</Badge>
                ) : (
                  <Badge variant="secondary">未熔断</Badge>
                )}
              </div>
              <Metric
                label="生效来源"
                value={
                  kill.data.runtime_override != null
                    ? '运行期覆盖'
                    : kill.data.config_default
                      ? '配置默认（启用）'
                      : '配置默认（停用）'
                }
              />
              <Metric
                label="运行期覆盖"
                value={
                  kill.data.runtime_override == null
                    ? '未设置'
                    : kill.data.runtime_override
                      ? '启用'
                      : '停用'
                }
              />
              <Metric
                label="配置默认"
                value={kill.data.config_default ? '启用' : '停用'}
                tone={kill.data.config_default ? 'bad' : 'muted'}
              />
              {kill.data.engaged ? (
                <Badge variant="destructive">所有自动执行将在下一次 tick 被短路</Badge>
              ) : null}
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* ── Panel 2: Advisory report ─────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>咨询报告（Advisory Report）</CardTitle>
          <CardDescription>
            control_actions 证据台账的收敛/恢复统计 —— 某个 (state, action_type) 组合的建议大多「已恢复」说明该处过度建议，
            不应自动化；大多「已固化」才具备翻转 automatic 模式的门禁资格。读取时自动完成一次懒评估。
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
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Card>
              )}
              <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                <span>模式分布：</span>
                {Object.entries(advisory.data.mode_breakdown).map(([mode, count]) => (
                  <Badge key={mode} variant={mode === 'automatic' ? 'default' : 'outline'}>
                    {mode === 'automatic' ? '自动' : '建议'} × {formatNum(count)}
                  </Badge>
                ))}
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* ── Panel 3: ODP data-plane state ────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <div>
              <CardTitle>ODP 数据面状态</CardTitle>
              <CardDescription>
                共享数据平面（Redis 消费组 / 死信队列 / 存储心跳）的系统级健康，与单数据源无关。
                任一环节不可用只降级自身区块，不影响其他区块。
              </CardDescription>
            </div>
            {odp.data ? (
              <span className="font-mono text-xs text-muted-foreground">
                {formatRelative(odp.data.collected_at)}
              </span>
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          {odp.isLoading ? (
            <LoadingState rows={3} />
          ) : odp.isError ? (
            <ErrorState message={(odp.error as Error)?.message} hint={BACKEND_HINT} />
          ) : odp.data ? (
            <OdpPanels state={odp.data} />
          ) : null}
        </CardContent>
      </Card>

      {/* ── Panel 4: Audit ledger ────────────────────────────────────────── */}
      <AuditLedger />

      {/* ── Kill-switch engage confirmation ──────────────────────────────── */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认熔断全部自动执行？</DialogTitle>
            <DialogDescription>
              <span className="text-destructive font-medium">此操作会无条件短路 Control Cycle 在 automatic 模式下的全部执行</span>
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
