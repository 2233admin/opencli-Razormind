"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Activity, Boxes, FileInput, Loader2, Play, RotateCcw } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { getApiAuthToken } from "@/lib/api/auth-token"
import { useFlowStore } from "@/lib/flow/store"
import { fetchWorkflowCapabilities } from "@/lib/workflow/backend-capabilities"
import { compileWorkflowProject, type WorkflowCompileResponse } from "@/lib/workflow/backend-compile"
import {
  findOpenCLIHDAWorkflowPackageNodeId,
  traceOpenCLIHDAWorkflow,
  type WorkflowOpenCLIHDATraceResponse,
} from "@/lib/workflow/backend-opencli-hda-trace"
import {
  continueWorkflowResearch,
  fetchWorkflowEvidenceBatchDetail,
  fetchWorkflowEvidenceBatchProjection,
  fetchWorkflowEvidenceBatches,
  fetchWorkspaceWorkflowEvidenceBatchDetail,
  fetchWorkspaceWorkflowEvidenceBatchProjection,
  fetchWorkspaceWorkflowEvidenceBatches,
  fetchWorkflowResearchLedger,
  buildWorkflowRunInputTemplate,
  continueWorkflowResearch,
  getWorkflowRunFileInput,
  parseWorkflowRunInput,
  replayWorkspaceWorkflowRunTrace,
  replayWorkflowRunEventStream,
  resumeGaojixingWorkflowRun,
  startWorkspaceWorkflowRun,
  startWorkflowRun,
  type WorkflowNodeRunEvent,
  type WorkflowResearchLedgerResponse,
  type WorkflowRunProjection,
  type WorkflowRunStatus,
} from "@/lib/workflow/backend-runs"
import { fetchWorkflowToolCapabilities } from "@/lib/workflow/backend-tool-capabilities"
import {
  extractGaojixingRecoveryCase,
  monitorWorkflowRun,
} from "@/lib/workflow/live-run-monitor"
import {
  buildNativeIntelligencePreviewEvidence,
  findNativeIntelligenceWorkflowPackageNodeId,
  type NativeIntelligencePreviewEvidence,
} from "@/lib/workflow/native-intelligence-preview"
import { applyRuntimeNodePatches, buildRuntimeNodePatches } from "@/lib/workflow/runtime-bridge"
import type { WorkflowProject } from "@/lib/workflow/schema"
import {
  dispatchRunPanelExtensions,
  type RunPanelExtension,
  type RunPanelScope,
} from "@/lib/workflow/run-panel-extensions"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  EvidenceBatchWorkbench,
  ResearchLedgerWorkbench,
  type EvidenceBatchState,
} from "./run-trace-research-workbenches"
import { RealRunProjection, RunEventCard } from "./run-trace-projection"
import { MetricGrid, readRecord, SectionCaption } from "./run-trace-primitives"

type RealRunState =
  | { status: "idle"; projection: null; events: WorkflowNodeRunEvent[]; error: null }
  | { status: "running"; projection: WorkflowRunProjection | null; events: WorkflowNodeRunEvent[]; error: null }
  | { status: "ready"; projection: WorkflowRunProjection; events: WorkflowNodeRunEvent[]; error: null }
  | { status: "error"; projection: WorkflowRunProjection | null; events: WorkflowNodeRunEvent[]; error: string }

type BackendPreviewState =
  | { status: "idle"; compile: null; trace: null; native: null; error: null }
  | { status: "running"; compile: WorkflowCompileResponse | null; trace: WorkflowOpenCLIHDATraceResponse | null; native: NativeIntelligencePreviewEvidence | null; error: null }
  | { status: "ready"; compile: WorkflowCompileResponse; trace: WorkflowOpenCLIHDATraceResponse | null; native: NativeIntelligencePreviewEvidence | null; error: null }
  | { status: "blocked"; compile: WorkflowCompileResponse; trace: WorkflowOpenCLIHDATraceResponse | null; native: NativeIntelligencePreviewEvidence | null; error: null }
  | { status: "error"; compile: WorkflowCompileResponse | null; trace: WorkflowOpenCLIHDATraceResponse | null; native: NativeIntelligencePreviewEvidence | null; error: string }



const RUN_STATUS_LABELS: Record<WorkflowRunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  waiting: "等待中",
  partial: "处理中",
  partial_success: "部分成功",
  blocked: "已阻止",
  completed: "成功",
  failed: "失败",
}

