import { z } from "zod"

import { validateWorkflowNodeHierarchy } from "./node-hierarchy.ts"

export const workflowProfileSchema = z.enum(["intelligence", "agent-debug", "sdk-dev"])

export const workflowNodeKindSchema = z.enum([
  "schedule",
  "source",
  "agent",
  "router",
  "notify",
  "inbox",
  "action",
  "flow",
  "control",
  "sink",
  "media",
])

export const workflowCapabilitySchema = z.enum([
  "trigger",
  "fetch",
  "normalize",
  "dedupe",
  "summarize",
  "score",
  "tag",
  "route",
  "send",
  "store",
  "merge",
  "accept",
  "generate",
])

const jsonRecordSchema = z.record(z.string(), z.unknown())
const workflowLocalNodeIdSchema = z
  .string()
  .min(1)
  .refine((value) => !value.includes("::") && !value.includes("__"), {
    message: 'Node id must not contain reserved path separators "::" or "__"',
  })

export const sourceAnchorSchema = z.object({
  kind: z.enum(["artifact", "url", "message", "selector"]),
  label: z.string().min(1),
  href: z.string().optional(),
  artifactPath: z.string().optional(),
  selector: z.string().optional(),
  runId: z.string().optional(),
})

export const miniNetworkPreviewSchema = z.object({
  nodes: z.number().int().nonnegative(),
  edges: z.number().int().nonnegative(),
  mode: z.enum(["title-only", "ports", "contract"]),
})

export const topicCollapseStateSchema = z.object({
  groupId: z.string().min(1),
  nodeCount: z.number().int().nonnegative(),
  mode: z.enum(["draft", "locked"]),
  packageInternal: z.boolean(),
})

export const semanticLinkSchema = z.object({
  relationship: z.enum(["related", "depends-on", "evidence", "contradicts", "implements"]),
  reason: z.string().optional(),
  confidence: z.number().min(0).max(1).optional(),
})

export const proposalStateSchema = z.enum(["draft", "proposed", "accepted"])

export const parameterBindingSchema = z.object({
  nodeId: z.string().min(1),
  source: z.enum(["params", "adapter", "data"]),
  fieldId: z.string().min(1),
})

export const parameterInterfaceGroupSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  order: z.number().optional(),
})

export const parameterInterfaceFieldSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  groupId: z.string().min(1),
  type: z.enum(["text", "textarea", "json", "number", "slider", "select", "boolean", "tokens"]),
  binding: parameterBindingSchema,
  description: z.string().optional(),
  order: z.number().optional(),
  readonly: z.boolean().optional(),
  optional: z.boolean().optional(),
  allowCustom: z.boolean().optional(),
  value: z.unknown().optional(),
  placeholder: z.string().optional(),
  min: z.number().optional(),
  max: z.number().optional(),
  step: z.number().optional(),
  options: z.array(z.object({ value: z.string(), label: z.string() })).optional(),
})

export const parameterInterfaceSchema = z.object({
  groups: z.array(parameterInterfaceGroupSchema),
  fields: z.array(parameterInterfaceFieldSchema),
})

export const workflowNodeSchema = z.object({
  id: workflowLocalNodeIdSchema,
  kind: workflowNodeKindSchema,
  capability: workflowCapabilitySchema,
  adapter: z.string().min(1).optional(),
  params: jsonRecordSchema.default({}),
  sourceAnchor: sourceAnchorSchema.optional(),
  runArtifact: z.object({
    runId: z.string().min(1),
    artifactPath: z.string().min(1),
    apiPath: z.string().optional(),
  }).optional(),
  miniNetwork: miniNetworkPreviewSchema.optional(),
  topicCollapse: topicCollapseStateSchema.optional(),
  proposalState: proposalStateSchema.optional(),
  parameterInterface: parameterInterfaceSchema.optional(),
  internals: z.object({
    locked: z.boolean().optional(),
    nodes: z.array(z.unknown()).default([]),
    edges: z.array(z.unknown()).default([]),
  }).optional(),
  ui: jsonRecordSchema.optional(),
}).passthrough()

export const workflowEdgeSchema = z.object({
  id: z.string().min(1),
  source: z.string().min(1),
  target: z.string().min(1),
  sourcePort: z.string().min(1).optional(),
  targetPort: z.string().min(1).optional(),
  label: z.string().optional(),
  condition: z.string().optional(),
  semantic: semanticLinkSchema.optional(),
  weight: z.number().min(0).max(1).optional(),
  contractId: z.string().min(1).optional(),
  proposalState: proposalStateSchema.optional(),
  ui: jsonRecordSchema.optional(),
})

