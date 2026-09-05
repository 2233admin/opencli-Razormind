"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type {
  WorkflowResearchGraphEntity,
  WorkflowResearchGraphMutationAction,
  WorkflowResearchGraphProjection,
} from "@/lib/workflow/research-graph-client"
import { researchGraphClaimActions } from "@/lib/workflow/research-graph-actions"

type ResearchGraphReadoutProps = {
  graph: WorkflowResearchGraphProjection
  onMutate: (
    entity: WorkflowResearchGraphEntity,
    action: WorkflowResearchGraphMutationAction,
  ) => Promise<WorkflowResearchGraphProjection>
}

function entityLabel(entity: WorkflowResearchGraphEntity): string {
  return entity.label?.trim() || entity.id
}


export function ResearchGraphReadout({
  graph,
  onMutate,
}: ResearchGraphReadoutProps) {
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [sourceId, setSourceId] = useState("")
  const [evidenceId, setEvidenceId] = useState("")
  const [pending, setPending] = useState<string | null>(null)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const sources = graph.entities.filter((entity) => entity.kind === "source")
  const nodeId = sources[0]?.nodeId ?? ""
  const reason = !nodeId
    ? "缺少 nodeId"
    : !sourceId
      ? "请选择 source"
      : !evidenceId
        ? "请选择 evidence"
        : !title.trim()
          ? "请输入标题"
          : ""

  async function mutate(
    entity: WorkflowResearchGraphEntity,
    action: WorkflowResearchGraphMutationAction,
  ) {
    setPending(`${action}:${entity.id}`)
    setMutationError(null)
    try {
      await onMutate(entity, action)
    } catch (error) {
      setMutationError(
        error instanceof Error ? error.message : "ResearchGraph mutation failed",
      )
    } finally {
      setPending(null)
    }
  }

  async function submitProposal() {
    if (reason) return
    await mutate(
      {
        id: `claim-${graph.runId}-${crypto.randomUUID()}`,
        kind: "claim",
        label: title.trim(),
        sourceIds: [sourceId],
        evidenceIds: [evidenceId],
        attributes: { content: content.trim() },
        eventId: "proposal",
        sequence: graph.lastSequence + 1,
        runId: graph.runId,
        traceId: graph.traceId,
        nodeId,
        revisionId: graph.currentRevision,
        lineage: { sourceId, evidenceId },
        state: "proposed",
        authoritative: false,
      },
      "propose",
    )
  }

  return (
    <div className="space-y-3 rounded-md border p-2">
      <div className="space-y-2">
        <Input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Proposal title"
        />
        <Textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder="Content"
        />
        <Input
          value={sourceId}
          onChange={(event) => setSourceId(event.target.value)}
          placeholder="Source ID"
        />
        <Input
          value={evidenceId}
          onChange={(event) => setEvidenceId(event.target.value)}
          placeholder="Evidence ID"
        />
        <Button
          disabled={Boolean(reason) || pending !== null}
          onClick={() => void submitProposal()}
        >
          Propose claim
        </Button>
        {reason ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
      </div>

      <section aria-label="Research graph entities" className="space-y-2">
        <h3 className="text-xs font-medium">Research graph entities</h3>
        {graph.entities.map((entity) => {
          const actions = researchGraphClaimActions(entity)
          return (
            <article
              key={entity.id}
              className="space-y-2 rounded border p-2 text-xs"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-medium">{entityLabel(entity)}</p>
                  <p className="font-mono text-muted-foreground">{entity.kind}</p>
                </div>
                <span className="rounded border px-1.5 py-0.5">{entity.state}</span>
              </div>
              {actions.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {actions.map((action) => {
                    const buttonLabel = action[0].toUpperCase() + action.slice(1)
                    const isPending = pending === `${action}:${entity.id}`
                    return (
                      <Button
                        key={action}
                        size="sm"
                        variant={action === "reject" || action === "retract" ? "outline" : "default"}
                        disabled={pending !== null}
                        onClick={() => void mutate(entity, action)}
                      >
                        {isPending ? `${buttonLabel}…` : buttonLabel}
                      </Button>
                    )
                  })}
                </div>
              ) : null}
            </article>
          )
        })}
      </section>

      {pending ? <p role="status" className="text-xs text-muted-foreground">Updating ResearchGraph…</p> : null}
      {mutationError ? <p role="alert" className="text-xs text-destructive">{mutationError}</p> : null}
    </div>
  )
}