export function RunTracePanel({
  runRequestId = 0,
  extensions = [],
  scope = null,
}: {
  runRequestId?: number
  extensions?: readonly RunPanelExtension[]
  scope?: RunPanelScope | null
}) {
  const runButtonRef = useRef<HTMLButtonElement>(null)
  const questionBankInputRef = useRef<HTMLInputElement>(null)
  const runMonitorAbortRef = useRef<AbortController | null>(null)
  const searchParams = useSearchParams()
  const workflowProject = useFlowStore((state) => state.workflowProject)
  const selectedNodeId = useFlowStore((state) => state.nodes.find((node) => node.selected)?.id ?? null)
  const nodeCount = useFlowStore((state) => state.nodes.length)
  const edgeCount = useFlowStore((state) => state.edges.length)
  const setNodes = useFlowStore((state) => state.setNodes)
  const applyWorkflowNodeRunEvent = useFlowStore((state) => state.applyWorkflowNodeRunEvent)
  const applyWorkflowRunProjection = useFlowStore((state) => state.applyWorkflowRunProjection)
  const applyWorkflowEvidenceBatchProjection = useFlowStore((state) => state.applyWorkflowEvidenceBatchProjection)
  const [runState, setRunState] = useState<RealRunState>({ status: "idle", projection: null, events: [], error: null })
  const extensionAuthorization = (() => {
    const token = getApiAuthToken()
    return token ? `Bearer ${token}` : null
  })()
  const [backendState, setBackendState] = useState<BackendPreviewState>({ status: "idle", compile: null, trace: null, native: null, error: null })
  const [evidenceState, setEvidenceState] = useState<EvidenceBatchState>({
    status: "idle",
    projection: null,
    batches: [],
    detail: null,
    selectedBatchId: null,
    error: null,
  })
  const [importNodeId, setImportNodeId] = useState("")
  const [importOutputText, setImportOutputText] = useState("[\n  {\n    \"title\": \"Imported example\",\n    \"url\": \"https://example.com/item\"\n  }\n]")
  const [importError, setImportError] = useState<string | null>(null)
  const runInputTemplateText = useMemo(
    () => JSON.stringify(buildWorkflowRunInputTemplate(workflowProject), null, 2),
    [workflowProject],
  )
  const runFileInput = useMemo(() => getWorkflowRunFileInput(workflowProject), [workflowProject])
  const [runInputText, setRunInputText] = useState(runInputTemplateText)
  const [runInputError, setRunInputError] = useState<string | null>(null)
  const [questionBankFile, setQuestionBankFile] = useState<File | null>(null)
  const [isResumingGaojixing, setIsResumingGaojixing] = useState(false)

  const workflowRunScope = useMemo(() => {
    const workspaceId = searchParams.get("workspace")?.trim() ?? ""
    const projectId = searchParams.get("project")?.trim() ?? ""
    const workflowId = searchParams.get("workflow")?.trim() ?? ""
    return workspaceId && projectId && workflowId
      ? { workspaceId, projectId, workflowId }
      : undefined
  }, [searchParams])

  useEffect(() => {
    setRunInputText(runInputTemplateText)
    setRunInputError(null)
    setQuestionBankFile(null)
    if (questionBankInputRef.current) questionBankInputRef.current.value = ""
  }, [runInputTemplateText, runFileInput])

  useEffect(() => () => {
    runMonitorAbortRef.current?.abort()
  }, [workflowProject.id])

  const outputInputNodes = useMemo(() => collectOutputInputNodes(workflowProject), [workflowProject])
  const selectedSourceId = outputInputNodes.some((node) => node.id === selectedNodeId) ? selectedNodeId : null
  const effectiveImportNodeId = outputInputNodes.some((node) => node.id === importNodeId)
    ? importNodeId
    : selectedSourceId ?? outputInputNodes[0]?.id ?? ""
  const [researchLedger, setResearchLedger] = useState<WorkflowResearchLedgerResponse | null>(null)
  const [continuationInput, setContinuationInput] = useState("{}")
  const [continuationKey, setContinuationKey] = useState("")
  const [continuationError, setContinuationError] = useState<string | null>(null)
  const [isContinuing, setIsContinuing] = useState(false)

  const projection = runState.projection
  const errors = projection?.errors ?? []
  const blockedCount = projection?.nodeStates.filter((node) => node.status === "blocked" || node.status === "failed").length ?? 0
  const batchCount = projection?.nodeStates.reduce((sum, node) => sum + node.batches.length, 0) ?? 0
  const itemCount = projection?.nodeStates.reduce(
    (sum, node) => sum + node.batches.reduce((inner, batch) => inner + batch.itemCount, 0),
    0,
  ) ?? 0
  const latestEvents = useMemo(() => runState.events.slice(-8).reverse(), [runState.events])
  const gaojixingRecoveryCase = useMemo(
    () => extractGaojixingRecoveryCase(runState.events),
    [runState.events],
  )
  const isRunning = runState.status === "running"
  const isBackendRunning = backendState.status === "running"

  const monitorActiveRun = async (started: WorkflowRunProjection, authorization: string | null) => {
    runMonitorAbortRef.current?.abort()
    const controller = new AbortController()
    runMonitorAbortRef.current = controller
    const scope = runFileInput ? workflowRunScope : undefined
    try {
      const finalSnapshot = await monitorWorkflowRun(started.runId, {
        authorization,
        scope,
        signal: controller.signal,
        onSnapshot: (snapshot) => {
          for (const event of snapshot.newEvents) applyWorkflowNodeRunEvent(event)
          applyWorkflowRunProjection(snapshot.projection)
          setRunState({
            status: ["queued", "running", "waiting", "partial"].includes(snapshot.projection.status)
              ? "running"
              : "ready",
            projection: snapshot.projection,
            events: snapshot.events,
            error: null,
          })
        },
      })
      applyWorkflowRunProjection(finalSnapshot.projection)
      setRunState({
        status: "ready",
        projection: finalSnapshot.projection,
        events: finalSnapshot.events,
        error: null,
      })
      await Promise.all([
        loadEvidenceBatchResults(finalSnapshot.projection.runId, authorization),
        loadResearchLedger(finalSnapshot.projection.runId, authorization),
      ])
    } catch (error) {
      if (controller.signal.aborted) return
      throw error
    } finally {
      if (runMonitorAbortRef.current === controller) runMonitorAbortRef.current = null
    }
  }

  const runBackendWorkflow = async (sourceOutputs?: Record<string, Array<Record<string, unknown>>>) => {
    if (runFileInput && !questionBankFile) {
      setRunInputError("请选择本次题库")
      return
    }
    let input
    try {
      input = parseWorkflowRunInput(workflowProject, runInputText)
      setRunInputError(null)
    } catch (error) {
      setRunInputError(error instanceof Error ? error.message : "运行输入格式无效")
      return
    }
    const submittedQuestionBankFile = questionBankFile
    setRunState((current) => ({ status: "running", projection: current.projection, events: current.events, error: null }))
    try {
      if (scope && sourceOutputs) {
        throw new Error("已发布的 Studio workflow 不支持导入 source outputs")
      }
      const token = getApiAuthToken()
      const authorization = token ? `Bearer ${token}` : null
      const started = await startWorkflowRun(workflowProject, {
        authorization,
        sourceOutputs,
        input,
        ...(runFileInput && workflowRunScope ? { scope: workflowRunScope } : {}),
        ...(submittedQuestionBankFile ? { questionBankFile: submittedQuestionBankFile } : {}),
      })
      setQuestionBankFile((current) => current === submittedQuestionBankFile ? null : current)
      if (questionBankInputRef.current?.files?.[0] === submittedQuestionBankFile) {
        questionBankInputRef.current.value = ""
      }
      applyWorkflowRunProjection(started)
      setRunState({ status: "running", projection: started, events: [], error: null })
      await monitorActiveRun(started, authorization)
    } catch (error) {
      setRunState((current) => ({
        status: "error",
        projection: current.projection,
        events: current.events,
        error: error instanceof Error ? error.message : "Workflow run failed",
      }))
    }
  }

  const runImportedOutput = async () => {
    if (runFileInput) {
      setImportError("题库文件 Run 不支持导入节点输出。")
      return
    }
    if (!effectiveImportNodeId) {
      setImportError("当前工作流没有可接收导入输出的输入节点。")
      return
    }
    try {
      const decoded: unknown = JSON.parse(importOutputText)
      const items = Array.isArray(decoded)
        ? decoded
        : decoded && typeof decoded === "object" && Array.isArray((decoded as { items?: unknown }).items)
          ? (decoded as { items: unknown[] }).items
          : null
      if (!items || !items.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
        throw new Error("输出必须是对象数组，或形如 { \"items\": [...] }。")
      }
      setImportError(null)
      await runBackendWorkflow({ [effectiveImportNodeId]: items as Array<Record<string, unknown>> })
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "导入输出格式无效")
    }
  }

  const resumeGaojixingRun = async () => {
    if (!projection || !gaojixingRecoveryCase || !workflowRunScope) return
    setIsResumingGaojixing(true)
    setRunState((current) => ({ ...current, status: "running", error: null }))
    try {
      const token = getApiAuthToken()
      const authorization = token ? `Bearer ${token}` : null
      const resumed = await resumeGaojixingWorkflowRun(projection.runId, {
        authorization,
        scope: workflowRunScope,
      })
      applyWorkflowRunProjection(resumed)
      setRunState((current) => ({
        status: "running",
        projection: resumed,
        events: current.events,
        error: null,
      }))
      await monitorActiveRun(resumed, authorization)
    } catch (error) {
      setRunState((current) => ({
        ...current,
        status: "error",
        error: error instanceof Error ? error.message : "Gaojixing Run resume failed",
      }))
    } finally {
      setIsResumingGaojixing(false)
    }
  }

  useEffect(() => {
    if (runRequestId > 0) runButtonRef.current?.click()
  }, [runRequestId])

  const loadEvidenceBatchResults = async (runId: string, authorization: string | null) => {
    setEvidenceState((current) => ({ ...current, status: "loading", error: null, detail: null, selectedBatchId: null }))
    try {
      const [batchList, projection] = await (scope
        ? Promise.all([
            fetchWorkspaceWorkflowEvidenceBatches(scope, runId, { authorization }),
            fetchWorkspaceWorkflowEvidenceBatchProjection(scope, runId, { authorization }),
          ])
        : Promise.all([
            fetchWorkflowEvidenceBatches(runId, { authorization }),
            fetchWorkflowEvidenceBatchProjection(runId, { authorization }),
          ]))
      applyWorkflowEvidenceBatchProjection(projection, batchList.batches)
      setEvidenceState({
        status: "ready",
        projection,
        batches: batchList.batches,
        detail: null,
        selectedBatchId: null,
        error: null,
      })
    } catch (error) {
      setEvidenceState((current) => ({
        ...current,
        status: "error",
        error: error instanceof Error ? error.message : "EvidenceBatch projection failed",
      }))
    }
  }

  const loadResearchLedger = async (runId: string, authorization: string | null) => {
    try {
      const ledger = await fetchWorkflowResearchLedger(runId, { authorization })
      setResearchLedger(
        ledger.entries.some((entry) => entry.revisionId || entry.decision) ? ledger : null,
      )
    } catch {
      setResearchLedger(null)
    }
  }

  const selectEvidenceBatch = async (batchId: string) => {
    if (!projection) return
    setEvidenceState((current) => ({ ...current, status: "loading", selectedBatchId: batchId, detail: null, error: null }))
    try {
      const token = getApiAuthToken()
      const detail = scope
        ? await fetchWorkspaceWorkflowEvidenceBatchDetail(scope, projection.runId, batchId, {
            authorization: token ? `Bearer ${token}` : null,
          })
        : await fetchWorkflowEvidenceBatchDetail(projection.runId, batchId, {
            authorization: token ? `Bearer ${token}` : null,
          })
      setEvidenceState((current) => ({ ...current, status: "ready", detail, error: null }))
    } catch (error) {
      setEvidenceState((current) => ({
        ...current,
        status: "error",
        error: error instanceof Error ? error.message : "EvidenceBatch detail failed",
      }))
    }
  }

  const continueResearchRun = async () => {
    if (runFileInput) {
      setContinuationError("题库文件 Run 不支持 sourceOutputs continuation。")
      return
    }
    const latest = researchLedger?.entries.at(-1)
    if (!projection || !latest?.revisionId || !latest.proposal?.proposalId) return
    setIsContinuing(true)
    setContinuationError(null)
    try {
      const parsed = JSON.parse(continuationInput) as Record<string, unknown>
      if (
        !parsed
        || Array.isArray(parsed)
        || typeof parsed !== "object"
        || Object.keys(parsed).length === 0
        || Object.values(parsed).some(
          (items) => !Array.isArray(items) || items.some((item) => !item || typeof item !== "object" || Array.isArray(item)),
        )
      ) {
        throw new Error("sourceOutputs 必须是非空的 { nodeId: object[] } JSON")
      }
      const sourceOutputs = parsed as Record<string, Array<Record<string, unknown>>>
      const token = getApiAuthToken()
      const authorization = token ? `Bearer ${token}` : null
      const idempotencyKey = continuationKey || crypto.randomUUID()
      setContinuationKey(idempotencyKey)
      const continued = await continueWorkflowResearch(
        projection.runId,
        {
          expectedRevisionId: latest.revisionId,
          proposalId: latest.proposal.proposalId,
          idempotencyKey,
          sourceOutputs,
        },
        { authorization },
      )
      applyWorkflowRunProjection(continued.projection)
      setRunState((current) => ({
        status: "running",
        projection: continued.projection,
        events: current.events,
        error: null,
      }))
      const replay = await replayWorkflowRunEventStream(continued.childRunId, { authorization })
      for (const event of replay.events) applyWorkflowNodeRunEvent(event)
      const finalProjection = replay.projection ?? continued.projection
      applyWorkflowRunProjection(finalProjection)
      setRunState({
        status: "ready",
        projection: finalProjection,
        events: replay.events,
        error: null,
      })
      await Promise.all([
        loadEvidenceBatchResults(finalProjection.runId, authorization),
        loadResearchLedger(finalProjection.runId, authorization),
      ])
      setContinuationInput("{}")
      setContinuationKey("")
    } catch (error) {
      setContinuationError(error instanceof Error ? error.message : "Research continuation failed")
    } finally {
      setIsContinuing(false)
    }
  }

  const runBackendPreview = async () => {
    setBackendState((current) => ({ status: "running", compile: current.compile, trace: current.trace, native: current.native, error: null }))
    try {
      const token = getApiAuthToken()
      const authorization = token ? `Bearer ${token}` : null
      const nativePackageNodeId = findNativeIntelligenceWorkflowPackageNodeId(workflowProject)
      const [compile, nativeDependencies] = await Promise.all([
        compileWorkflowProject(workflowProject, { authorization }),
        nativePackageNodeId
          ? Promise.all([
              fetchWorkflowCapabilities({ authorization }),
              fetchWorkflowToolCapabilities({ authorization }),
            ])
          : Promise.resolve(null),
      ])
      const openCLIPackageNodeId = findOpenCLIHDAWorkflowPackageNodeId(workflowProject)
      const trace = compile.valid && openCLIPackageNodeId
        ? await traceOpenCLIHDAWorkflow(workflowProject, {
            authorization,
            packageNodeId: openCLIPackageNodeId,
          })
        : null
      const native = nativeDependencies
        ? buildNativeIntelligencePreviewEvidence({
            project: workflowProject,
            compile,
            capabilities: nativeDependencies[0],
            tools: nativeDependencies[1].tools,
          })
        : null
      const patches = buildRuntimeNodePatches({ compile, trace })
      setNodes((nodes) => applyRuntimeNodePatches(nodes, patches))
      setBackendState({
        status: compile.valid
          && (trace === null || trace.valid)
          && (native === null || native.status === "ready")
          ? "ready"
          : "blocked",
        compile,
        trace,
        native,
        error: null,
      })
    } catch (error) {
      setBackendState((current) => ({
        status: "error",
        compile: current.compile,
        trace: current.trace,
        native: current.native,
        error: error instanceof Error ? error.message : "Backend runtime preview failed",
      }))
    }
  }

  const resetRun = () => {
    setRunState({ status: "idle", projection: null, events: [], error: null })
    setBackendState({ status: "idle", compile: null, trace: null, native: null, error: null })
    setEvidenceState({
      status: "idle",
      projection: null,
      batches: [],
      detail: null,
      selectedBatchId: null,
      error: null,
    })
    setResearchLedger(null)
    setContinuationInput("{}")
    setContinuationKey("")
    setContinuationError(null)
    setRunInputError(null)
    setQuestionBankFile(null)
    if (questionBankInputRef.current) questionBankInputRef.current.value = ""
  }

  return (
    <aside
      className="flex max-h-[32rem] w-80 flex-col overflow-hidden rounded-lg border bg-sidebar/95 shadow-xl backdrop-blur-sm"
      aria-label="运行追踪"
    >
      <div className="border-b px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <SectionCaption>Backend Run</SectionCaption>
            <h2 className="mt-1 flex items-center gap-2 text-sm font-medium">
              <Activity className="size-3.5 text-muted-foreground" />
              <span>Run Trace</span>
            </h2>
            <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
              {workflowProject.id} · {nodeCount}N / {edgeCount}E
            </p>
          </div>
          <Badge variant={runState.status === "error" ? "destructive" : "outline"} className="font-mono uppercase">
            {projection ? RUN_STATUS_LABELS[projection.status] : runState.status}
          </Badge>
        </div>
        <div className="mt-3 grid grid-cols-[1fr_1fr_auto] gap-2">
          <Button ref={runButtonRef} size="sm" onClick={() => void runBackendWorkflow()} disabled={isRunning || isBackendRunning}>
            {isRunning ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run
          </Button>
          <Button size="sm" variant="outline" onClick={runBackendPreview} disabled={isRunning || isBackendRunning}>
            {isBackendRunning ? <Loader2 className="size-3.5 animate-spin" /> : <Activity className="size-3.5" />}
            Preview
          </Button>
          <Button
            size="icon-sm"
            variant="outline"
            onClick={resetRun}
            disabled={isRunning || isBackendRunning || (runState.status === "idle" && backendState.status === "idle" && !questionBankFile)}
          >
            <RotateCcw className="size-3.5" />
            <span className="sr-only">Reset run trace</span>
          </Button>
        </div>
        <details className="mt-3 rounded-md border bg-card/50 p-2.5" open={Boolean(runFileInput) || runInputTemplateText !== "{}"}>
          <summary className="flex cursor-pointer items-center gap-2 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            <FileInput className="size-3.5" />本次运行输入
          </summary>
          <div className="mt-2.5 space-y-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              仅属于本次 Run，不会写回工作流节点；新题包会创建新的批次快照。
            </p>
            {runFileInput ? (
              <>
                <input
                  ref={questionBankInputRef}
                  type="file"
                  disabled={isRunning}
                  accept={runFileInput.accept}
                  className="sr-only"
                  aria-label={`选择${runFileInput.title}`}
                  onChange={(event) => {
                    if (isRunning) {
                      event.target.value = ""
                      return
                    }
                    const file = event.target.files?.[0]
                    if (!file) return
                    if (!/\.(?:json|xls|xlsx)$/i.test(file.name)) {
                      setQuestionBankFile(null)
                      setRunInputError("请选择 JSON 或 Excel 题库文件")
                      event.target.value = ""
                      return
                    }
                    setQuestionBankFile(file)
                    setRunInputError(null)
                  }}
                />
                <button
                  type="button"
                  onClick={() => questionBankInputRef.current?.click()}
                  disabled={isRunning}
                  className="flex min-h-20 w-full flex-col items-center justify-center rounded-md border border-dashed px-3 py-2 text-center outline-none transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <FileInput className="size-5 text-muted-foreground" />
                  <span className="mt-1.5 max-w-full truncate text-xs font-medium">
                    {questionBankFile?.name ?? `选择${runFileInput.title}`}
                  </span>
                  <span className="mt-0.5 text-[10px] text-muted-foreground">
                    {questionBankFile
                      ? `${(questionBankFile.size / 1024).toFixed(1)} KiB · 点击重新选择`
                      : "题库文件（JSON / Excel） · 每次 Run 重新选择"}
                  </span>
                </button>
              </>
            ) : null}
            {runInputTemplateText !== "{}" ? (
              <Textarea
                value={runInputText}
                onChange={(event) => setRunInputText(event.target.value)}
                rows={5}
                className="font-mono text-[10px]"
                aria-label="本次运行参数 JSON"
              />
            ) : null}
            {runInputError ? <p className="text-[11px] text-destructive">{runInputError}</p> : null}
          </div>
        </details>
        {!runFileInput ? (
          <details className="mt-3 rounded-md border bg-card/50 p-2.5" open={Boolean(selectedSourceId)}>
            <summary className="flex cursor-pointer items-center gap-2 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
              <FileInput className="size-3.5" />导入节点输出
            </summary>
            <div className="mt-2.5 space-y-2">
              <p className="text-[11px] leading-relaxed text-muted-foreground">把真实或测试 JSON 输出注入一个源节点，再启动同一条原生运行链路；不会改写节点配置。</p>
              {outputInputNodes.length ? (
                <Select value={effectiveImportNodeId} onValueChange={(value) => setImportNodeId(value ?? "")}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue>
                      {(value: string | null) =>
                        value
                          ? (outputInputNodes.find((node) => node.id === value)?.label ?? value)
                          : "选择输入节点"
                      }
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {outputInputNodes.map((node) => (
                      <SelectItem key={node.id} value={node.id}>
                        {node.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
              <Textarea value={importOutputText} onChange={(event) => setImportOutputText(event.target.value)} rows={5} className="font-mono text-[10px]" aria-label="导入节点输出 JSON" />
              {importError ? <p className="text-[11px] text-destructive">{importError}</p> : null}
              <Button size="sm" variant="outline" className="w-full" onClick={() => void runImportedOutput()} disabled={!outputInputNodes.length || isRunning || isBackendRunning}>
                <FileInput className="size-3.5" />导入输出并运行
              </Button>
            </div>
          </details>
        ) : null}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4">
          {runState.error ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              {runState.error}
            </div>
          ) : null}
          {backendState.error ? (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
              {backendState.error}
            </div>
          ) : null}
          {projection ? (
            <>
              <RealRunProjection
                projection={projection}
                eventCount={runState.events.length}
                blockedCount={blockedCount}
                batchCount={batchCount}
                itemCount={itemCount}
              />
              {gaojixingRecoveryCase ? (
                <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
                  <p className="text-xs font-medium">需要完成页面验证</p>
                  <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                    {gaojixingRecoveryCase.artifactRef}
                  </p>
                  <Button
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => void resumeGaojixingRun()}
                    disabled={isResumingGaojixing || !workflowRunScope}
                  >
                    {isResumingGaojixing ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                    已完成验证，继续
                  </Button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="rounded-md border border-dashed p-4 text-center text-xs leading-relaxed text-muted-foreground">
              no backend run yet
            </div>
          )}

          {latestEvents.length > 0 ? (
            <>
              <Separator />
              <div className="space-y-2">
                <SectionCaption>SSE Events</SectionCaption>
                <div className="space-y-2">
                  {latestEvents.map((event) => <RunEventCard key={event.id} event={event} />)}
                </div>
              </div>
            </>
          ) : null}
          {dispatchRunPanelExtensions(extensions, {
            runId: projection?.runId ?? null,
            projection,
            scope,
            authorization: extensionAuthorization,
            runStatus: runState.status,
            lifecycle: {
              onError: (message) => setRunState((current) => ({
                status: "error",
                projection: current.projection,
                events: current.events,
                error: message,
              })),
            },
          }).map(({ key, Component }) => (
            <Component
              key={key}
              runId={projection?.runId ?? null}
              projection={projection}
              authorization={extensionAuthorization}
              scope={scope}
              runStatus={runState.status}
              lifecycle={{
                onError: (message) => setRunState((current) => ({
                  status: "error",
                  projection: current.projection,
                  events: current.events,
                  error: message,
                })),
              }}
            />
          ))}

          {researchLedger ? (
            <>
              <Separator />
              <ResearchLedgerWorkbench
                ledger={researchLedger}
                allowSourceOutputs={!runFileInput}
                sourceOutputs={continuationInput}
                onSourceOutputsChange={setContinuationInput}
                onContinue={continueResearchRun}
                continuing={isContinuing}
                error={continuationError}
              />
            </>
          ) : null}

          {evidenceState.status !== "idle" ? (
            <>
              <Separator />
              <EvidenceBatchWorkbench state={evidenceState} onSelectBatch={selectEvidenceBatch} />
            </>
          ) : null}

          {errors.length > 0 ? (
            <>
              <Separator />
              <RuntimeErrorList errors={errors} />
            </>
          ) : null}

          {backendState.compile || backendState.trace || backendState.native ? (
            <>
              <Separator />
              <BackendRuntimePreview
                status={backendState.status}
                compile={backendState.compile}
                trace={backendState.trace}
                native={backendState.native}
              />
            </>
          ) : null}
        </div>
      </ScrollArea>
    </aside>
  )
}

type OutputInputNode = { id: string; label: string }
function collectOutputInputNodes(project: WorkflowProject): OutputInputNode[] {
  return project.nodes.filter(canReceiveImportedOutput).map((node) => ({ id: node.id, label: nodeLabel(node) }))
}

function canReceiveImportedOutput(node: WorkflowProject["nodes"][number]): boolean {
  return node.kind === "source" || node.capability === "fetch" || Array.isArray(node.params.sources)
}


function nodeLabel(node: WorkflowProject["nodes"][number]): string {
  const label = node.ui?.label
  return typeof label === "string" && label.trim() ? label : node.id
}


function BackendRuntimePreview({
  status,
  compile,
  trace,
  native,
}: {
  status: BackendPreviewState["status"]
  compile: WorkflowCompileResponse | null
  trace: WorkflowOpenCLIHDATraceResponse | null
  native: NativeIntelligencePreviewEvidence | null
}) {
  const runtimeNodes = compile?.plan?.runtime.nodes ?? []
  const boundCount = runtimeNodes.filter((node) => Boolean(readRecord(node.runtime.binding))).length
  const missingParameterCount = runtimeNodes.filter((node) => {
    const missingRuntime = readRecord(node.runtime.missing_runtime)
    return missingRuntime?.code === "missing_runtime_parameter"
  }).length
  const dispatches = trace?.dispatches ?? []
  const errors = [...(compile?.errors ?? []), ...(trace?.errors ?? [])]
  const nativeBlockedCount = native?.status === "blocked"
    ? Math.max(native.blockedActions.length, 1)
    : 0
  const blockedCount = errors.length + missingParameterCount + nativeBlockedCount

  return (
    <div className="space-y-3">
      <MetricGrid
        title="Backend Preview"
        metrics={[
          {
            key: "status",
            label: "Status",
            value: status,
            tone: status === "ready" ? "good" : status === "idle" ? "neutral" : "warn",
          },
          {
            key: "nodes",
            label: "Runtime Nodes",
            value: `${runtimeNodes.length}`,
            tone: runtimeNodes.length > 0 ? "good" : "warn",
          },
          {
            key: "bound",
            label: "Bound",
            value: `${boundCount}`,
            tone: boundCount > 0 ? "good" : "neutral",
          },
          {
            key: "dispatches",
            label: "Dispatches",
            value: `${dispatches.length}`,
            tone: dispatches.length > 0 ? "good" : "neutral",
          },
          {
            key: "missing",
            label: "Blocked",
            value: `${blockedCount}`,
            tone: blockedCount === 0 ? "good" : "warn",
          },
          {
            key: "mode",
            label: "Mode",
            value: native ? "native-preview" : trace?.dispatch?.mode ?? compile?.plan?.runtime.execution_mode ?? "preview",
            tone: "neutral",
          },
        ]}
      />

      {native ? (
        <div className="rounded-md border bg-card p-3" aria-label="Native Intelligence Preview">
          <div className="flex items-center justify-between gap-2">
            <SectionCaption>Native Intelligence Preview</SectionCaption>
            <Badge variant={native.status === "ready" ? "secondary" : "outline"} className="font-mono text-[9px] uppercase">
              {native.status}
            </Badge>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] text-muted-foreground">
            <span>{native.actions.length}/{native.expectedActionCount} actions</span>
            <span className="text-right">{native.compiledNodeIds.length} compiled nodes</span>
            <span>readiness {native.readiness.status}</span>
            <span className="text-right">dispatch none · mutation none</span>
          </div>
          <div className="mt-3 max-h-52 space-y-1.5 overflow-auto">
            {native.actions.map((action) => (
              <div key={action.action} className="flex items-center justify-between gap-2 rounded-sm border bg-background px-2 py-1.5 font-mono text-[10px]">
                <span className="min-w-0 truncate text-foreground">{action.action}</span>
                <span className={action.status === "runnable" ? "text-[#4ade80]" : "text-[#d97706]"}>
                  {action.status}
                </span>
              </div>
            ))}
          </div>
          {native.missingReasons.length > 0 ? (
            <div className="mt-3 rounded-sm border border-[#d97706]/30 bg-[#d97706]/10 p-2 font-mono text-[9px] text-[#d97706]">
              {native.missingReasons.join(" · ")}
            </div>
          ) : null}
          <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
            Capability/readiness evidence only. Preview does not execute or mutate an intelligence session.
          </p>
        </div>
      ) : null}

      {trace ? (
        <div className="rounded-md border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <SectionCaption>OpenCLI HDA Trace</SectionCaption>
            <Badge variant={trace.valid ? "secondary" : "outline"} className="font-mono text-[9px] uppercase">
              {trace.valid ? "ready" : "blocked"}
            </Badge>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] text-muted-foreground">
            <span className="truncate">run {trace.runId}</span>
            <span className="truncate text-right">trace {trace.traceId}</span>
          </div>
          {dispatches.length > 0 ? (
            <div className="mt-3 space-y-1.5">
              {dispatches.slice(0, 6).map((dispatch) => (
                <div key={dispatch.taskId} className="rounded-sm border bg-background px-2 py-1.5 font-mono text-[10px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-foreground">{dispatch.nodeId}</span>
                    <span className="shrink-0 text-muted-foreground">{dispatch.sourceGroup}</span>
                  </div>
                  <p className="mt-1 truncate text-muted-foreground">
                    {dispatch.site} · {dispatch.command} · {dispatch.iii.function_id}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {errors.length > 0 ? <RuntimeErrorList errors={errors} /> : null}
    </div>
  )
}

function RuntimeErrorList({ errors }: { errors: Array<{ code: string; message: string; node_id?: string | null; edge_id?: string | null }> }) {
  return (
    <div className="space-y-1.5">
      <SectionCaption>Runtime Blocks</SectionCaption>
      {errors.slice(0, 5).map((error) => (
        <div key={`${error.code}-${error.node_id ?? error.edge_id ?? error.message}`} className="rounded-md border border-destructive/25 bg-destructive/10 p-2.5">
          <div className="flex items-center justify-between gap-2 font-mono text-[10px]">
            <span className="min-w-0 truncate text-destructive">{error.code}</span>
            <span className="shrink-0 text-muted-foreground">{error.node_id ?? error.edge_id ?? "workflow"}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-destructive/90">{error.message}</p>
        </div>
      ))}
    </div>
  )
}
