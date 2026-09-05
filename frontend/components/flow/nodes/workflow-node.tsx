"use client"

import { memo, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react"
import type { WorkflowNode as WorkflowNodeType } from "@/lib/flow/types"
import { workflowNodeSize } from "@/lib/flow/node-geometry"
import { getIcon } from "@/lib/flow/icons"
import { useFlowStore } from "@/lib/flow/store"
import { useSettingsStore } from "@/lib/flow/settings-store"
import {
  getNodeDisplayId,
  localizeNodeText,
  shouldPreserveNodeAuthoredText,
  type WorkflowLanguage,
} from "@/lib/workflow/node-i18n"
import { getNodeVisualSignature } from "@/lib/workflow/node-visuals"
import { runtimeStatusLabel, runtimeStatusTone } from "@/lib/workflow/capabilities"
import { buildCanonicalNodeViewContract } from "@/lib/workflow/canonical-node-contract"
import { findWorkflowProjectNodeByCanvasId } from "@/lib/workflow/node-path"
import { businessNodeName } from "@/lib/workflow/business-node-experience"
import type { WorkflowCapability, WorkflowNodeKind } from "@/lib/workflow/schema"
import { cn } from "@/lib/utils"
import {
  WorkflowNodeHeader,
  WorkflowNodeInterface,
  WorkflowNodeInterfaceRow,
  WorkflowNodePortHandle,
  WorkflowNodeRoot,
  WorkflowNodeSummary,
  WorkflowNodeSurface,
  type WorkflowNodePort,
  type WorkflowNodeStatus,
} from "./workflow-node-primitives"

const PORT_TYPE_LABELS: Record<string, Record<WorkflowLanguage, string>> = {
  trigger: { "zh-CN": "触发信号", "en-US": "Trigger" },
  "items[]": { "zh-CN": "条目", "en-US": "Items" },
  "recordCandidate[]": { "zh-CN": "候选记录", "en-US": "Candidates" },
  "record[]": { "zh-CN": "记录", "en-US": "Records" },
  "runtimeArtifact[]": { "zh-CN": "运行产物", "en-US": "Artifacts" },
  "scoredItems[]": { "zh-CN": "评分条目", "en-US": "Scored items" },
  "summary[]": { "zh-CN": "摘要", "en-US": "Summaries" },
  branch: { "zh-CN": "分支", "en-US": "Branch" },
  delivery: { "zh-CN": "投递结果", "en-US": "Delivery" },
  "storedItems[]": { "zh-CN": "已存储条目", "en-US": "Stored items" },
}

const PORT_ID_LABELS: Record<string, Record<WorkflowLanguage, string>> = {
  candidates: { "zh-CN": "候选记录", "en-US": "Candidates" },
  records: { "zh-CN": "记录", "en-US": "Records" },
  review: { "zh-CN": "人工复核", "en-US": "Review" },
  notify: { "zh-CN": "通知", "en-US": "Notify" },
  delivery: { "zh-CN": "投递结果", "en-US": "Delivery" },
  stored: { "zh-CN": "已存储", "en-US": "Stored" },
  in1: { "zh-CN": "输入 1", "en-US": "Input 1" },
  in2: { "zh-CN": "输入 2", "en-US": "Input 2" },
}

type VisibleNodePort = WorkflowNodePort

function portDisplayLabel(port: { id?: string; direction: string; type: string }, language: WorkflowLanguage) {
  const explicit = port.id ? PORT_ID_LABELS[port.id]?.[language] : undefined
  if (explicit) return explicit
  const typeLabel = PORT_TYPE_LABELS[port.type]?.[language]
  if (typeLabel) return typeLabel
  if (port.id && port.id !== "in" && port.id !== "out") return port.id
  return port.direction === "input" ? language === "zh-CN" ? "输入" : "Input" : language === "zh-CN" ? "输出" : "Output"
}

function paramSummary(data: WorkflowNodeType["data"]): string | undefined {
  if (data.condition) return data.condition.slice(0, 96)
  if (data.fields?.length) {
    return data.fields
      .slice(0, 2)
      .map((field) => `${field.label}=${field.value.slice(0, 32)}`)
      .join(" · ")
  }
  return undefined
}

function typeCaption(category: string, nodeType: string) {
  return `${category}::${nodeType}`.toUpperCase()
}

function nodeStatus(status: string | undefined): WorkflowNodeStatus {
  switch (status) {
    case "running": case "waiting": case "success": case "partial_success": case "error": return status
    default: return "idle"
  }
}

function mergeNodePorts(
  declared: VisibleNodePort[],
  connectedIds: Array<string | undefined>,
  direction: "input" | "output",
  language: WorkflowLanguage,
): VisibleNodePort[] {
  const ports = new Map(declared.map((port) => [portKey(port.id), port]))
  for (const id of connectedIds) {
    if (id === undefined && declared.length) continue
    const key = portKey(id)
    if (!ports.has(key)) ports.set(key, { id, label: portDisplayLabel({ id, direction, type: "unknown" }, language), type: "unknown" })
  }
  return Array.from(ports.values())
}

function semanticFallbackPorts(kind: string | undefined, language: WorkflowLanguage): { inputs: VisibleNodePort[]; outputs: VisibleNodePort[] } {
  const visible = (id: string, direction: "input" | "output", type: string): VisibleNodePort => ({ id, label: portDisplayLabel({ id, direction, type }, language), type })
  switch (kind) {
    case "schedule": return { inputs: [], outputs: [visible("out", "output", "trigger")] }
    case "source": return { inputs: [visible("in", "input", "trigger")], outputs: [visible("out", "output", "items[]")] }
    case "sink": return { inputs: [visible("records", "input", "record[]")], outputs: [visible("stored", "output", "storedItems[]")] }
    default: return { inputs: [], outputs: [] }
  }
}

function portKey(id: string | undefined, prefix = "port") { return `${prefix}:${id ?? "__default__"}` }
type WorkflowNodeProps = {
  id: string
  data: WorkflowNodeType["data"]
  selected: boolean
}


function WorkflowNodeComponent({ id, data, selected }: WorkflowNodeProps) {
  const workflowProject = useFlowStore((state) => state.workflowProject)
  const networkStackLength = useFlowStore((state) => state.networkStack.length)
  const language = useSettingsStore((state) => state.language)
  const projectNode = findWorkflowProjectNodeByCanvasId(workflowProject, id)
  const contract = buildCanonicalNodeViewContract(projectNode, data, id)
  const canonical = data.canonical as { kind?: string; capability?: string; params?: Record<string, unknown> } | undefined
  const displayId = getNodeDisplayId(data)
  const prefersCustomLabel = projectNode?.ui?.preferCustomLabel === true || shouldPreserveNodeAuthoredText(data)
  const localized = prefersCustomLabel
    ? { label: data.label, description: data.description }
    : localizeNodeText(displayId, { label: data.label, description: data.description }, language)
  const title = networkStackLength === 0 && !prefersCustomLabel
    ? businessNodeName({ label: localized.label, kind: contract.identity.kind as WorkflowNodeKind, capability: contract.identity.capability as WorkflowCapability, params: canonical?.params, language })
    : localized.label
  const declared = contract.ports.length ? contract.ports : Array.isArray(data.primitivePorts) ? data.primitivePorts as Array<{ id: string; direction: string; type: string }> : []
  const semantic = semanticFallbackPorts(canonical?.kind, language)
  const declaredInputs = declared
    .filter((port) => port.direction === "input")
    .map((port) => ({ id: port.id, label: portDisplayLabel(port, language), type: port.type }))
  const declaredOutputs = declared
    .filter((port) => port.direction === "output")
    .map((port) => ({ id: port.id, label: portDisplayLabel(port, language), type: port.type }))
  const resolvedInputs = mergeNodePorts(
    declaredInputs.length ? declaredInputs : semantic.inputs,
    workflowProject.edges.filter((edge) => edge.target === id).map((edge) => edge.targetPort),
    "input",
    language,
  )
  const resolvedOutputs = mergeNodePorts(
    declaredOutputs.length ? declaredOutputs : semantic.outputs,
    workflowProject.edges.filter((edge) => edge.source === id).map((edge) => edge.sourcePort),
    "output",
    language,
  )
  const interfaceRows = [...resolvedInputs.map((port) => ({ direction: "IN" as const, port })), ...resolvedOutputs.map((port) => ({ direction: "OUT" as const, port }))]
  const portSignature = interfaceRows.map(({ direction, port }) => `${direction}:${port.id ?? "__default__"}`).join("|")

  const openPortMenu = (
    event: MouseEvent<HTMLDivElement> | KeyboardEvent<HTMLDivElement>,
    port: VisibleNodePort,
    handleType: "source" | "target",
    point: { x: number; y: number },
  ) => {
    event.preventDefault()
    event.stopPropagation()
    window.dispatchEvent(new CustomEvent("opencli:workflow-port-menu", {
      detail: { nodeId: id, handleId: port.id ?? null, handleType, label: port.label, type: port.type ?? "unknown", ...point },
    }))
  }
  const visual = getNodeVisualSignature(data)
  const Icon = getIcon(data.icon)
  const status = nodeStatus(data.runtimeRunState?.status === "waiting" ? "waiting" : data.status)
  const packageState = data.internalLocked ? "locked" : data.internalDraft ? "draft" : "canonical"
  const summary = paramSummary(data)

  const capability = data.runtimeCapability
    ? <span className={cn("workflow-node-capability", runtimeStatusTone(data.runtimeCapability.status))}>{runtimeStatusLabel(data.runtimeCapability.status)}</span>
    : null
  const stateLabel = [status, capability ? runtimeStatusLabel(data.runtimeCapability!.status) : null, data.internalLocked ? "locked" : null]
    .filter(Boolean)
    .join(", ")

  return (
    <WorkflowNodeRoot
      nodeId={id}
      selected={selected}
      status={status}
      packageState={packageState}
      label={`${title}, ${contract.identity.kind}, ${stateLabel}`}
      style={{ ...workflowNodeSize(interfaceRows.length), "--node-accent": visual.stripe } as CSSProperties}
    >
      <WorkflowNodeSurface>
        <WorkflowNodeHeader
          icon={Icon}
          accent={visual.stripe}
          eyebrow={typeCaption(data.category, canonical?.capability ?? data.nodeType)}
          title={title}
          status={status}
          locked={data.internalLocked === true}
          summary={summary ? <WorkflowNodeSummary>{summary}</WorkflowNodeSummary> : null}
          capability={capability}
        />
        <WorkflowNodeInterface nodeId={id} portSignature={portSignature}>
          {interfaceRows.map(({ direction, port }) => <WorkflowNodeInterfaceRow key={`${direction}-${portKey(port.id)}`} direction={direction} id={port.id ?? "default"} label={port.label} type={port.type} />)}
        </WorkflowNodeInterface>
      </WorkflowNodeSurface>
      {resolvedInputs.map((port, index) => (
        <WorkflowNodePortHandle
          key={portKey(port.id, "in")}
          port={port}
          direction="input"
          directionLabel={language === "zh-CN" ? "输入" : "Input"}
          nodeTitle={title}
          index={index}
          count={resolvedInputs.length}
          onOpenMenu={openPortMenu}
        />
      ))}
      {resolvedOutputs.map((port, index) => (
        <WorkflowNodePortHandle
          key={portKey(port.id, "out")}
          port={port}
          direction="output"
          directionLabel={language === "zh-CN" ? "输出" : "Output"}
          nodeTitle={title}
          index={index}
          count={resolvedOutputs.length}
          onOpenMenu={openPortMenu}
        />
      ))}
    </WorkflowNodeRoot>
  )
}

export default memo(WorkflowNodeComponent)
