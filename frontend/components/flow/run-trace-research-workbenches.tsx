"use client"

import { Boxes, Loader2, Play } from "lucide-react"
import type {
  WorkflowEvidenceBatchDetail,
  WorkflowEvidenceBatchProjection,
  WorkflowEvidenceBatchSummary,
  WorkflowResearchLedgerResponse,
} from "@/lib/workflow/backend-runs"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { MetricGrid, SectionCaption } from "./run-trace-primitives"

export type EvidenceBatchState = {
  status: "idle" | "loading" | "ready" | "error"
  projection: WorkflowEvidenceBatchProjection | null
  batches: WorkflowEvidenceBatchSummary[]
  detail: WorkflowEvidenceBatchDetail | null
  selectedBatchId: string | null
  error: string | null
}

export function ResearchLedgerWorkbench({
  ledger,
  allowSourceOutputs,
  sourceOutputs,
  onSourceOutputsChange,
  onContinue,
  continuing,
  error,
}: {
  ledger: WorkflowResearchLedgerResponse
  allowSourceOutputs: boolean
  sourceOutputs: string
  onSourceOutputsChange: (value: string) => void
  onContinue: () => void
  continuing: boolean
  error: string | null
}) {
  const latest = ledger.entries.at(-1)
  const canContinue = allowSourceOutputs && latest?.researchStatus === "needs_evidence"
    && Boolean(latest.revisionId && latest.proposal?.proposalId)
  return (
    <div className="space-y-3" aria-label="Research revision ledger">
      <div className="flex items-center justify-between gap-2">
        <SectionCaption>Research Ledger</SectionCaption>
        <Badge variant="outline" className="font-mono text-[9px] uppercase">
          {latest?.researchStatus ?? "running"}
        </Badge>
      </div>
      <div className="space-y-1.5">
        {ledger.entries.map((entry) => (
          <div key={entry.runId} className="rounded-md border bg-card p-2.5">
            <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
              <span>iteration {entry.iteration}</span>
              <span className="uppercase text-muted-foreground">{entry.decision ?? "pending"}</span>
            </div>
            <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground">
              {entry.revisionId ?? entry.runId}
            </p>
            {entry.gaps.length ? (
              <p className="mt-1 text-[10px] text-[#d97706]">gaps: {entry.gaps.join(", ")}</p>
            ) : null}
            {entry.gateReasons.length ? (
              <p className="mt-1 text-[10px] text-destructive">
                gate: {entry.gateReasons.join(", ")}
              </p>
            ) : null}
          </div>
        ))}
      </div>
      {latest?.evidenceRefs.length ? (
        <div className="space-y-1.5">
          <SectionCaption>Evidence refs</SectionCaption>
          {latest.evidenceRefs.slice(0, 4).map((reference, index) => {
            const evidenceId = typeof reference.evidenceId === "string" ? reference.evidenceId : `evidence-${index + 1}`
            const url = typeof reference.url === "string" ? reference.url : null
            const manifest = typeof reference.manifestUri === "string" ? reference.manifestUri : null
            return (
              <div key={`${evidenceId}-${index}`} className="rounded-md border bg-card px-2.5 py-2">
                {url ? (
                  <a className="block truncate font-mono text-[10px] underline-offset-2 hover:underline" href={url} target="_blank" rel="noreferrer">
                    {evidenceId}
                  </a>
                ) : (
                  <p className="truncate font-mono text-[10px]">{evidenceId}</p>
                )}
                {manifest ? (
                  <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground">{manifest}</p>
                ) : null}
              </div>
            )
          })}
        </div>
      ) : null}
      {canContinue ? (
        <div className="space-y-2 rounded-md border border-[#d97706]/30 bg-[#d97706]/5 p-2.5">
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            Paste approved source outputs for the existing source node IDs. The server starts one
            immutable child Run and advances the bounded budget.
          </p>
          <Textarea
            value={sourceOutputs}
            onChange={(event) => onSourceOutputsChange(event.target.value)}
            className="min-h-24 font-mono text-[10px]"
            aria-label="Research continuation source outputs JSON"
          />
          <Button size="sm" className="w-full" onClick={onContinue} disabled={continuing}>
            {continuing ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Continue research
          </Button>
          {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
        </div>
      ) : null}
    </div>
  )
}

export function EvidenceBatchWorkbench({
  state,
  onSelectBatch,
}: {
  state: EvidenceBatchState
  onSelectBatch: (batchId: string) => void
}) {
  const projection = state.projection
  const batches = state.batches
  const recordCount = batches.reduce((sum, batch) => sum + batch.recordCount, 0)
  const partialCount = projection?.summaries.filter((summary) => summary.status === "partial").length ?? 0
  const blockedCount = projection?.nodes.filter((node) => node.status === "blocked" || node.status === "failed").length ?? 0
  return (
    <div className="space-y-3" aria-label="EvidenceBatch results">
      <div className="flex items-center justify-between gap-2">
        <SectionCaption>Result Workbench</SectionCaption>
        {state.status === "loading" ? <Loader2 className="size-3 animate-spin text-muted-foreground" /> : null}
      </div>

      {projection ? (
        <MetricGrid
          title="EvidenceBatch Projection"
          metrics={[
            { key: "status", label: "Status", value: projection.status, tone: projection.status === "completed" ? "good" : "warn" },
            { key: "batches", label: "Batches", value: `${batches.length}`, tone: batches.length > 0 ? "good" : "neutral" },
            { key: "records", label: "Records", value: `${recordCount}`, tone: recordCount > 0 ? "good" : "neutral" },
            { key: "partial", label: "Partial", value: `${partialCount}`, tone: partialCount === 0 ? "good" : "warn" },
            { key: "missing", label: "Missing", value: `${projection.missingSources.length}`, tone: projection.missingSources.length === 0 ? "good" : "warn" },
            { key: "blocked", label: "Blocked", value: `${blockedCount}`, tone: blockedCount === 0 ? "good" : "warn" },
          ]}
        />
      ) : null}

      {state.error ? (
        <div className="rounded-md border border-[#d97706]/30 bg-[#d97706]/10 p-2.5 text-[11px] leading-relaxed text-[#d97706]">
          {state.error}
        </div>
      ) : null}

      {projection?.missingSources.length ? (
        <div className="space-y-1.5">
          {projection.missingSources.slice(0, 4).map((source) => (
            <div
              key={`${source.nodeId}-${source.sourceGroup ?? "source"}`}
              className="rounded-md border border-[#d97706]/25 bg-[#d97706]/10 p-2.5"
            >
              <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
                <span className="min-w-0 truncate text-[#d97706]">{source.nodeId}</span>
                <span className="shrink-0 uppercase text-muted-foreground">{source.status}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
                {source.reasons[0]?.message ?? `Missing source ${source.sourceGroup ?? "output"}`}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {batches.length > 0 ? (
        <div className="space-y-1.5">
          {batches.map((batch) => (
            <button
              key={batch.batchId}
              type="button"
              onClick={() => onSelectBatch(batch.batchId)}
              className={cn(
                "block w-full rounded-md border bg-card p-2.5 text-left transition-colors hover:border-foreground/30",
                state.selectedBatchId === batch.batchId && "border-foreground/40 bg-accent/40",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-1.5 font-mono text-[10px]">
                  <Boxes className="size-3 shrink-0 text-muted-foreground" />
                  <span className="truncate">{batch.batchId}</span>
                </span>
                <span className={cn(
                  "shrink-0 font-mono text-[9px] uppercase",
                  batch.status === "completed" ? "text-[#2f9e44]" : "text-[#d97706]",
                )}>
                  {batch.status}
                </span>
              </div>
              <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                {batch.nodeId} · {batch.sourceGroup ?? "ungrouped"} · {batch.recordCount} records
              </p>
              {batch.manifestUri || batch.odpRef ? (
                <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground/80">
                  {batch.manifestUri ?? batch.odpRef}
                </p>
              ) : null}
            </button>
          ))}
        </div>
      ) : state.status === "ready" ? (
        <div className="rounded-md border border-dashed p-3 text-center text-xs text-muted-foreground">
          no EvidenceBatch output
        </div>
      ) : null}

      {state.detail ? <EvidenceBatchDetailCard detail={state.detail} /> : null}
    </div>
  )
}

function EvidenceBatchDetailCard({ detail }: { detail: WorkflowEvidenceBatchDetail }) {
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <SectionCaption>Batch Detail</SectionCaption>
        <Badge variant="outline" className="font-mono text-[9px] uppercase">{detail.sourceCoverage.status}</Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] text-muted-foreground">
        <span>{detail.itemCount} items</span>
        <span className="text-right">{detail.recordCount} records</span>
      </div>
      <p className="mt-2 line-clamp-2 font-mono text-[9px] leading-relaxed text-muted-foreground">
        source {detail.sourceCoverage.sourceGroup ?? "ungrouped"} · {detail.sourceCoverage.batchCount} batches
      </p>
      {detail.manifestUri || detail.odpRef ? (
        <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground/80">
          {detail.manifestUri ?? detail.odpRef}
        </p>
      ) : null}
    </div>
  )
}
