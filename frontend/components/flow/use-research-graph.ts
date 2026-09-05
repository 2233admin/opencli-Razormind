import type {
  WorkflowResearchGraphEntity,
  WorkflowResearchGraphMutationAction,
  WorkflowResearchGraphProjection,
} from "@/lib/workflow/research-graph-client"
import {
  mutateResearchGraphRunPanel,
  type ResearchGraphRunPanelRequestOptions,
} from "./research-graph-run-panel-client"
import { useCallback } from "react"

export function useResearchGraphMutation(
  graph: WorkflowResearchGraphProjection | null,
  setGraph: (graph: WorkflowResearchGraphProjection) => void,
  onError: ((error: string) => void) | undefined,
  options: ResearchGraphRunPanelRequestOptions,
) {
  return useCallback(async (
    entity: WorkflowResearchGraphEntity,
    action: WorkflowResearchGraphMutationAction,
  ) => {
    try {
      const result = await mutateResearchGraphRunPanel(graph, entity, action, options)
      setGraph(result.graph)
      return result.graph
    } catch (error) {
      const message = error instanceof Error ? error.message : "ResearchGraph mutation failed"
      onError?.(message)
      throw error
    }
  }, [graph, onError, options, setGraph])
}
