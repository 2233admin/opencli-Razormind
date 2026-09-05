"use client"

import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export type TraceMetric = {
  key: string
  label: string
  value: string
  tone: string
}

export function SectionCaption({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground/70">{children}</p>
}

export function MetricGrid({ title, metrics }: { title: string; metrics: TraceMetric[] }) {
  return (
    <div className="space-y-2">
      <SectionCaption>{title}</SectionCaption>
      <div className="grid grid-cols-3 gap-2">
        {metrics.map((metric) => (
          <div key={metric.key} className="rounded-md border bg-card p-2">
            <p className="truncate text-[10px] text-muted-foreground">{metric.label}</p>
            <p
              className={cn(
                "mt-1 font-mono text-sm",
                metric.tone === "good" && "text-[#2f9e44]",
                metric.tone === "warn" && "text-[#d97706]",
              )}
            >
              {metric.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function readRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  return value as Record<string, unknown>
}
