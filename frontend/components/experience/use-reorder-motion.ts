'use client'

import { useLayoutEffect, useRef } from 'react'

const DURATION_MS = 180

function positions(container: HTMLElement) {
  const top = container.getBoundingClientRect().top
  return new Map(Array.from(container.children).flatMap((child) => {
    if (!(child instanceof HTMLElement) || !child.dataset.reorderId) return []
    return [[child.dataset.reorderId, child.getBoundingClientRect().top - top] as const]
  }))
}

/** Small FLIP helper adapted from OpenAlice's workspace reorder primitive. */
export function useReorderMotion<T extends HTMLElement>(ids: readonly string[]) {
  const ref = useRef<T | null>(null)
  const previous = useRef(new Map<string, number>())
  const animations = useRef(new Map<string, Animation>())
  const key = ids.join('\u001f')
  useLayoutEffect(() => {
    const container = ref.current
    if (!container) return
    const activeAnimations = animations.current
    for (const animation of activeAnimations.values()) animation.cancel()
    activeAnimations.clear()
    const next = positions(container)
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    const stopWhenReduced = () => {
      if (!media?.matches) return
      for (const animation of activeAnimations.values()) animation.cancel()
      activeAnimations.clear()
    }
    media?.addEventListener('change', stopWhenReduced)
    const reduce = media?.matches
    if (!reduce && previous.current.size) {
      for (const child of Array.from(container.children)) {
        if (!(child instanceof HTMLElement)) continue
        const id = child.dataset.reorderId
        if (!id) continue
        const before = previous.current.get(id)
        const after = next.get(id)
        if (before === undefined || after === undefined || Math.abs(before - after) < 1) continue
        const animation = child.animate([{ transform: `translateY(${before - after}px)` }, { transform: 'translateY(0)' }], {
          duration: DURATION_MS,
          easing: 'cubic-bezier(.32,.72,0,1)',
        })
        activeAnimations.set(id, animation)
        animation.addEventListener('finish', () => activeAnimations.delete(id), { once: true })
      }
    }
    previous.current = next
    return () => {
      media?.removeEventListener('change', stopWhenReduced)
      for (const animation of activeAnimations.values()) animation.cancel()
      activeAnimations.clear()
    }
  }, [key])
  return ref
}
