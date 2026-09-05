import type { ComponentType } from "react"

export type RunPanelProjection = { readonly runId: string }

export type RunPanelScope = {
  readonly workspaceId: string
  readonly projectId: string
  readonly workflowId: string
}

export type RunPanelExtensionContext = {
  runId: string | null
  projection: RunPanelProjection | null
  scope?: RunPanelScope | null
  authorization: string | null
  runStatus: "idle" | "running" | "ready" | "error"
  lifecycle: { onError: (message: string) => void }
}

export type RunPanelExtension = {
  key: string
  Component: ComponentType<RunPanelExtensionContext>
}

export function resolveRunPanelExtensions(
  extensions: readonly RunPanelExtension[] = [],
): readonly RunPanelExtension[] {
  const seen = new Set<string>()
  for (const extension of extensions) {
    if (seen.has(extension.key)) {
      throw new Error(`Duplicate run-panel extension key: ${extension.key}`)
    }
    seen.add(extension.key)
  }
  return [...extensions].sort((left, right) => left.key.localeCompare(right.key))
}

export function dispatchRunPanelExtensions(
  extensions: readonly RunPanelExtension[],
  context: RunPanelExtensionContext,
): readonly RunPanelExtension[] {
  return resolveRunPanelExtensions(extensions).filter((extension) => {
    return Boolean(extension.Component) && context.runStatus !== "idle"
  })
}
