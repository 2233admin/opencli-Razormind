import { workflowNodeSize, workflowNodeSizeForData } from "./node-geometry"
import type { WorkflowNode } from "./types"

/**
 * dnd-kit 风格的 AABB 碰撞内核。
 * - rectIntersection: 两个矩形是否相交（带间距）
 * - resolveCollisions: 以"被移动节点"为优先，把与其重叠的兄弟节点沿最小平移向量推开
 * - findFreePosition: 为新节点寻找一个不与现有节点重叠的落点
 */

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

export const COLLISION_GAP = 24

const DEFAULT_SIZE: Record<string, { width: number; height: number }> = {
  note: { width: 180, height: 100 },
  shape: { width: 140, height: 100 },
  group: { width: 320, height: 220 },
}

export function nodeRect(node: WorkflowNode): Rect {
  const fallback = node.type === "workflow"
    ? workflowNodeSizeForData(node.data)
    : DEFAULT_SIZE[node.type ?? "workflow"] ?? workflowNodeSize()
  return {
    x: node.position.x,
    y: node.position.y,
    width: node.measured?.width ?? (node.width as number) ?? fallback.width,
    height: node.measured?.height ?? (node.height as number) ?? fallback.height,
  }
}

export function rectsIntersect(a: Rect, b: Rect, gap = 0): boolean {
  return (
    a.x < b.x + b.width + gap &&
    a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap &&
    a.y + a.height + gap > b.y
  )
}

/** 最小平移向量：把 b 推离 a 所需的位移（推 b，不动 a） */
function minimumTranslation(a: Rect, b: Rect, gap: number): { dx: number; dy: number } {
  const overlapX =
    b.x + b.width / 2 >= a.x + a.width / 2
      ? a.x + a.width + gap - b.x // push right
      : -(b.x + b.width + gap - a.x) // push left
  const overlapY =
    b.y + b.height / 2 >= a.y + a.height / 2
      ? a.y + a.height + gap - b.y // push down
      : -(b.y + b.height + gap - a.y) // push up

  // 沿重叠更小的轴推开（最小平移）
  if (Math.abs(overlapX) < Math.abs(overlapY)) return { dx: overlapX, dy: 0 }
  return { dx: 0, dy: overlapY }
}

/** 参与碰撞的节点：真实算子/图形，跳过分组容器与备注 */
function collidable(node: WorkflowNode): boolean {
  return node.type === "workflow" || node.type === "shape"
}

/**
 * 迭代分离：priorityId 节点保持不动，其余同层节点被推开。
 * 只在同一个 parentId 层级内做碰撞（分组内外互不干扰）。
 */
export function resolveCollisions(
  nodes: WorkflowNode[],
  priorityId: string,
  gap = COLLISION_GAP,
  maxIterations = 32,
): WorkflowNode[] {
  const priority = nodes.find((n) => n.id === priorityId)
  if (!priority || !collidable(priority)) return nodes

  const layer = priority.parentId
  const working = nodes.map((n) => ({ ...n, position: { ...n.position } }))

  for (let iter = 0; iter < maxIterations; iter++) {
    let moved = false
    for (const pusher of working) {
      if (!collidable(pusher) || pusher.parentId !== layer) continue
      const pusherRect = nodeRect(pusher)
      for (const other of working) {
        if (other.id === pusher.id || other.id === priorityId) continue
        if (!collidable(other) || other.parentId !== layer) continue
        const otherRect = nodeRect(other)
        if (!rectsIntersect(pusherRect, otherRect, 0)) continue
        const { dx, dy } = minimumTranslation(pusherRect, otherRect, gap)
        other.position = { x: other.position.x + dx, y: other.position.y + dy }
        moved = true
      }
    }
    if (!moved) break
  }
  return working
}

/**
 * 为新节点寻找空位：从期望位置开始，按 右侧列 → 上下错位 的顺序扫描。
 */
export function findFreePosition(
  nodes: WorkflowNode[],
  desired: { x: number; y: number },
  size: { width: number; height: number },
  parentId?: string,
  gap = COLLISION_GAP,
): { x: number; y: number } {
  const others = nodes.filter((n) => collidable(n) && n.parentId === parentId)
  const stepY = size.height + gap
  const stepX = size.width + gap

  for (let col = 0; col < 6; col++) {
    for (let row = 0; row < 12; row++) {
      // 0, +1, -1, +2, -2 ... 上下交替
      const offset = row === 0 ? 0 : row % 2 === 1 ? Math.ceil(row / 2) : -Math.ceil(row / 2)
      const candidate: Rect = {
        x: desired.x + col * stepX,
        y: desired.y + offset * stepY,
        width: size.width,
        height: size.height,
      }
      if (!others.some((n) => rectsIntersect(candidate, nodeRect(n), gap / 2))) {
        return { x: candidate.x, y: candidate.y }
      }
    }
  }
  return desired
}
