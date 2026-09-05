import { InteractionSettingsPanel } from "./interaction-settings-panel"
import { ProjectSettingsPanel } from "./project-settings-panel"
import { RunTracePanel } from "./run-trace-panel"
import { NodeManagementPanel } from "./node-management-panel"
import { cn } from "@/lib/utils"
import type { CanvasPoint } from "./workflow-canvas-geometry"
import type { WorkflowProfile } from "@/lib/workflow/schema"
import { MAX_WORKFLOW_NODE_DEPTH, workflowNodeDepthFromNetworkStack, workflowNodeLayerAtDepth } from "@/lib/workflow/node-hierarchy"
import type { RunPanelExtension, RunPanelScope } from "@/lib/workflow/run-panel-extensions"

type NetworkStackEntry = { nodeId: string; label: string }

export function ScissorTrailOverlay({ active, points }: { active: boolean; points: CanvasPoint[] }) {
  if (!active || points.length <= 1) return null
  const polylinePoints = points.map((point) => `${point.x},${point.y}`).join(" ")
  return (
    <svg className="pointer-events-none absolute inset-0 z-30 h-full w-full overflow-visible">
      <polyline
        points={polylinePoints}
        fill="none"
        stroke="var(--background)"
        strokeWidth={7}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.9}
      />
      <polyline
        points={polylinePoints}
        className="workflow-scissor-trail"
        fill="none"
        stroke="#ff7a17"
        strokeWidth={2}
        strokeDasharray="7 5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function NetworkBreadcrumb({
  locked,
  networkStack,
  onExit,
}: {
  locked: boolean
  networkStack: NetworkStackEntry[]
  onExit: () => void
}) {
  if (networkStack.length === 0) return null
  const currentDepth = workflowNodeDepthFromNetworkStack(networkStack.length)
  const currentLayer = workflowNodeLayerAtDepth(currentDepth)
  return (
    <div className="workflow-floating-panel absolute left-3 top-3 z-40 flex items-center gap-2 rounded-md border bg-popover px-2.5 py-2 font-mono text-[10px] uppercase tracking-[0.12em] shadow-lg">
      <button
        type="button"
        className="rounded-sm border border-border bg-background px-2 py-1 text-foreground transition-colors hover:bg-accent"
        onClick={onExit}
      >
        ← 上一层
      </button>
      <span className="text-muted-foreground/60">/</span>
      <span className="text-muted-foreground">obj</span>
      {networkStack.map((entry) => (
        <span key={entry.nodeId} className="flex items-center gap-1">
          <span className="text-muted-foreground/60">/</span>
          <span className="text-foreground">{entry.label}</span>
        </span>
      ))}
      <span className="rounded-sm border border-border bg-background px-1.5 py-0.5 text-foreground">
        L{currentDepth}/{MAX_WORKFLOW_NODE_DEPTH} · {currentLayer.label}
      </span>
      <span className={cn("rounded-sm border px-1.5 py-0.5", locked ? "border-[#d97706]/50 text-[#d97706]" : "border-[#2f9e44]/50 text-[#2f9e44]")}>
        {locked ? "LOCKED" : "DRAFT"}
      </span>
      <span className="ml-1 text-muted-foreground/60">Esc / Backspace</span>
    </div>
  )
}

export function WorkflowFloatingPanels({
  nodeManagementOpen,
  onCloseNodeManagement,
  onProfileChange,
  projectSettingsOpen,
  runTraceOpen,
  runRequestId,
  settingsOpen,
  workflowProfile,
  scope,
  runPanelExtensions = [],
}: {
  nodeManagementOpen: boolean
  onCloseNodeManagement: () => void
  onProfileChange: (profile: WorkflowProfile) => void
  projectSettingsOpen: boolean
  runTraceOpen: boolean
  runRequestId: number
  settingsOpen: boolean
  workflowProfile: WorkflowProfile
  runPanelExtensions?: readonly RunPanelExtension[]
  scope?: RunPanelScope | null
}) {
  return (
    <>
      {runTraceOpen ? (
        <div className={cn("workflow-floating-panel absolute top-3 z-40", nodeManagementOpen ? "left-[28.75rem]" : "left-3")}>
          <RunTracePanel runRequestId={runRequestId} extensions={runPanelExtensions} scope={scope} />
        </div>
      ) : null}

      {nodeManagementOpen ? <NodeManagementPanel onClose={onCloseNodeManagement} /> : null}

      {projectSettingsOpen ? (
        <div className="workflow-floating-panel absolute bottom-3 right-3 top-3 z-40">
          <ProjectSettingsPanel profile={workflowProfile} onProfileChange={onProfileChange} />
        </div>
      ) : settingsOpen ? (
        <div className="workflow-floating-panel contents">
          <InteractionSettingsPanel />
        </div>
      ) : null}
    </>
  )
}

export function WorkflowToast({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="workflow-toast pointer-events-none absolute bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md border bg-popover px-4 py-2 font-mono text-xs shadow-lg">
      {message}
    </div>
  )
}
