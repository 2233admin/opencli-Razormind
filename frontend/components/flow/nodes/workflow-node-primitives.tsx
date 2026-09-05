import { useEffect, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode } from "react"
import { Handle, Position, useUpdateNodeInternals } from "@xyflow/react"
import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { WORKFLOW_NODE_GEOMETRY } from "@/lib/flow/node-geometry"

export type WorkflowNodeStatus = "idle" | "running" | "waiting" | "success" | "partial_success" | "error"
export type WorkflowNodePort = { id?: string; label: string; type?: string }
export type WorkflowNodeHandleType = "source" | "target"

const STATUS_LABELS: Record<WorkflowNodeStatus, string> = {
  idle: "Idle",
  running: "Running",
  waiting: "Waiting",
  success: "Done",
  partial_success: "Partial success",
  error: "Error",
}

const STATUS_TONES: Record<WorkflowNodeStatus, string> = {
  idle: "border-muted-foreground/70 bg-transparent",
  running: "border-primary bg-primary",
  waiting: "border-warning bg-warning",
  success: "border-success bg-success",
  partial_success: "border-warning bg-warning",
  error: "border-destructive bg-destructive",
}

type RootProps = {
  children: ReactNode
  selected: boolean
  status: WorkflowNodeStatus
  packageState: "canonical" | "draft" | "locked"
  style?: CSSProperties
  label: string
  nodeId: string
}

export function WorkflowNodeRoot({ children, nodeId, selected, status, packageState, style, label }: RootProps) {
  return (
    <div
      data-workflow-node="true"
      data-status={status}
      data-node-id={nodeId}
      data-selected={selected ? "true" : "false"}
      data-package-state={packageState}
      aria-label={label}
      className="workflow-node-root relative"
      style={{ ...style, width: WORKFLOW_NODE_GEOMETRY.width }}
    >
      {children}
    </div>
  )
}

export function WorkflowNodeSurface({ children }: { children: ReactNode }) {
  return <div className="workflow-node-surface h-full overflow-hidden bg-card text-card-foreground">{children}</div>
}

export function WorkflowNodeHeader({
  icon: Icon,
  accent,
  eyebrow,
  title,
  status,
  locked,
  summary,
  capability,
}: {
  icon: LucideIcon
  accent: string
  eyebrow: string
  title: string
  status: WorkflowNodeStatus
  locked: boolean
  summary?: ReactNode
  capability?: ReactNode
}) {
  return (
    <header className="workflow-node-header flex h-[72px] min-w-0 items-center gap-2 px-3">
      <span className="workflow-node-icon flex size-8 shrink-0 items-center justify-center rounded-sm border" style={{ borderColor: accent }} aria-hidden>
        <Icon className="size-4" strokeWidth={1.8} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="workflow-node-eyebrow truncate">{eyebrow}</span>
          {capability}
        </div>
        <p className="workflow-node-title truncate">{title}</p>
        <div className="workflow-node-summary-slot">{summary}</div>
      </div>
      <WorkflowNodeStatusMark status={status} />
      {locked ? <span className="workflow-node-lock shrink-0" aria-label="Locked">LOCK</span> : null}
    </header>
  )
}

export function WorkflowNodeStatusMark({ status }: { status: WorkflowNodeStatus }) {
  const label = STATUS_LABELS[status]
  return (
    <span className="workflow-node-status" title={`Status: ${label}`} aria-label={`Status: ${label}`}>
      <span className={cn("size-1.5 rounded-full border", STATUS_TONES[status])} />
      <span className="workflow-node-status-label">{label}</span>
    </span>
  )
}

export function WorkflowNodeSummary({ children }: { children?: ReactNode }) {
  return <p className="workflow-node-summary line-clamp-2">{children}</p>
}

export function WorkflowNodeInterface({
  children,
  nodeId,
  portSignature,
}: {
  children: ReactNode
  nodeId: string
  portSignature: string
}) {
  const updateNodeInternals = useUpdateNodeInternals()

  useEffect(() => {
    updateNodeInternals(nodeId)
  }, [nodeId, portSignature, updateNodeInternals])

  return <div className="workflow-node-interface" style={{ minHeight: WORKFLOW_NODE_GEOMETRY.interfaceRowHeight }}>{children}</div>
}

export function WorkflowNodeInterfaceRow({ direction, id, type, label }: { direction: "IN" | "OUT"; id: string; type?: string; label: string }) {
  return (
    <div className="workflow-node-interface-row" title={`${direction} · ${id}: ${type ?? "unknown"}`}>
      <span className="workflow-node-interface-direction">{direction}</span>
      <span className="workflow-node-interface-id truncate">{id}</span>
      <span className="workflow-node-interface-label truncate">{label}</span>
      <span className="workflow-node-interface-type">[{type ?? "unknown"}]</span>
    </div>
  )
}

type WorkflowNodePortHandleProps = {
  port: WorkflowNodePort
  direction: "input" | "output"
  directionLabel: string
  nodeTitle: string
  index: number
  count: number
  onOpenMenu: (
    event: MouseEvent<HTMLDivElement> | KeyboardEvent<HTMLDivElement>,
    port: WorkflowNodePort,
    handleType: WorkflowNodeHandleType,
    point: { x: number; y: number },
  ) => void
}

export function WorkflowNodePortHandle({
  port,
  direction,
  directionLabel,
  nodeTitle,
  index,
  count,
  onOpenMenu,
}: WorkflowNodePortHandleProps) {
  const handleType = direction === "output" ? "source" : "target"
  const position = direction === "output" ? Position.Bottom : Position.Top
  const left = count === 1 ? "50%" : `${((index + 1) / (count + 1)) * 100}%`
  const id = port.id ?? "default"
  const type = port.type ?? "unknown"
  const openAtTarget = (event: MouseEvent<HTMLDivElement> | KeyboardEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect()
    onOpenMenu(event, port, handleType, { x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 })
  }

  return (
    <div
      className={cn("workflow-port-anchor", direction === "output" ? "workflow-port-anchor-output" : "workflow-port-anchor-input")}
      style={{ left }}
    >
      <span className="workflow-port-name">{direction === "output" ? "OUT" : "IN"} · {id} · {port.label} [{type}]</span>
      <Handle
        type={handleType}
        id={port.id}
        position={position}
        role="button"
        aria-haspopup="menu"
        aria-keyshortcuts="Enter Space Shift+F10 ContextMenu"
        aria-label={`${nodeTitle} · ${directionLabel} · ${id} · ${port.label} · ${type}`}
        data-port-direction={direction}
        data-port-id={id}
        data-port-name={port.label}
        data-port-type={type}
        tabIndex={0}
        className="workflow-port-handle"
        onClickCapture={(event) => {
          if (!event.altKey) return
          onOpenMenu(event, port, handleType, { x: event.clientX, y: event.clientY })
        }}
        onContextMenu={(event) => onOpenMenu(event, port, handleType, { x: event.clientX, y: event.clientY })}
        onKeyDown={(event) => {
          const opensMenu = event.key === "ContextMenu" || (event.shiftKey && event.key === "F10") || event.key === "Enter" || event.key === " "
          if (!opensMenu) return
          openAtTarget(event)
        }}
      />
    </div>
  )
}
