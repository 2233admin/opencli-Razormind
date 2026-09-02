'use client'

import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  Database,
  Gauge,
  Layers3,
  LoaderCircle,
  ShieldCheck,
} from 'lucide-react'
import { useId, useState } from 'react'

import { LoadingState } from '@/components/shell/data-states'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  useCreateRunAnalysisSnapshot,
  usePreviewRunAnalysisSnapshot,
  useRunAnalysisSnapshotCapability,
  useRunAnalysisSnapshotReceipt,
  useRunAnalysisSnapshotReceipts,
  useRunAnalysisSnapshotSummary,
} from '@/lib/api/hooks'
import type {
  RunAnalysisSnapshotCapabilityState,
  RunAnalysisSnapshotReceipt,
  RunAnalysisSnapshotRowCounts,
  RunAnalysisSnapshotSourceRange,
  RunAnalysisSnapshotStatus,
  RunAnalysisSnapshotSummary,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'

type RunAnalysisSnapshotPanelProps = {
  workspaceId: string | null
  projectId: string
  workflowId: string
  runId: string
  runStatus: string
  runStartAt: string
  runEndAt: string
}

const capabilityCopy: Record<RunAnalysisSnapshotCapabilityState, { label: string; description: string; tone: string }> = {
  disabled: {
    label: '未启用',
    description: '此工作区尚未启用按需分析快照。现有运行数据不会被复制。',
    tone: 'border-zinc-500/30 bg-zinc-500/10 text-zinc-300',
  },
  unavailable: {
    label: '不可用',
    description: '分析运行时当前不可达。运行和既有回执仍保留在 OpenCLI。',
    tone: 'border-signal-warning/30 bg-signal-warning/10 text-signal-warning',
  },
  unhealthy: {
    label: '运行异常',
    description: '分析运行时未通过健康检查，暂不允许创建新快照。',
    tone: 'border-signal-danger/30 bg-signal-danger/10 text-signal-danger',
  },
  ready: {
    label: '可创建',
    description: '只会导出所选 UTC 范围内的脱敏事件与采集执行指标。',
    tone: 'border-signal-success/30 bg-signal-success/10 text-signal-success',
  },
}

const receiptCopy: Record<RunAnalysisSnapshotStatus, { label: string; tone: string }> = {
  exporting: {
    label: '快照正在导出',
    tone: 'border-primary/30 bg-primary/10 text-primary',
  },
  completed: {
    label: '快照已完成',
    tone: 'border-signal-success/30 bg-signal-success/10 text-signal-success',
  },
  failed: {
    label: '快照创建失败',
    tone: 'border-signal-danger/30 bg-signal-danger/10 text-signal-danger',
  },
  expired: {
    label: '快照已过期',
    tone: 'border-signal-warning/30 bg-signal-warning/10 text-signal-warning',
  },
}

export function RunAnalysisSnapshotPanel({
  workspaceId,
  projectId,
  workflowId,
  runId,
  runStatus,
  runStartAt,
  runEndAt,
}: RunAnalysisSnapshotPanelProps) {
  const eligibleRun = runStatus === 'completed'
  const queriesEnabled = eligibleRun && Boolean(workspaceId)
  const rangeId = useId()
  const rangeDescriptionId = `${rangeId}-description`
  const rangeErrorId = `${rangeId}-error`
  const startInputId = `${rangeId}-start`
  const endInputId = `${rangeId}-end`
  const runStartInput = toUtcInputBoundary(runStartAt, 'ceil')
  const runEndInput = toUtcInputBoundary(runEndAt, 'floor')
  const [startAt, setStartAt] = useState(() => runStartInput)
  const [endAt, setEndAt] = useState(() => runEndInput)
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null)
  const capabilityQuery = useRunAnalysisSnapshotCapability(
    workspaceId,
    projectId,
    workflowId,
    runId,
    queriesEnabled,
  )
  const receiptsQuery = useRunAnalysisSnapshotReceipts(
    workspaceId,
    projectId,
    workflowId,
    runId,
    queriesEnabled,
  )
  const previewMutation = usePreviewRunAnalysisSnapshot()
  const createMutation = useCreateRunAnalysisSnapshot()
  const receipts = receiptsQuery.data ?? []
  const preferredSnapshotId = selectedSnapshotId ?? createMutation.data?.snapshotId ?? receipts[0]?.snapshotId ?? null
  const receiptQuery = useRunAnalysisSnapshotReceipt(
    workspaceId,
    projectId,
    workflowId,
    runId,
    preferredSnapshotId,
  )
  const activeReceipt = receiptQuery.data
    ?? (createMutation.data?.snapshotId === preferredSnapshotId ? createMutation.data : undefined)
    ?? receipts.find((candidate) => candidate.snapshotId === preferredSnapshotId)
  const visibleReceipts = activeReceipt
    ? receipts.some((candidate) => candidate.snapshotId === activeReceipt.snapshotId)
      ? receipts.map((candidate) => candidate.snapshotId === activeReceipt.snapshotId ? activeReceipt : candidate)
      : [activeReceipt, ...receipts]
    : receipts
  const summaryQuery = useRunAnalysisSnapshotSummary(
    workspaceId,
    projectId,
    workflowId,
    runId,
    activeReceipt?.snapshotId ?? null,
    activeReceipt?.status === 'completed',
  )
  const range = buildRange(startAt, endAt)
  const rangeError = validateRange(range, runStartAt, runEndAt)
  const capability = capabilityQuery.data
  const canPreview = capability?.state === 'ready' && !rangeError && !previewMutation.isPending && !createMutation.isPending
  const preview = previewMutation.data
  const previewMatchesRange = Boolean(
    preview
    && range
    && preview.runId === runId
    && rangesMatch(preview.sourceRange, range),
  )
  const canExport = Boolean(
    capability?.state === 'ready'
    && !rangeError
    && previewMatchesRange
    && preview?.eligible
    && preview.rowCounts.total > 0
    && !createMutation.isPending,
  )

  const updateRange = (field: 'start' | 'end', value: string) => {
    previewMutation.reset()
    if (createMutation.isError) createMutation.reset()
    if (field === 'start') setStartAt(value)
    else setEndAt(value)
  }

  const previewRange = () => {
    if (!workspaceId || !range || rangeError) return
    previewMutation.mutate({ workspaceId, projectId, workflowId, runId, data: range })
  }

  const createSnapshot = () => {
    if (
      !workspaceId
      || !range
      || !preview
      || !canExport
      || preview.runId !== runId
      || !rangesMatch(preview.sourceRange, range)
    ) return
    createMutation.mutate(
      { workspaceId, projectId, workflowId, runId, data: preview.sourceRange },
      { onSuccess: (nextReceipt) => setSelectedSnapshotId(nextReceipt.snapshotId) },
    )
  }

  return (
    <section
      aria-labelledby="run-analysis-snapshot-title"
      className="overflow-hidden rounded-lg border border-ops-line bg-ops-panel shadow-panel"
    >
      <header className="flex flex-col gap-3 border-b border-ops-line px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md border border-primary/20 bg-primary/10 text-primary">
            <Database className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 id="run-analysis-snapshot-title" className="type-title-small text-zinc-100">分析快照</h3>
            <p className="mt-1 text-xs leading-5 text-zinc-400">
              为定向分析创建一次性、限时保留的 QuestDB 脱敏投影。
            </p>
          </div>
        </div>
        <Badge variant="outline" className="w-fit border-primary/20 bg-primary/5 font-mono text-2xs text-primary">
          OODA · OBSERVE / ORIENT
        </Badge>
      </header>

      <div className="space-y-4 p-4">
        {runStatus !== 'completed' ? (
          <InlineNotice
            icon={ShieldCheck}
            title="仅已完成运行"
            description="Analysis Snapshot 只接受状态精确为 completed 的 Run；当前运行不会发起能力检查或导出。"
          />
        ) : !workspaceId ? (
          <InlineNotice
            icon={AlertTriangle}
            title="工作区上下文不可用"
            description="返回 Studio 并从工作区项目入口重新打开此运行。"
            tone="danger"
          />
        ) : capabilityQuery.isLoading ? (
          <LoadingState rows={2} />
        ) : capabilityQuery.isError || !capability ? (
          <InlineNotice
            icon={AlertTriangle}
            title="能力状态读取失败"
            description="当前无法确认分析运行时状态。为保护运行数据，导出操作保持关闭。"
            tone="danger"
          />
        ) : (
          <CapabilityState state={capability.state} reasonCode={capability.reasonCode} />
        )}

        {capability?.state === 'ready' ? (
          <div className="space-y-3 rounded-md border border-ops-line bg-ops-raised/50 p-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h4 className="text-sm font-medium text-zinc-100">选择来源时间范围</h4>
                <p id={rangeDescriptionId} className="mt-1 text-xs leading-5 text-zinc-400">
                  输入值按 UTC 解释，精确到毫秒，并再次由服务端限制在此 Run 的权威时间边界内。
                </p>
              </div>
              <span className="font-mono text-2xs text-zinc-500">UTC · startAt &lt; endAt</span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1.5 text-xs text-zinc-300" htmlFor={startInputId}>
                <span>开始时间（UTC）</span>
                <Input
                  id={startInputId}
                  type="datetime-local"
                  step="0.001"
                  min={runStartInput}
                  max={runEndInput}
                  value={startAt}
                  onChange={(event) => updateRange('start', event.target.value)}
                  disabled={previewMutation.isPending || createMutation.isPending}
                  aria-invalid={Boolean(rangeError)}
                  aria-describedby={`${rangeDescriptionId}${rangeError ? ` ${rangeErrorId}` : ''}`}
                  className="min-h-11 font-mono"
                />
              </label>
              <label className="space-y-1.5 text-xs text-zinc-300" htmlFor={endInputId}>
                <span>结束时间（UTC）</span>
                <Input
                  id={endInputId}
                  type="datetime-local"
                  step="0.001"
                  min={runStartInput}
                  max={runEndInput}
                  value={endAt}
                  onChange={(event) => updateRange('end', event.target.value)}
                  disabled={previewMutation.isPending || createMutation.isPending}
                  aria-invalid={Boolean(rangeError)}
                  aria-describedby={`${rangeDescriptionId}${rangeError ? ` ${rangeErrorId}` : ''}`}
                  className="min-h-11 font-mono"
                />
              </label>
            </div>

            {rangeError ? <p id={rangeErrorId} className="text-xs text-signal-danger" role="alert">{rangeError}</p> : null}

            <div className="flex flex-col gap-2 sm:flex-row">
              <Button type="button" variant="outline" className="min-h-11 sm:min-w-32" disabled={!canPreview} onClick={previewRange}>
                {previewMutation.isPending ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Activity className="size-4" aria-hidden="true" />}
                预览范围
              </Button>
              <Button type="button" className="min-h-11 sm:min-w-40" disabled={!canExport} onClick={createSnapshot}>
                {createMutation.isPending ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="size-4" aria-hidden="true" />}
                创建分析快照
              </Button>
            </div>

            {previewMutation.isError ? (
              <InlineNotice
                icon={AlertTriangle}
                title="范围预览失败"
                description="没有创建回执或写入分析运行时。请检查范围后重试。"
                tone="danger"
              />
            ) : preview && !previewMatchesRange ? (
              <InlineNotice
                icon={Clock3}
                title="范围已更改"
                description="当前预览不再匹配输入范围，请重新预览后再创建快照。"
              />
            ) : preview ? (
              preview.eligible && preview.rowCounts.total > 0 ? (
                <PreviewResult rowCounts={preview.rowCounts} sourceRange={preview.sourceRange} />
              ) : (
                <InlineNotice
                  icon={Layers3}
                  title="所选范围内没有可导出的事件"
                  description="空范围可以预览，但不能创建 Analysis Snapshot。"
                />
              )
            ) : null}
          </div>
        ) : null}

        {createMutation.isPending ? (
          <div className="flex items-center gap-3 rounded-md border border-primary/25 bg-primary/10 p-3 text-sm text-primary" role="status" aria-live="polite">
            <LoaderCircle className="size-4 shrink-0 animate-spin" aria-hidden="true" />
            <div>
              <div className="font-medium">正在导出已脱敏快照</div>
              <div className="mt-1 text-xs text-zinc-400">请求保持在当前页面；完成后会显示权威回执。</div>
            </div>
          </div>
        ) : createMutation.isError ? (
          <InlineNotice
            icon={AlertTriangle}
            title="快照请求失败"
            description="服务端未返回可展示的回执。最近回执已重新读取，原始 Run 不受影响。"
            tone="danger"
          />
        ) : null}

        {queriesEnabled ? (
          <ReceiptHistory
            receipts={visibleReceipts}
            selectedSnapshotId={preferredSnapshotId}
            isLoading={receiptsQuery.isLoading}
            isError={receiptsQuery.isError}
            onSelect={setSelectedSnapshotId}
          />
        ) : null}

        {activeReceipt ? (
          <ReceiptDetail
            receipt={activeReceipt}
            isRefreshing={receiptQuery.isFetching}
            summary={summaryQuery.data}
            summaryLoading={summaryQuery.isLoading}
            summaryError={summaryQuery.isError}
          />
        ) : null}
      </div>
    </section>
  )
}

