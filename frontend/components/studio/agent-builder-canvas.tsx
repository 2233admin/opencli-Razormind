'use client'

import {
  addEdge,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react'
import { AlertTriangle, Check, CircleDot, GitBranch, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { GeneratedWorkflowSpec } from '@/lib/flow/types'

import '@xyflow/react/dist/style.css'

type BuilderNodeData = {
  description: string
  label: string
  readiness: 'ready' | 'gap'
  recentStatus: string
  type: string
}

type BuilderNode = Node<BuilderNodeData, 'builder'>
type BuilderEdge = Edge<{ mapping: string }, 'smoothstep'>
type BuilderConnection = { source?: string | null; target?: string | null }

type AgentBuilderCanvasProps = {
  gapNodeIds?: string[]
  onManualEdit: (summary: string) => void
  onSpecChange: (spec: GeneratedWorkflowSpec) => void
  resolvedDeliveryEmail?: string
  spec: GeneratedWorkflowSpec
}

function displayType(type: string) {
  return type.replaceAll('_', ' ').replaceAll('-', ' ').toUpperCase()
}

function BuilderNodeCard({ data, selected }: NodeProps<BuilderNode>) {
  return (
    <article
      className={`w-56 rounded-md border bg-background px-3.5 py-3 shadow-sm transition-shadow ${selected ? 'border-foreground/50 shadow-md' : ''}`}
    >
      <Handle type="target" position={Position.Left} className="!size-2.5 !border-background !bg-foreground/55" />
      <div className="flex items-start gap-2.5">
        <div className="grid size-8 shrink-0 place-items-center rounded-sm bg-muted">
          <GitBranch className="size-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold" title={data.label}>{data.label}</div>
          <div className="mt-0.5 truncate font-mono text-3xs text-muted-foreground">{displayType(data.type)}</div>
        </div>
      </div>
      <p className="mt-3 line-clamp-2 text-3xs leading-4 text-muted-foreground">{data.description}</p>
      <div className="mt-3 flex items-center justify-between gap-2 border-t pt-2 font-mono text-[9px]">
        <span className={`inline-flex items-center gap-1 ${data.readiness === 'ready' ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
          {data.readiness === 'ready' ? <Check className="size-2.5" /> : <AlertTriangle className="size-2.5" />}
          {data.readiness === 'ready' ? 'READY' : 'NEEDS CONFIG'}
        </span>
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <CircleDot className="size-2.5" />最近状态 · {data.recentStatus === 'idle' ? '未运行' : data.recentStatus}
        </span>
      </div>
      <Handle type="source" position={Position.Right} className="!size-2.5 !border-background !bg-foreground/55" />
    </article>
  )
}

const NODE_TYPES = { builder: BuilderNodeCard }

function layoutFor(index: number, compact: boolean) {
  if (compact) return { x: 42, y: 28 + index * 156 }
  const column = index % 3
  const row = Math.floor(index / 3)
  return { x: 80 + column * 310, y: 80 + row * 190 }
}

function savedPosition(node: GeneratedWorkflowSpec['nodes'][number]) {
  const candidate = node.params?.builderPosition
  if (!candidate || typeof candidate !== 'object') return null
  const position = candidate as { x?: unknown; y?: unknown }
  return typeof position.x === 'number' && typeof position.y === 'number' ? { x: position.x, y: position.y } : null
}

function wouldCreateCycle(edges: GeneratedWorkflowSpec['edges'], connection: BuilderConnection) {
  if (!connection.source || !connection.target || connection.source === connection.target) return true
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
  const pending = [connection.target]
  const visited = new Set<string>()
  while (pending.length) {
    const current = pending.pop()!
    if (current === connection.source) return true
    if (visited.has(current)) continue
    visited.add(current)
    pending.push(...(adjacency.get(current) ?? []))
  }
  return false
}

function violatesExplicitMerge(spec: GeneratedWorkflowSpec, connection: BuilderConnection) {
  if (!connection.target) return true
  const incomingCount = spec.edges.filter((edge) => edge.target === connection.target).length
  if (incomingCount === 0) return false
  const target = spec.nodes.find((node) => node.id === connection.target)
  return !target || !/(?:^|[-_\s])merge(?:$|[-_\s])|合并/i.test(`${target.type} ${target.label}`)
}

function connectionAllowed(spec: GeneratedWorkflowSpec, connection: BuilderConnection) {
  return !wouldCreateCycle(spec.edges, connection) && !violatesExplicitMerge(spec, connection)
}

export function AgentBuilderCanvas({ gapNodeIds = [], onManualEdit, onSpecChange, resolvedDeliveryEmail, spec }: AgentBuilderCanvasProps) {
  const gapSet = useMemo(() => new Set(gapNodeIds), [gapNodeIds])
  const [nodes, setNodes] = useState<BuilderNode[]>([])
  const [edges, setEdges] = useState<BuilderEdge[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [compactLayout, setCompactLayout] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches)
  const canvasRef = useRef<HTMLDivElement>(null)
  const flowRef = useRef<ReactFlowInstance<BuilderNode, BuilderEdge> | null>(null)
  const wasVisibleRef = useRef(false)
  const lastCompactLayoutRef = useRef(compactLayout)

  useEffect(() => {
    const element = canvasRef.current
    if (!element) return
    const observer = new ResizeObserver(() => {
      const visible = element.clientWidth > 0 && element.clientHeight > 0
      if (!visible) {
        wasVisibleRef.current = false
        return
      }
      if (!wasVisibleRef.current && flowRef.current) {
        wasVisibleRef.current = true
        requestAnimationFrame(() => flowRef.current?.fitView({ padding: 0.22, duration: 0 }))
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 639px)')
    const update = () => setCompactLayout(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    setNodes((current) => {
      const positions = new Map(current.map((node) => [node.id, node.position]))
      const layoutChanged = lastCompactLayoutRef.current !== compactLayout
      lastCompactLayoutRef.current = compactLayout
      return spec.nodes.map((node, index) => ({
        id: node.id,
        type: 'builder',
        position: savedPosition(node) ?? (layoutChanged ? undefined : positions.get(node.id)) ?? layoutFor(index, compactLayout),
        data: {
          label: node.label,
          description: node.type === 'email-output' && resolvedDeliveryEmail
            ? `发送到 ${resolvedDeliveryEmail}`
            : node.description,
          type: node.type,
          readiness: gapSet.has(node.id) ? 'gap' : 'ready',
          recentStatus: node.recentStatus ?? 'idle',
        },
      }))
    })
    setEdges(spec.edges.map((edge, index) => ({
      id: `builder-edge-${index}-${edge.source}-${edge.target}`,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      label: edge.label || (edge.mapping?.mode === 'override' ? '手工映射' : '自动映射'),
      data: {
        mapping: edge.mapping?.fields.length
          ? edge.mapping.fields.map((field) => `${field.source} → ${field.target}`).join(', ')
          : edge.label || 'data.* → data.*',
      },
      style: { strokeWidth: 1.5 },
    })))
  }, [compactLayout, gapSet, resolvedDeliveryEmail, spec])

  useEffect(() => {
    if (!flowRef.current || !canvasRef.current || canvasRef.current.clientWidth === 0) return
    const frame = requestAnimationFrame(() => flowRef.current?.fitView({ padding: 0.16, duration: 0 }))
    return () => cancelAnimationFrame(frame)
  }, [compactLayout, nodes.length])

  const selectedNode = spec.nodes.find((node) => node.id === selectedNodeId)
  const selectedEdgeIndex = selectedEdgeId ? edges.findIndex((edge) => edge.id === selectedEdgeId) : -1
  const selectedSpecEdge = selectedEdgeIndex >= 0 ? spec.edges[selectedEdgeIndex] : undefined

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || !connectionAllowed(spec, connection)) return
    const nextEdge = {
      source: connection.source,
      target: connection.target,
      label: '自动映射',
      mapping: { mode: 'auto' as const, fields: [], preserveRaw: true as const, compatible: true, conflicts: [] },
    }
    onSpecChange({ ...spec, edges: [...spec.edges, nextEdge] })
    setEdges((current) => addEdge({ ...connection, type: 'smoothstep', label: '自动映射' }, current) as BuilderEdge[])
    onManualEdit('新增节点连接')
  }, [onManualEdit, onSpecChange, spec])

  function updateNodeLabel(label: string) {
    if (!selectedNode) return
    onSpecChange({
      ...spec,
      nodes: spec.nodes.map((node) => (node.id === selectedNode.id ? { ...node, label } : node)),
    })
    onManualEdit(`修改节点「${selectedNode.label}」`)
  }

  function deleteSelectedNode() {
    if (!selectedNode) return
    onSpecChange({
      ...spec,
      nodes: spec.nodes.filter((node) => node.id !== selectedNode.id),
      edges: spec.edges.filter((edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id),
    })
    setSelectedNodeId(null)
    onManualEdit(`删除节点「${selectedNode.label}」`)
  }

  function updateNodePosition(nodeId: string, position: { x: number; y: number }, label: string) {
    onSpecChange({
      ...spec,
      nodes: spec.nodes.map((node) => (node.id === nodeId
        ? { ...node, params: { ...(node.params ?? {}), builderPosition: position } }
        : node)),
    })
    onManualEdit(`移动节点「${label}」`)
  }

  function updateMapping(mapping: string) {
    if (!selectedSpecEdge || selectedEdgeIndex < 0) return
    const [source = 'data.*', target = 'data.*'] = mapping.split(/\s*(?:→|->)\s*/, 2)
    onSpecChange({
      ...spec,
      edges: spec.edges.map((edge, index) => (index === selectedEdgeIndex
        ? {
            ...edge,
            label: '手工映射',
            mapping: {
              mode: 'override',
              fields: [{ source: source.trim(), target: target.trim() }],
              preserveRaw: true,
              compatible: true,
              conflicts: [],
            },
          }
        : edge)),
    })
    onManualEdit('覆盖字段映射')
  }

  return (
    <div className="grid min-h-[520px] min-w-0 bg-muted/10 lg:grid-cols-[minmax(0,1fr)_250px]">
      <div ref={canvasRef} className="relative min-h-[440px] min-w-0" aria-label="可编辑 Workflow Draft 画布">
        <ReactFlow<BuilderNode, BuilderEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.22 }}
          minZoom={0.75}
          maxZoom={1.6}
          onInit={(instance) => {
            flowRef.current = instance
            if (canvasRef.current && canvasRef.current.clientWidth > 0) {
              wasVisibleRef.current = true
              requestAnimationFrame(() => instance.fitView({ padding: 0.22, duration: 0 }))
            }
          }}
          deleteKeyCode={null}
          isValidConnection={(connection) => connectionAllowed(spec, connection)}
          onConnect={onConnect}
          onNodesChange={(changes) => setNodes((current) => applyNodeChanges(changes, current))}
          onNodeClick={(_, node) => {
            setSelectedNodeId(node.id)
            setSelectedEdgeId(null)
          }}
          onNodeDragStop={(_, node) => updateNodePosition(node.id, node.position, node.data.label)}
          onEdgeClick={(_, edge) => {
            setSelectedEdgeId(edge.id)
            setSelectedNodeId(null)
          }}
          onPaneClick={() => {
            setSelectedNodeId(null)
            setSelectedEdgeId(null)
          }}
        >
          <Background gap={22} size={1} />
          <Controls showInteractive={false} className="!border-border !bg-background/90 !shadow-sm [&_button]:!border-border [&_button]:!bg-background [&_button_svg]:!fill-foreground" />
          <MiniMap pannable zoomable nodeStrokeWidth={2} className="hidden !bg-background/85 sm:block" />
        </ReactFlow>
        <div className="pointer-events-none absolute top-3 left-3 rounded-sm border bg-background/90 px-2.5 py-1.5 font-mono text-[9px] text-muted-foreground shadow-sm">
          拖拽节点 · 点击边编辑 mapping · DAG 禁止循环 · 多输入必须显式 Merge
        </div>
      </div>

      <aside className="border-t bg-background/80 p-4 lg:border-t-0 lg:border-l" aria-label="画布详情面板">
        {selectedNode ? (
          <div className="space-y-4">
            <div>
              <div className="font-mono text-3xs text-muted-foreground">节点详情</div>
              <h3 className="mt-1 text-sm font-semibold">{selectedNode.label}</h3>
            </div>
            <label className="block space-y-1.5 text-xs">
              <span className="font-medium">业务标签</span>
              <Input value={selectedNode.label} onChange={(event) => updateNodeLabel(event.target.value)} className="min-h-10 rounded-xs" />
            </label>
            <div className="rounded-sm border p-3 text-3xs leading-5 text-muted-foreground">
              <div>类型：{displayType(selectedNode.type)}</div>
              <div>Readiness：{gapSet.has(selectedNode.id) ? 'Needs config' : 'Ready'}</div>
              <div>最近状态：{selectedNode.recentStatus === 'idle' || !selectedNode.recentStatus ? '未运行' : selectedNode.recentStatus}</div>
            </div>
            <Button variant="outline" className="w-full text-destructive" onClick={deleteSelectedNode}>
              <Trash2 className="size-3.5" />删除节点
            </Button>
          </div>
        ) : selectedSpecEdge ? (
          <div className="space-y-4">
            <div>
              <div className="font-mono text-3xs text-muted-foreground">字段映射</div>
              <h3 className="mt-1 text-sm font-semibold">{selectedSpecEdge.source} → {selectedSpecEdge.target}</h3>
            </div>
            <label className="block space-y-1.5 text-xs">
              <span className="font-medium">Edge mapping override</span>
              <Input
                value={selectedSpecEdge.mapping?.fields.length
                  ? selectedSpecEdge.mapping.fields.map((field) => `${field.source} → ${field.target}`).join(', ')
                  : ''}
                onChange={(event) => updateMapping(event.target.value)}
                placeholder="data.items → data.records"
                className="min-h-10 rounded-xs"
              />
            </label>
            <div className="rounded-sm border bg-muted/35 p-3 text-3xs leading-5 text-muted-foreground">
              默认自动匹配同名字段；手工覆盖存储在连接上。原始结构始终保留在 <code>data.raw</code>。
            </div>
          </div>
        ) : (
          <div className="grid h-full min-h-40 place-items-center text-center">
            <div>
              <GitBranch className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-3 text-xs font-medium">选择节点或连接</p>
              <p className="mt-1 text-3xs leading-4 text-muted-foreground">编辑业务标签、查看 readiness，或覆盖字段映射。</p>
            </div>
          </div>
        )}
      </aside>
    </div>
  )
}
