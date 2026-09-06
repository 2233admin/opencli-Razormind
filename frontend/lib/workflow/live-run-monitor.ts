import {
  queryWorkflowRunTrace,
  type WorkflowNodeRunEvent,
  type WorkflowRunProjection,
  type WorkflowRunScope,
  type WorkflowRunTraceResponse,
} from "./backend-runs"

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "waiting", "partial"])

export type WorkflowRunMonitorSnapshot = {
  projection: WorkflowRunProjection
  events: WorkflowNodeRunEvent[]
  newEvents: WorkflowNodeRunEvent[]
}

export type GaojixingRecoveryCase = {
  status: "waiting_verification" | "waiting_reconciliation"
  action?: string
  kind?: string
  artifactRef: string | null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function opaqueArtifactRef(value: unknown): string | null {
  return typeof value === "string" && value.startsWith("run-artifact:") ? value : null
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return
  throw signal.reason instanceof Error
    ? signal.reason
    : new DOMException("Workflow Run monitoring was aborted", "AbortError")
}

async function waitForNextPoll(intervalMs: number, signal?: AbortSignal): Promise<void> {
  throwIfAborted(signal)
  if (intervalMs <= 0) return
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, intervalMs)
    const onAbort = () => {
      clearTimeout(timer)
      reject(signal?.reason instanceof Error
        ? signal.reason
        : new DOMException("Workflow Run monitoring was aborted", "AbortError"))
    }
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}

export function extractGaojixingRecoveryCase(
  events: WorkflowNodeRunEvent[],
): GaojixingRecoveryCase | null {
  for (const event of [...events].reverse()) {
    const samples = Array.isArray(event.details.sampleOutputs) ? event.details.sampleOutputs : []
    for (const value of [...samples].reverse()) {
      const sample = record(value)
      if (!sample || sample.schema !== "gaojixing.collection-run.v1") continue
      const status = sample.status
      if (status !== "waiting_verification" && status !== "waiting_reconciliation") return null
      const recoveryCase = record(sample.recoveryCase)
      const evidence = Array.isArray(recoveryCase?.evidence) ? recoveryCase.evidence : []
      const artifactRef = opaqueArtifactRef(sample.artifactRef)
        ?? opaqueArtifactRef(recoveryCase?.artifactRef)
        ?? evidence.map((item) => opaqueArtifactRef(record(item)?.artifactRef)).find(Boolean)
        ?? null
      if (!artifactRef && status === "waiting_verification") return null
      return {
        status,
        ...(typeof recoveryCase?.action === "string" ? { action: recoveryCase.action } : {}),
        ...(typeof recoveryCase?.kind === "string"
          ? { kind: recoveryCase.kind }
          : typeof sample.waitingKind === "string"
            ? { kind: sample.waitingKind }
            : {}),
        artifactRef,
      }
    }
  }
  return null
}

export async function monitorWorkflowRun(
  runId: string,
  options: {
    authorization?: string | null
    scope?: WorkflowRunScope
    signal?: AbortSignal
    intervalMs?: number
    onSnapshot?: (snapshot: WorkflowRunMonitorSnapshot) => void
  } = {},
): Promise<WorkflowRunMonitorSnapshot> {
  const events: WorkflowNodeRunEvent[] = []
  let afterSequence = 0

  while (true) {
    throwIfAborted(options.signal)
    const trace: WorkflowRunTraceResponse = await queryWorkflowRunTrace(runId, {
      authorization: options.authorization,
      scope: options.scope,
      signal: options.signal,
      afterSequence,
    })
    throwIfAborted(options.signal)
    events.push(...trace.events)
    afterSequence = trace.nextAfterSequence
    const snapshot = {
      projection: trace.projection,
      events: [...events],
      newEvents: trace.events,
    }
    options.onSnapshot?.(snapshot)
    if (!ACTIVE_RUN_STATUSES.has(trace.projection.status)) return snapshot
    await waitForNextPoll(options.intervalMs ?? 1_000, options.signal)
  }
}
