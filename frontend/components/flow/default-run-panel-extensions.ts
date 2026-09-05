import type { RunPanelExtension } from "@/lib/workflow/run-panel-extensions"
import { ResearchGraphRunPanelExtension } from "./research-graph-run-panel-extension"

export function createDefaultRunPanelExtensions(
  researchGraphEnabled = true,
): readonly RunPanelExtension[] {
  return researchGraphEnabled
    ? [{ key: "research-graph", Component: ResearchGraphRunPanelExtension }]
    : []
}
