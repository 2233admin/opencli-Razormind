import { useEffect, type Dispatch, type SetStateAction } from "react"
import { useFlowStore } from "@/lib/flow/store"
import { useSettingsStore } from "@/lib/flow/settings-store"
import type { ToolMode } from "@/lib/flow/types"

type Point = { x: number; y: number }
type WritableRef<T> = { current: T }
type SetOpen = Dispatch<SetStateAction<boolean>>

type WorkflowKeyboardShortcutOptions = {
  autoLayout: (direction: "TB", engine: "elk", animated: boolean) => Promise<void>
  copy: () => void
  cut: () => void
  deleteSelected: () => void
  duplicate: () => void
  exitNodeNetwork: () => boolean
  fitView: (options?: { padding?: number; duration?: number }) => unknown
  groupSelection: () => void
  inspectorOpen: boolean
  mousePosRef: WritableRef<Point>
  paste: (position?: Point) => void
  projectSettingsOpen: boolean
  redo: () => void
  save: () => void
  screenToFlowPosition: (position: Point) => Point
  scissorCutRef: WritableRef<Set<string>>
  scissorDraggingRef: WritableRef<boolean>
  setInspectorOpen: SetOpen
  setPaletteOpen: SetOpen
  setProjectSettingsOpen: SetOpen
  setScissorTrail: Dispatch<SetStateAction<Point[]>>
  setSettingsOpen: SetOpen
  setToolMode: (mode: ToolMode) => void
  setMiniMapVisible: (visible: boolean) => void
  settingsOpen: boolean
  showToast: (message: string) => void
  undo: () => void
  yMomentaryModeRef: WritableRef<ToolMode | null>
}

function isEditableTarget(target: EventTarget | null) {
  const element = target as HTMLElement | null
  if (!element) return false
  return element.tagName === "INPUT" || element.tagName === "TEXTAREA" || element.isContentEditable
}

export function useWorkflowKeyboardShortcuts({
  autoLayout,
  copy,
  cut,
  deleteSelected,
  duplicate,
  exitNodeNetwork,
  fitView,
  groupSelection,
  inspectorOpen,
  mousePosRef,
  paste,
  projectSettingsOpen,
  redo,
  save,
  screenToFlowPosition,
  scissorCutRef,
  scissorDraggingRef,
  setInspectorOpen,
  setPaletteOpen,
  setProjectSettingsOpen,
  setScissorTrail,
  setSettingsOpen,
  setToolMode,
  setMiniMapVisible,
  settingsOpen,
  showToast,
  undo,
  yMomentaryModeRef,
}: WorkflowKeyboardShortcutOptions) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey
      const key = event.key.toLowerCase()

      if (mod && key === "k") {
        event.preventDefault()
        setPaletteOpen((open) => !open)
        return
      }
      if (mod && key === "s") {
        event.preventDefault()
        save()
        return
      }
      if (isEditableTarget(event.target)) return

      if (!mod && (event.key === "Tab" || key === "b")) {
        event.preventDefault()
        setPaletteOpen(true)
        return
      }

      if (mod && key === "z" && !event.shiftKey) {
        event.preventDefault()
        undo()
      } else if (mod && (key === "y" || (key === "z" && event.shiftKey))) {
        event.preventDefault()
        redo()
      } else if (mod && key === "c") {
        copy()
      } else if (mod && key === "v") {
        event.preventDefault()
        paste(screenToFlowPosition(mousePosRef.current))
      } else if (mod && key === "x") {
        cut()
      } else if (mod && key === "d") {
        event.preventDefault()
        duplicate()
      } else if (mod && key === "g") {
        event.preventDefault()
        groupSelection()
      } else if (!mod && key === "o") {
        event.preventDefault()
        const next = !useSettingsStore.getState().showMiniMap
        setMiniMapVisible(next)
        showToast(next ? "节点缩略图已显示" : "节点缩略图已隐藏")
      } else if (!mod && key === "p") {
        event.preventDefault()
        if (inspectorOpen) {
          setInspectorOpen(false)
          showToast("右侧工具架已隐藏")
          return
        }
        if (settingsOpen || projectSettingsOpen) {
          setSettingsOpen(false)
          setProjectSettingsOpen(false)
        }
        setInspectorOpen(true)
        showToast("右侧工具架已显示")
      } else if (!mod && key === "h") {
        event.preventDefault()
        if (useFlowStore.getState().nodes.length === 0) return
        void fitView({ padding: 0.24, duration: 220 })
        showToast("已显示全部节点")
      } else if (!mod && key === "l") {
        event.preventDefault()
        if (useFlowStore.getState().nodes.length === 0) return
        showToast("正在自动排布节点")
        void autoLayout("TB", "elk", true).then(() => {
          showToast("已自动排布整体节点")
          window.setTimeout(() => void fitView({ padding: 0.24, duration: 260 }), 30)
        })
      } else if (!mod && (event.key === "Escape" || event.key === "Backspace") && useFlowStore.getState().networkStack.length > 0) {
        event.preventDefault()
        if (exitNodeNetwork()) {
          showToast("已返回上一层 Network")
          window.setTimeout(() => void fitView({ padding: 0.24, duration: 180 }), 20)
        }
      } else if (!mod && key === "y") {
        event.preventDefault()
        if (event.repeat) return
        yMomentaryModeRef.current = useFlowStore.getState().toolMode
        setToolMode("scissors")
      } else if (event.key === "Delete" || event.key === "Backspace") {
        deleteSelected()
      }
    }

    const onKeyUp = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return
      if (event.metaKey || event.ctrlKey || event.key.toLowerCase() !== "y") return
      if (yMomentaryModeRef.current === null) return
      event.preventDefault()
      const restoreMode = yMomentaryModeRef.current
      yMomentaryModeRef.current = null
      scissorDraggingRef.current = false
      scissorCutRef.current = new Set()
      setScissorTrail([])
      setToolMode(restoreMode)
    }

    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("keyup", onKeyUp)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("keyup", onKeyUp)
    }
  }, [
    autoLayout,
    copy,
    cut,
    deleteSelected,
    duplicate,
    exitNodeNetwork,
    fitView,
    groupSelection,
    inspectorOpen,
    mousePosRef,
    paste,
    projectSettingsOpen,
    redo,
    save,
    screenToFlowPosition,
    scissorCutRef,
    scissorDraggingRef,
    setInspectorOpen,
    setPaletteOpen,
    setProjectSettingsOpen,
    setScissorTrail,
    setSettingsOpen,
    setToolMode,
    setMiniMapVisible,
    settingsOpen,
    showToast,
    undo,
    yMomentaryModeRef,
  ])
}