export const workflowSettingsSchema = z.object({
  timezone: z.string().min(1).default("Asia/Shanghai"),
  deterministicSimulation: z.boolean().default(true),
  maxItemsPerRun: z.number().int().positive().default(20),
})

export const adapterBindingSchema = z.object({
  id: z.string().min(1),
  type: z.enum(["source", "notification", "storage", "agent", "utility"]),
  provider: z.string().min(1),
  mode: z.enum(["fixture", "mock", "webhook", "live"]).default("fixture"),
  config: jsonRecordSchema.default({}),
})

export const agentPermissionsSchema = z.object({
  canFetchNetwork: z.boolean().default(false),
  canSendNotifications: z.boolean().default(false),
  canWriteInbox: z.boolean().default(true),
  canMutateExternalSites: z.boolean().default(false),
  allowedDomains: z.array(z.string()).default([]),
})

export const workflowProjectSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  profile: workflowProfileSchema,
  version: z.literal(1).default(1),
  nodes: z.array(workflowNodeSchema).min(1),
  edges: z.array(workflowEdgeSchema),
  settings: workflowSettingsSchema.default({
    timezone: "Asia/Shanghai",
    deterministicSimulation: true,
    maxItemsPerRun: 20,
  }),
  adapters: z.array(adapterBindingSchema).default([]),
  agentPermissions: agentPermissionsSchema.default({
    canFetchNetwork: false,
    canSendNotifications: false,
    canWriteInbox: true,
    canMutateExternalSites: false,
    allowedDomains: [],
  }),
}).passthrough()

export type WorkflowProfile = z.infer<typeof workflowProfileSchema>
export type WorkflowNodeKind = z.infer<typeof workflowNodeKindSchema>
export type WorkflowCapability = z.infer<typeof workflowCapabilitySchema>
export type WorkflowProjectNode = z.infer<typeof workflowNodeSchema>
export type WorkflowProjectEdge = z.infer<typeof workflowEdgeSchema>
export type WorkflowSettings = z.infer<typeof workflowSettingsSchema>
export type AdapterBinding = z.infer<typeof adapterBindingSchema>
export type AgentPermissions = z.infer<typeof agentPermissionsSchema>
export type WorkflowProject = z.infer<typeof workflowProjectSchema>

const OPENCLI_SOURCE_SLOT_CATALOG_ID = "intelligence.source.opencli-slot"
const LEGACY_OPENCLI_ADAPTER_CATALOG_ID = /^opencli\.adapter\.[a-z0-9][a-z0-9._-]*$/i

function normalizeLegacyOpenCLISourceCatalogIds(project: WorkflowProject): WorkflowProject {
  const adapters = new Map(project.adapters.map((adapter) => [adapter.id, adapter]))
  let changed = false
  const nodes = project.nodes.map((node) => {
    const catalogId = node.ui?.catalogId
    const opencliAdapterNodeId = node.params.opencliAdapterNodeId
    const adapter = node.adapter ? adapters.get(node.adapter) : undefined
    if (
      node.kind !== "source" ||
      node.capability !== "fetch" ||
      typeof catalogId !== "string" ||
      !LEGACY_OPENCLI_ADAPTER_CATALOG_ID.test(catalogId) ||
      opencliAdapterNodeId !== catalogId ||
      adapter?.type !== "source" ||
      adapter.provider !== "opencli"
    ) {
      return node
    }
    changed = true
    return {
      ...node,
      ui: { ...node.ui, catalogId: OPENCLI_SOURCE_SLOT_CATALOG_ID },
    }
  })
  return changed ? { ...project, nodes } : project
}

export function parseWorkflowProject(input: unknown): WorkflowProject {
  const project = normalizeLegacyOpenCLISourceCatalogIds(workflowProjectSchema.parse(input))
  validateWorkflowReferences(project)
  return project
}

export function validateWorkflowReferences(project: WorkflowProject): void {
  const nodeIds = new Set(project.nodes.map((node) => node.id))
  const edgeIds = new Set<string>()
  for (const edge of project.edges) {
    if (edgeIds.has(edge.id)) {
      throw new Error(`Workflow root scope contains duplicate edge id "${edge.id}"`)
    }
    edgeIds.add(edge.id)
    if (!nodeIds.has(edge.source)) {
      throw new Error(`Workflow edge "${edge.id}" references missing source "${edge.source}"`)
    }
    if (!nodeIds.has(edge.target)) {
      throw new Error(`Workflow edge "${edge.id}" references missing target "${edge.target}"`)
    }
  }

  const adapterIds = new Set(project.adapters.map((adapter) => adapter.id))
  validateWorkflowNodeHierarchy(project.nodes, { adapterIds })
}
