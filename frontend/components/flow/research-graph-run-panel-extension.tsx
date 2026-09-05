"use client"

import { useEffect, useMemo, useState } from "react"
import type { RunPanelExtensionContext } from "@/lib/workflow/run-panel-extensions"
import type { WorkflowResearchGraphProjection } from "@/lib/workflow/research-graph-client"
import {
  loadResearchGraphRunPanel,
  researchGraphRunPanelRequestOptions,
} from "./research-graph-run-panel-client"
import { ResearchGraphReadout } from "./research-graph-readout"
import { useResearchGraphMutation } from "./use-research-graph"

export function ResearchGraphRunPanelExtension({
  runId,
  authorization,
  scope,
  lifecycle,
}: RunPanelExtensionContext) {
  const [graph, setGraph] = useState<WorkflowResearchGraphProjection | null>(null)
  const requestOptions = useMemo(
    () => researchGraphRunPanelRequestOptions({ authorization, scope }),
    [authorization, scope],
  )
  const mutate = useResearchGraphMutation(graph, setGraph, lifecycle.onError, requestOptions)
  useEffect(() => {
    let cancelled = false
    if (!runId) {
      setGraph(null)
      return () => {
        cancelled = true
      }
    }
    void loadResearchGraphRunPanel(runId, requestOptions)
      .then((projection) => {
        if (!cancelled) setGraph(projection)
      })
      .catch(() => {
        if (!cancelled) setGraph(null)
      })
    return () => {
      cancelled = true
    }
  }, [requestOptions, runId])

  return graph ? <ResearchGraphReadout graph={graph} onMutate={mutate} /> : null
}