function CapabilityState({ state, reasonCode }: { state: RunAnalysisSnapshotCapabilityState; reasonCode: string | null }) {
  const copy = capabilityCopy[state]
  return (
    <div className="flex flex-col gap-3 rounded-md border border-ops-line bg-ops-raised/40 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-zinc-100">QuestDB 分析能力</span>
          <Badge variant="outline" className={cn('font-mono text-2xs', copy.tone)}>{copy.label}</Badge>
        </div>
        <p className="mt-1 text-xs leading-5 text-zinc-400">{copy.description}</p>
      </div>
      {reasonCode ? <code className="w-fit rounded-sm border border-ops-line bg-ops-black px-2 py-1 font-mono text-2xs text-zinc-400">{reasonCode}</code> : null}
    </div>
  )
}

function PreviewResult({ rowCounts, sourceRange }: { rowCounts: RunAnalysisSnapshotRowCounts; sourceRange: RunAnalysisSnapshotSourceRange }) {
  return (
    <div className="space-y-3 border-t border-ops-line pt-3" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-signal-success">共 {rowCounts.total.toLocaleString('zh-CN')} 行可导出</span>
        <span className="max-w-full break-all font-mono text-2xs text-zinc-500 sm:text-right">{formatUtcRange(sourceRange)}</span>
      </div>
      <RowCounts rowCounts={rowCounts} />
    </div>
  )
}

