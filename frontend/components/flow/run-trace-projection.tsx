"use client"

import type { WorkflowNodeRunEvent, WorkflowRunProjection } from "@/lib/workflow/backend-runs"
import { formatNativeIntelligenceResultPreview } from "@/lib/workflow/native-intelligence-result-preview"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { MetricGrid, readRecord } from "./run-trace-primitives"

const INSPECTABLE_NATIVE_ACTIONS: Record<string, true> = {
  "simulation.timeline": true,
  "simulation.stats": true,
  "interviews.history": true,
  "report.progress": true,
  "report.read": true,
  "report.ask": true,
  "report.answers": true,
}

export function RealRunProjection({
  projection,
  eventCount,
  blockedCount,
  batchCount,
  itemCount,
}: {
  projection: WorkflowRunProjection
  eventCount: number
  blockedCount: number
  batchCount: number
  itemCount: number
}) {
  return (
    <div className="space-y-3">
      <MetricGrid
        title="Run Projection"
        metrics={[
          { key: "status", label: "Status", value: projection.status, tone: projection.status === "completed" ? "good" : projection.status === "failed" || projection.status === "blocked" ? "warn" : "neutral" },
          { key: "events", label: "Events", value: `${eventCount || projection.eventCount}`, tone: eventCount > 0 ? "good" : "neutral" },
          { key: "nodes", label: "Nodes", value: `${projection.nodeStates.length}`, tone: projection.nodeStates.length > 0 ? "good" : "neutral" },
          { key: "blocked", label: "Blocked", value: `${blockedCount}`, tone: blockedCount === 0 ? "good" : "warn" },
          { key: "batches", label: "Batches", value: `${batchCount}`, tone: batchCount > 0 ? "good" : "neutral" },
          { key: "items", label: "Items", value: `${itemCount}`, tone: itemCount > 0 ? "good" : "neutral" },
        ]}
      />
      <div className="rounded-md border bg-card p-3">
        <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
          <span className="min-w-0 truncate text-foreground">run {projection.runId}</span>
          <Badge variant={projection.valid ? "secondary" : "outline"} className="font-mono text-[9px] uppercase">
            {projection.valid ? "valid" : "invalid"}
          </Badge>
        </div>
        <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">trace {projection.traceId}</p>
      </div>
      <div className="space-y-1.5">
        {projection.nodeStates.map((node) => (
          <div key={node.nodeId} className="rounded-md border bg-card px-2.5 py-2">
            <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
              <span className="min-w-0 truncate text-foreground">{node.nodeId}</span>
              <span className={cn("shrink-0 uppercase", node.status === "completed" ? "text-[#2f9e44]" : node.status === "blocked" || node.status === "failed" ? "text-destructive" : "text-muted-foreground")}>
                {node.status}
              </span>
            </div>
            <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
              {node.eventCount} events · {node.batches.length} batches
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function RunEventCard({ event }: { event: WorkflowNodeRunEvent }) {
  const itemCount = event.batch?.itemCount ?? 0
  const sample = Array.isArray(event.details.sampleOutputs)
    ? readRecord(event.details.sampleOutputs[0])
    : null
  const native = sample?.action ? sample : event.details
  const nativeAction = typeof native.action === "string" ? native.action : null
  const domainState = typeof native.state === "string"
    ? native.state
    : typeof native.domainState === "string"
      ? native.domainState
      : null
  const sessionId = typeof native.sessionId === "string" ? native.sessionId : null
  const command = typeof native.command === "string" ? native.command : null
  const artifactIds = Array.isArray(native.artifactIds)
    ? native.artifactIds.filter((value): value is string => typeof value === "string")
    : []
  const provenance = readRecord(native.provenance)
  const nativeResult = native.result
  const result = readRecord(native.result)
  const artifacts = Array.isArray(result?.artifacts)
    ? result.artifacts.map(readRecord).filter((value): value is Record<string, unknown> => Boolean(value))
    : []
  const simulated = artifacts.some((artifact) => artifact.simulated === true)
  const groundingIds = artifacts.flatMap((artifact) =>
    Array.isArray(artifact.groundingArtifactIds)
      ? artifact.groundingArtifactIds.filter(
          (value): value is string => typeof value === "string",
        )
      : [],
  )
  const nativeResultPreview =
    nativeAction && INSPECTABLE_NATIVE_ACTIONS[nativeAction] && nativeResult !== undefined
      ? formatNativeIntelligenceResultPreview(nativeResult)
      : null
  return (
    <div className="rounded-md border bg-card p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 font-mono text-[11px]">
          <span className="text-muted-foreground">{event.sequence}</span>
          <span className="mx-1.5 text-muted-foreground/50">/</span>
          <span>{event.eventType}</span>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
          {itemCount} items
        </span>
      </div>
      <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{event.nodeId}</p>
      {event.message ?? event.blockReason?.message ? (
        <p className="mt-1.5 line-clamp-2 break-all font-mono text-[10px] leading-relaxed text-muted-foreground/80">
          {event.message ?? event.blockReason?.message}
        </p>
      ) : null}
      {nativeAction || command || domainState || sessionId || artifactIds.length ? (
        <div className="mt-2 rounded-sm border bg-background/70 p-2 font-mono text-[9px] text-muted-foreground">
          <div className="flex flex-wrap gap-x-2 gap-y-1">
            {nativeAction ? <span>action {nativeAction}</span> : null}
            {command ? <span>transition {command}</span> : null}
            {domainState ? <span>state {domainState}</span> : null}
            {provenance?.credentialFree === true ? <span>credential-free</span> : null}
            {provenance?.offline === true ? <span>offline</span> : null}
            {simulated ? <span>simulated</span> : null}
          </div>
          {sessionId ? <p className="mt-1 truncate">session {sessionId}</p> : null}
          {artifactIds.length ? (
            <p className="mt-1 truncate" title={artifactIds.join(", ")}>
              artifacts {artifactIds.join(", ")}
            </p>
          ) : null}
          {groundingIds.length ? (
            <p className="mt-1 truncate" title={groundingIds.join(", ")}>
              grounding {groundingIds.join(", ")}
            </p>
          ) : null}
          {nativeResultPreview ? (
            <div className="mt-2">
              <p className="mb-1 uppercase">result</p>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-sm border bg-card p-2 text-[9px] leading-relaxed text-foreground">
                {nativeResultPreview}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
