"use client"

import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react"
import { LocateFixed, Pin, X } from "lucide-react"
import { cn } from "@/lib/utils"

export const workflowStatusText: Record<string, string> = {
  idle: "Idle",
  running: "Running",
  waiting: "Waiting",
  success: "Done",
  partial_success: "Partial success",
  error: "Error",
}

export const workflowStatusDotClass: Record<string, string> = {
  idle: "border-muted-foreground/50 bg-transparent",
  running: "border-info bg-info",
  waiting: "border-violet-400 bg-violet-400",
  success: "border-success bg-success",
  partial_success: "border-warning bg-warning",
  error: "border-destructive bg-destructive",
}

const MIN_DOCK_WIDTH = 320
const MAX_DOCK_WIDTH = 560

function clampDockWidth(width: number) {
  return Math.min(MAX_DOCK_WIDTH, Math.max(MIN_DOCK_WIDTH, width))
}

function splitTypeLine(typeLine: string) {
  const [kind = typeLine, version = ""] = typeLine.split("·").map((part) => part.trim())
  return { kind, version }
}

export function SectionCaption({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-3xs uppercase tracking-[0.2em] text-muted-foreground">
      {children}
    </p>
  )
}

export function MonoRow({ k, v }: { k: string; v: string | number }) {
  return (
    <div className="flex items-center justify-between gap-2 font-mono text-2xs">
      <span className="text-muted-foreground">{k}</span>
      <span className="truncate text-foreground">{v}</span>
    </div>
  )
}

function PanelStatus({ status }: { status?: string }) {
  if (!status) return null
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 text-muted-foreground"
      title={`Status: ${workflowStatusText[status] ?? status}`}
    >
      <span className={cn("size-1.5 rounded-full border", workflowStatusDotClass[status] ?? workflowStatusDotClass.idle)} />
      <span>{workflowStatusText[status] ?? status}</span>
    </span>
  )
}

export function PanelShell({
  title,
  typeLine,
  status,
  onClose,
  onLocate,
  compact = false,
  pinned = false,
  onTogglePin,
  children,
}: {
  title: string
  typeLine: string
  status?: string
  onClose: () => void
  onLocate?: () => void
  compact?: boolean
  pinned?: boolean
  onTogglePin?: () => void
  children: ReactNode
}) {
  const { kind, version } = splitTypeLine(typeLine)
  const [width, setWidth] = useState(380)
  const resizeStart = useRef<{ pointerX: number; width: number } | null>(null)

  const resizeByKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
    event.preventDefault()
    setWidth((current) => clampDockWidth(current + (event.key === "ArrowLeft" ? 16 : -16)))
  }

  const startResize = (event: PointerEvent<HTMLDivElement>) => {
    resizeStart.current = { pointerX: event.clientX, width }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const continueResize = (event: PointerEvent<HTMLDivElement>) => {
    if (!resizeStart.current) return
    setWidth(clampDockWidth(resizeStart.current.width + resizeStart.current.pointerX - event.clientX))
  }

  return (
    <aside
      data-health="inspector"
      data-dock-mode={compact ? "overlay" : "shared"}
      style={compact ? undefined : { width }}
      className={cn(
        "flex shrink-0 flex-col overflow-hidden border border-ops-line bg-ops-panel text-zinc-100 duration-150 animate-in fade-in slide-in-from-right-4",
        compact
          ? "fixed bottom-3 right-3 top-3 z-50 w-[min(380px,calc(100vw-1.5rem))] rounded-md"
          : "relative z-40 h-full rounded-md",
      )}
      aria-label="工作流右侧工具架"
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      {!compact ? (
        <div
          role="separator"
          aria-label="调整右侧工具架宽度"
          aria-orientation="vertical"
          aria-valuemin={MIN_DOCK_WIDTH}
          aria-valuemax={MAX_DOCK_WIDTH}
          aria-valuenow={width}
          tabIndex={0}
          className="absolute inset-y-0 left-0 z-10 w-1 -translate-x-1/2 cursor-col-resize bg-transparent focus-visible:bg-primary"
          onKeyDown={resizeByKeyboard}
          onPointerDown={startResize}
          onPointerMove={continueResize}
          onPointerUp={() => {
            resizeStart.current = null
          }}
        />
      ) : null}
      <div className="border-b border-ops-line bg-ops-raised px-3 py-2">
        <div className="grid grid-cols-[120px_minmax(0,1fr)_auto_auto] items-center gap-2">
          <span
            className="flex h-7 min-w-0 items-center truncate rounded-xs border border-ops-line bg-ops-panel px-2 font-mono text-3xs uppercase tracking-[0.08em] text-zinc-500"
            title={kind}
          >
            {kind}
          </span>
          <div className="flex h-7 min-w-0 items-center rounded-xs border border-ops-line bg-ops-black px-2">
            <h2 className="truncate font-mono text-xs font-semibold text-zinc-100">{title}</h2>
          </div>
          <PanelStatus status={status} />
          <div className="flex items-center gap-1">
            {onTogglePin ? (
              <button
                type="button"
                onClick={onTogglePin}
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-xs text-zinc-500 transition-colors hover:bg-ops-panel hover:text-zinc-100",
                  pinned && "bg-primary/15 text-primary",
                )}
                aria-pressed={pinned}
                aria-label={pinned ? "取消固定当前节点" : "固定当前节点"}
                title={pinned ? "取消固定当前节点" : "固定当前节点"}
              >
                <Pin className="size-3.5" />
              </button>
            ) : null}
            {onLocate ? (
              <button
                type="button"
                onClick={onLocate}
                className="flex size-5 shrink-0 items-center justify-center rounded-xs text-zinc-500 transition-colors hover:bg-ops-panel hover:text-zinc-100"
                aria-label="定位到当前节点"
                title="定位到当前节点"
              >
                <LocateFixed className="size-3.5" />
              </button>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="flex size-5 shrink-0 items-center justify-center rounded-xs text-zinc-500 transition-colors hover:bg-ops-panel hover:text-zinc-100"
              aria-label="关闭右侧工具架"
            >
              <X className="size-3.5" />
            </button>
          </div>
        </div>
        <div className="mt-1 grid h-4 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 pl-32 font-mono text-3xs uppercase tracking-[0.18em] text-zinc-500">
          <span className="whitespace-nowrap">Workflow Dock</span>
          <span className="truncate normal-case tracking-normal">obj / {title}</span>
          {version ? <span className="shrink-0 tracking-[0.08em]">{version}</span> : null}
        </div>
      </div>
      <div
        data-inspector-scroll
        className="workflow-inspector-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain"
      >
        {children}
      </div>
    </aside>
  )
}