function ReceiptHistory({
  receipts,
  selectedSnapshotId,
  isLoading,
  isError,
  onSelect,
}: {
  receipts: RunAnalysisSnapshotReceipt[]
  selectedSnapshotId: string | null
  isLoading: boolean
  isError: boolean
  onSelect: (snapshotId: string) => void
}) {
  return (
    <div className="space-y-2 border-t border-ops-line pt-4">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-medium text-zinc-100">最近快照</h4>
        {receipts.length > 0 ? <span className="text-2xs text-zinc-500">已恢复 {receipts.length} 个快照回执</span> : null}
      </div>
      {isLoading ? (
        <p className="text-xs text-zinc-500" role="status">正在恢复快照回执…</p>
      ) : isError ? (
        <p className="text-xs text-signal-danger" role="alert">最近回执读取失败，范围操作仍可独立使用。</p>
      ) : receipts.length === 0 ? (
        <p className="rounded-md border border-dashed border-ops-line p-3 text-xs text-zinc-500">此 Run 尚无分析快照回执。</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {receipts.map((item) => (
            <Button
              key={item.snapshotId}
              type="button"
              variant="outline"
              aria-label={`打开快照 ${item.snapshotId}`}
              aria-pressed={selectedSnapshotId === item.snapshotId}
              className="h-auto min-h-11 w-full min-w-0 justify-start px-3 py-2 text-left aria-pressed:border-primary/40 aria-pressed:bg-primary/10"
              onClick={() => onSelect(item.snapshotId)}
            >
              <span className={cn('size-2 rounded-full', receiptDot(item.status))} aria-hidden="true" />
              <span className="min-w-0 overflow-hidden">
                <span className="block truncate text-xs">{receiptCopy[item.status].label}</span>
                <span className="mt-0.5 block truncate font-mono text-2xs text-zinc-500">{formatUtc(item.createdAt)}</span>
              </span>
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}

function ReceiptDetail({
  receipt,
  isRefreshing,
  summary,
  summaryLoading,
  summaryError,
}: {
  receipt: RunAnalysisSnapshotReceipt
  isRefreshing: boolean
  summary?: RunAnalysisSnapshotSummary
  summaryLoading: boolean
  summaryError: boolean
}) {
  const copy = receiptCopy[receipt.status]
  return (
    <div className="space-y-4 rounded-md border border-ops-line bg-ops-raised/40 p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2" aria-live="polite">
            <Badge variant="outline" className={cn('font-mono text-2xs', copy.tone)}>{copy.label}</Badge>
            {isRefreshing ? <LoaderCircle className="size-3.5 animate-spin text-zinc-500" aria-label="正在刷新回执" /> : null}
          </div>
          <div className="mt-2 break-all font-mono text-xs text-zinc-200">{receipt.snapshotId}</div>
        </div>
        <div className="text-left text-2xs text-zinc-500 sm:text-right">
          <div>schema v{receipt.schemaVersion} · redaction v{receipt.redactionVersion}</div>
          <div className="mt-1">保留至 {formatUtc(receipt.expiresAt)}</div>
        </div>
      </div>

      {receipt.status === 'failed' ? (
        <InlineNotice
          icon={AlertTriangle}
          title="分析运行时未接收此快照"
          description={receipt.failureCode ?? 'failure_code_unavailable'}
          tone="danger"
        />
      ) : receipt.status === 'expired' ? (
        <InlineNotice
          icon={Clock3}
          title="投影保留期已结束"
          description="权威回执仍可查看，但已过期投影不再提供分析摘要。"
        />
      ) : receipt.status === 'exporting' ? (
        <InlineNotice
          icon={LoaderCircle}
          title="正在同步权威回执"
          description="此状态来自已恢复的回执；页面会短间隔刷新直到进入终态。"
        />
      ) : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-medium text-zinc-300">来源范围</span>
          <span className="max-w-full break-all font-mono text-2xs text-zinc-500 sm:text-right">{formatUtcRange(receipt.sourceRange)}</span>
        </div>
        <RowCounts rowCounts={receipt.rowCounts} />
        <p className="text-2xs text-zinc-500">共 {receipt.rowCounts.total.toLocaleString('zh-CN')} 行 · 创建于 {formatUtc(receipt.createdAt)}</p>
      </div>

      {receipt.status === 'completed' ? (
        <SummarySection
          summary={summary}
          isLoading={summaryLoading}
          isError={summaryError}
        />
      ) : null}
    </div>
  )
}

function SummarySection({ summary, isLoading, isError }: { summary?: RunAnalysisSnapshotSummary; isLoading: boolean; isError: boolean }) {
  return (
    <section aria-labelledby="run-analysis-summary-title" className="space-y-3 border-t border-ops-line pt-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 id="run-analysis-summary-title" className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <BarChart3 className="size-4 text-primary" aria-hidden="true" />
            固定分析摘要
          </h4>
          <p className="mt-1 text-xs text-zinc-500">固定聚合维度，不接受自定义查询。</p>
        </div>
        {summary ? <span className="max-w-full break-all font-mono text-2xs text-zinc-500 sm:text-right">{formatUtcRange(summary.sourceRange)}</span> : null}
      </div>

      {isLoading ? (
        <LoadingState rows={3} />
      ) : isError || !summary ? (
        <InlineNotice
          icon={AlertTriangle}
          title="摘要暂时不可用"
          description="快照回执仍然有效；这里只隐藏当前无法读取的聚合区段。"
          tone="danger"
        />
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-3">
            <SummaryMetric icon={Gauge} label="吞吐量" value={`${formatDecimal(summary.throughput.perMinute)} / 分钟`} detail={`${summary.throughput.total.toLocaleString('zh-CN')} 个事件`} />
            <SummaryMetric icon={Clock3} label="延迟" value={`${formatMilliseconds(summary.latency.averageMs)} 平均`} detail={`P95 ${formatMilliseconds(summary.latency.p95Ms)} · 最大 ${formatMilliseconds(summary.latency.maxMs)} · ${summary.latency.sampleCount} 样本`} />
            <SummaryMetric icon={AlertTriangle} label="失败率" value={formatPercent(summary.failureRate.rate)} detail={`${summary.failureRate.failed} / ${summary.failureRate.total} 失败`} />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Breakdown title="事件类型" empty={summary.eventTypes.length === 0}>
              {summary.eventTypes.map((item) => (
                <BreakdownRow key={item.eventType} label={item.eventType} value={item.count} />
              ))}
            </Breakdown>
            <Breakdown title="节点行为" empty={summary.nodes.length === 0}>
              {summary.nodes.map((item, index) => (
                <BreakdownRow
                  key={`${item.nodeId ?? 'run'}-${index}`}
                  label={item.nodeId ?? 'run-level'}
                  value={item.eventCount}
                  suffix={item.failureCount > 0 ? `${item.failureCount} 失败` : '0 失败'}
                />
              ))}
            </Breakdown>
          </div>
        </>
      )}
    </section>
  )
}

function RowCounts({ rowCounts }: { rowCounts: RunAnalysisSnapshotRowCounts }) {
  const items = [
    { label: 'Workflow Trace', value: rowCounts.workflowTraceEvents },
    { label: '采集执行指标', value: rowCounts.acquisitionExecutionMetrics },
    { label: '总行数', value: rowCounts.total },
  ]
  return (
    <dl className="grid gap-2 sm:grid-cols-3">
      {items.map((item) => (
        <div key={item.label} className="min-w-0 rounded-sm border border-ops-line bg-ops-black/60 px-3 py-2">
          <dt className="truncate text-2xs text-zinc-500">{item.label}</dt>
          <dd className="mt-1 font-mono text-sm font-semibold text-zinc-100">{item.value.toLocaleString('zh-CN')}</dd>
        </div>
      ))}
    </dl>
  )
}

function SummaryMetric({ icon: Icon, label, value, detail }: { icon: typeof Gauge; label: string; value: string; detail: string }) {
  return (
    <div className="rounded-sm border border-ops-line bg-ops-black/60 p-3">
      <div className="flex items-center gap-2 text-2xs text-zinc-500"><Icon className="size-3.5" aria-hidden="true" />{label}</div>
      <div className="mt-2 font-mono text-sm font-semibold text-zinc-100">{value}</div>
      <div className="mt-1 text-2xs leading-4 text-zinc-500">{detail}</div>
    </div>
  )
}

function Breakdown({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-sm border border-ops-line">
      <div className="border-b border-ops-line bg-ops-black/40 px-3 py-2 text-xs font-medium text-zinc-300">{title}</div>
      {empty ? <p className="p-3 text-xs text-zinc-500">此范围没有可汇总数据。</p> : <div className="divide-y divide-ops-line">{children}</div>}
    </div>
  )
}

function BreakdownRow({ label, value, suffix }: { label: string; value: number; suffix?: string }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 px-3 py-2 text-xs">
      <span className="truncate font-mono text-zinc-300">{label}</span>
      <span className="font-mono text-zinc-100">{value.toLocaleString('zh-CN')}</span>
      {suffix ? <span className="text-2xs text-zinc-500">{suffix}</span> : null}
    </div>
  )
}

function InlineNotice({
  icon: Icon,
  title,
  description,
  tone = 'neutral',
}: {
  icon: typeof AlertTriangle
  title: string
  description: string
  tone?: 'neutral' | 'danger'
}) {
  return (
    <div role={tone === 'danger' ? 'alert' : undefined} className={cn(
      'flex gap-3 rounded-md border p-3',
      tone === 'danger' ? 'border-signal-danger/25 bg-signal-danger/5' : 'border-ops-line bg-ops-raised/40',
    )}>
      <Icon className={cn('mt-0.5 size-4 shrink-0', tone === 'danger' ? 'text-signal-danger' : 'text-zinc-400')} aria-hidden="true" />
      <div>
        <div className="text-sm font-medium text-zinc-100">{title}</div>
        <div className="mt-1 text-xs leading-5 text-zinc-400">{description}</div>
      </div>
    </div>
  )
}

function buildRange(startAt: string, endAt: string): RunAnalysisSnapshotSourceRange | null {
  const start = parseUtcInput(startAt)
  const end = parseUtcInput(endAt)
  return start && end ? { startAt: start, endAt: end } : null
}

function validateRange(range: RunAnalysisSnapshotSourceRange | null, runStartAt: string, runEndAt: string) {
  if (!range) return '请输入有效的 UTC 开始与结束时间。'
  const rangeOrder = compareUtcInstants(range.startAt, range.endAt)
  const startBoundOrder = compareUtcInstants(range.startAt, runStartAt)
  const endBoundOrder = compareUtcInstants(range.endAt, runEndAt)
  if (rangeOrder == null || startBoundOrder == null || endBoundOrder == null) {
    return '此 Run 的 UTC 时间边界不可用。'
  }
  if (rangeOrder >= 0) return '开始时间必须早于结束时间。'
  if (startBoundOrder < 0) return '开始时间不能早于此 Run 的开始时间。'
  if (endBoundOrder > 0) return '结束时间不能晚于此 Run 的结束时间。'
  return null
}

function parseUtcInput(value: string) {
  if (!value) return null
  const parsed = new Date(`${value}Z`)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

function toUtcInputBoundary(value: string, rounding: 'ceil' | 'floor') {
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return ''
  const fraction = fractionalSeconds(value)
  const hasSubMillisecondRemainder = /[1-9]/.test(fraction.slice(3))
  const rounded = rounding === 'ceil' && hasSubMillisecondRemainder ? parsed + 1 : parsed
  return new Date(rounded).toISOString().slice(0, -1)
}

function formatUtc(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'UTC 时间不可用'
  const fraction = fractionalSeconds(value).replace(/0+$/, '')
  const precision = fraction ? `.${fraction}` : ''
  const utcDateTime = parsed.toISOString().replace(/\.\d{3}Z$/, '').replace('T', ' ')
  return `${utcDateTime}${precision} UTC`
}

function rangesMatch(left: RunAnalysisSnapshotSourceRange, right: RunAnalysisSnapshotSourceRange) {
  return compareUtcInstants(left.startAt, right.startAt) === 0
    && compareUtcInstants(left.endAt, right.endAt) === 0
}

function compareUtcInstants(left: string, right: string) {
  const leftMilliseconds = Date.parse(left)
  const rightMilliseconds = Date.parse(right)
  if (Number.isNaN(leftMilliseconds) || Number.isNaN(rightMilliseconds)) return null
  if (leftMilliseconds !== rightMilliseconds) return leftMilliseconds < rightMilliseconds ? -1 : 1

  const leftFraction = fractionalSeconds(left)
  const rightFraction = fractionalSeconds(right)
  const precision = Math.max(leftFraction.length, rightFraction.length)
  const normalizedLeft = leftFraction.padEnd(precision, '0')
  const normalizedRight = rightFraction.padEnd(precision, '0')
  if (normalizedLeft === normalizedRight) return 0
  return normalizedLeft < normalizedRight ? -1 : 1
}

function fractionalSeconds(value: string) {
  return value.match(/\.(\d+)(?=Z$|[+-]\d{2}:\d{2}$)/i)?.[1] ?? ''
}

function formatUtcRange(range: RunAnalysisSnapshotSourceRange) {
  return `${formatUtc(range.startAt)} → ${formatUtc(range.endAt)}`
}

function formatMilliseconds(value: number | null) {
  return value == null ? '—' : `${formatDecimal(value)} ms`
}

function formatDecimal(value: number) {
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function receiptDot(status: RunAnalysisSnapshotStatus) {
  if (status === 'completed') return 'bg-signal-success'
  if (status === 'failed') return 'bg-signal-danger'
  if (status === 'expired') return 'bg-signal-warning'
  return 'bg-primary'
}
