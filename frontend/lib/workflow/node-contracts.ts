import type { AdapterBinding, WorkflowProject, WorkflowProjectEdge, WorkflowProjectNode } from "./schema"

export type PortDirection = "input" | "output"
export type PortDataType =
  | "trigger"
  | "text"
  | "items[]"
  | "CollectorOutputV1"
  | "CollectorMergeInputV1"
  | "mediaAsset[]"
  | "mediaGenerationResult"
  | "recordCandidate[]"
  | "record[]"
  | "runtimeArtifact[]"
  | "scoredItems[]"
  | "summary[]"
  | "branch"
  | "delivery"
  | "storedItems[]"
  | "unknown"
export type ParamDataType = "string" | "number" | "boolean" | "string[]" | "object" | "object[]"
export type ContractStatus = "pass" | "warn" | "fail"

export type PortContract = {
  id: string
  direction: PortDirection
  type: PortDataType
  required: boolean
  description: string
  cardinality?: "one" | "many"
  minConnections?: number
  legacyIds?: string[]
}

export type ParamContract = {
  id: string
  source: "params" | "adapter.mode" | "adapter.config"
  type: ParamDataType
  required: boolean
  defaultValue?: unknown
  enum?: string[]
  min?: number
  max?: number
  description: string
}

export type NodeContract = {
  id: string
  title: string
  dataModel: string
  ports: PortContract[]
  params: ParamContract[]
  assertions: string[]
}

export type NodeContractFinding = {
  nodeId: string
  contractId: string
  status: ContractStatus
  summary: string
  evidence: Record<string, unknown>
}

export type ProjectContractReport = {
  status: ContractStatus
  nodeContracts: Array<{
    nodeId: string
    contractId: string
    title: string
    ports: PortContract[]
    params: ParamContract[]
    assertions: string[]
  }>
  portCoverage: {
    nodesWithContracts: number
    totalNodes: number
    percent: number
    missingNodeIds: string[]
  }
  findings: NodeContractFinding[]
}

export type EdgeContractResolution = {
  edgeId: string
  sourceNodeId: string
  targetNodeId: string
  sourcePort: PortContract | null
  targetPort: PortContract | null
  compatible: boolean
  explicit: {
    sourcePort: boolean
    targetPort: boolean
  }
}

const CONTRACTS: Record<string, NodeContract> = {
  "media.image-generation": contract(
    "media.image-generation",
    "Image Generation",
    "prompt + mediaAsset[] -> mediaAsset[] + mediaGenerationResult",
    [
      port("prompt", "input", "text", false, "Optional prompt supplied by an upstream text-producing node."),
      port("assets", "input", "mediaAsset[]", false, "Optional OpenCLI assets used as initial, mask, control, or reference images."),
    ],
    [
      port("assets", "output", "mediaAsset[]", true, "Emits durable OpenCLI asset references after ingest succeeds."),
      port("generation", "output", "mediaGenerationResult", true, "Emits the durable generation job result and lineage."),
    ],
    [
      param("canvasDocumentId", "params", "string", true, "", {
        description: "Editable Canvas document id. Publication resolves it to an immutable snapshot outside node params.",
      }),
    ],
    [
      "published workflow versions must resolve canvasDocumentId to an immutable snapshotId",
      "runtime completion requires asset ingest and atomic OpenCLI asset commit",
      "outputs must never contain an Invoke temporary URL",
    ],
  ),
  "media.image-asset": contract(
    "media.image-asset",
    "Image Asset",
    "pinned asset ids -> mediaAsset[]",
    [],
    [port("assets", "output", "mediaAsset[]", true, "Emits pinned OpenCLI asset references without generating new media.")],
    [
      param("assetIds", "params", "string[]", true, [], {
        description: "Workspace-owned OpenCLI asset ids selected from the first-party gallery.",
      }),
    ],
    [
      "asset ids must be resolved within the workflow workspace and project scope",
      "outputs must never contain an Invoke temporary URL",
    ],
  ),
  "intelligence.input.collection-need": contract(
    "intelligence.input.collection-need",
    "Collection Need",
    "need -> trigger",
    [],
    [port("out", "output", "trigger", true, "Emits a manual demand trigger for demand-draft assembly.")],
    [
      param("text", "params", "string", true, "抓小红书热帖", {
        description: "Natural-language collection need. Runtime details are resolved by existing source capabilities.",
      }),
      param("locale", "params", "string", false, "zh-CN", {
        enum: ["zh-CN", "en-US"],
        description: "Locale used for demand interpretation and proposal labels.",
      }),
    ],
    [
      "user demand text must remain business intent, not raw executor params",
      "node assembly must call the backend demand-draft endpoint",
    ],
  ),
  "intelligence.schedule.cron": contract(
    "intelligence.schedule.cron",
    "Cron Schedule",
    "clock -> trigger",
    [],
    [port("out", "output", "trigger", true, "Emits a deterministic trigger tick.")],
    [
      param("interval", "params", "string", true, "5m", { description: "Interval or cron-like cadence." }),
      param("timezone", "params", "string", false, "Asia/Shanghai", {
        enum: ["Asia/Shanghai", "UTC", "America/New_York"],
        description: "Timezone used for future wall-clock scheduling.",
      }),
    ],
    ["interval must be present", "output trigger must be traceable"],
  ),
  "intelligence.source.jin10": contract(
    "intelligence.source.jin10",
    "JIN10 Source",
    "trigger -> items[]",
    [port("in", "input", "trigger", true, "Consumes a schedule trigger.")],
    [port("out", "output", "items[]", true, "Emits normalized JIN10 source items.")],
    [
      param("mode", "adapter.mode", "string", true, "fixture", {
        enum: ["fixture", "live"],
        description: "Adapter mode. Fixture is deterministic; live requires network permission.",
      }),
      param("limit", "params", "number", true, 20, { min: 1, max: 100, description: "Maximum source items per run." }),
      param("importantOnly", "params", "boolean", false, false, { description: "Filters to important items only." }),
      param("channel", "params", "string", false, "kuaixun", { enum: ["kuaixun"], description: "JIN10 feed channel." }),
    ],
    ["source adapter must be registered", "items[] output must include stable item ids"],
  ),
  "intelligence.source.rss": contract(
    "intelligence.source.rss",
    "RSS / Atom Reader",
    "trigger -> items[]",
    [port("in", "input", "trigger", false, "Consumes a schedule trigger.")],
    [port("out", "output", "items[]", true, "Emits parsed RSS or Atom entries.")],
    [
      param("feedUrl", "params", "string", true, "https://www.federalreserve.gov/feeds/press_all.xml", {
        description: "Public RSS or Atom URL. Its host must be listed in agentPermissions.allowedDomains.",
      }),
      param("maxEntries", "params", "number", true, 20, {
        min: 1,
        max: 500,
        description: "Maximum feed entries read per run, also capped by project settings.",
      }),
      param("sourceGroup", "params", "string", true, "macro-policy", {
        description: "Business grouping key preserved in lineage and Records.",
      }),
      param("site", "params", "string", false, "federal-reserve", {
        description: "Human-readable source key used by Records and source ownership.",
      }),
      param("providerId", "params", "string", false, "", {
        description: "Optional self-hosted RSS generator Provider resolved by the backend at run time.",
      }),
      param("generatorType", "params", "string", false, "rsshub", {
        enum: ["rsshub", "rss_bridge"],
        description: "Generator kind for a provider-backed feed URL.",
      }),
      param("generatorSelection", "params", "object", false, {}, {
        description: "Selected RSSHub route or RSS-Bridge bridge and its non-secret parameters.",
      }),
    ],
    [
      "feed host must be allowed",
      "provider token must remain backend-only",
      "items[] must retain sourceGroup lineage",
    ],
  ),
  "intelligence.source.rsshub": contract(
    "intelligence.source.rsshub",
    "RSSHub Reader",
    "trigger -> items[]",
    [port("in", "input", "trigger", false, "Consumes a schedule or source-pool trigger.")],
    [port("out", "output", "items[]", true, "Emits entries from the selected RSSHub route.")],
    [
      param("providerId", "params", "string", true, "", {
        description: "Enabled RSSHub Provider connection resolved by the backend.",
      }),
      param("generatorType", "params", "string", true, "rsshub", {
        enum: ["rsshub"],
        description: "Locks this atomic node to the RSSHub generator.",
      }),
      param("route", "params", "string", true, "", {
        description: "RSSHub route selected from the connected Provider.",
      }),
      param("routeParameters", "params", "object", false, {}, {
        description: "Non-secret RSSHub route parameters.",
      }),
      param("generatorSelection", "params", "object", false, {}, {
        description: "Provider-generated route payload retained for API-created nodes.",
      }),
      param("maxEntries", "params", "number", true, 20, {
        min: 1,
        max: 500,
        description: "Maximum generated-feed entries read per run.",
      }),
      param("sourceGroup", "params", "string", true, "rsshub", {
        description: "Business grouping key preserved in lineage and Records.",
      }),
    ],
    [
      "providerId must reference an enabled RSSHub Provider",
      "route must be selected before execution",
      "provider credentials must remain backend-only",
    ],
  ),
  "intelligence.source.rss-bridge": contract(
    "intelligence.source.rss-bridge",
    "RSS-Bridge Reader",
    "trigger -> items[]",
    [port("in", "input", "trigger", false, "Consumes a schedule or source-pool trigger.")],
    [port("out", "output", "items[]", true, "Emits entries from the selected RSS-Bridge bridge.")],
    [
      param("providerId", "params", "string", true, "", {
        description: "Enabled RSS-Bridge Provider connection resolved by the backend.",
      }),
      param("generatorType", "params", "string", true, "rss_bridge", {
        enum: ["rss_bridge"],
        description: "Locks this atomic node to the RSS-Bridge generator.",
      }),
      param("bridge", "params", "string", true, "", {
        description: "RSS-Bridge bridge selected from the connected Provider.",
      }),
      param("bridgeParameters", "params", "object", false, {}, {
        description: "Non-secret RSS-Bridge parameters.",
      }),
      param("generatorSelection", "params", "object", false, {}, {
        description: "Provider-generated bridge payload retained for API-created nodes.",
      }),
      param("maxEntries", "params", "number", true, 20, {
        min: 1,
        max: 500,
        description: "Maximum generated-feed entries read per run.",
      }),
      param("sourceGroup", "params", "string", true, "rss-bridge", {
        description: "Business grouping key preserved in lineage and Records.",
      }),
    ],
    [
      "providerId must reference an enabled RSS-Bridge Provider",
      "bridge must be selected before execution",
      "provider credentials must remain backend-only",
    ],
  ),
  "intelligence.source.http": contract(
    "intelligence.source.http",
    "HTTP / API Reader",
    "trigger -> items[]",
    [port("in", "input", "trigger", false, "Consumes a schedule or source-pool trigger.")],
    [port("out", "output", "items[]", true, "Emits JSON objects selected from the HTTP response.")],
    [
      param("url", "params", "string", true, "", {
        description: "HTTP or HTTPS endpoint whose host must be allowed by project permissions.",
      }),
      param("method", "params", "string", true, "GET", {
        enum: ["GET", "POST"],
        description: "Guarded request method.",
      }),
      param("resultPath", "params", "string", false, "", {
        description: "Optional dot-separated path selecting the item list from the JSON response.",
      }),
      param("headers", "params", "object", false, {}, {
        description: "Non-secret request headers. Credentials should use a managed connection.",
      }),
      param("query", "params", "object", false, {}, {
        description: "Query-string parameters.",
      }),
      param("sourceGroup", "params", "string", true, "http-api", {
        description: "Business grouping key preserved in lineage and Records.",
      }),
    ],
    [
      "endpoint host must be listed in agentPermissions.allowedDomains",
      "response must be valid JSON and no larger than the runtime limit",
      "items[] must retain sourceGroup lineage",
    ],
  ),
  "intelligence.source.opencli-slot": contract(
    "intelligence.source.opencli-slot",
    "OpenCLI Source Slot",
    "trigger -> items[]",
    [port("in", "input", "trigger", false, "Consumes a package fanout trigger.")],
    [port("out", "output", "items[]", true, "Emits items fetched through the OpenCLI channel.")],
    [
      param("site", "params", "string", true, "bilibili", {
        description: "Logical OpenCLI site key such as bilibili or xiaohongshu.",
      }),
      param("command", "params", "string", true, "search", {
        description: "Structured command selected by the package/source planner.",
      }),
      param("sourceGroup", "params", "string", true, "video", {
        description: "Downstream grouping key used in traces and source selection.",
      }),
      param("args", "params", "object", false, { keyword: "ai" }, {
        description: "Structured command args selected by the package/source planner.",
      }),
    ],
    ["source slot params must stay structured", "slot execution is delegated to OpenCLI runtime resources"],
  ),
  "intelligence.source.feishu-table": contract(
    "intelligence.source.feishu-table",
    "Feishu Bitable Keywords",
    "trigger -> keyword items[]",
    [port("in", "input", "trigger", false, "Consumes a schedule or manual trigger.")],
    [port("out", "output", "items[]", true, "Emits bounded keyword rows with stable Feishu lineage.")],
    [
      param("sourceId", "params", "string", true, "", { description: "Configured DataSource id; credentials never live in the graph." }),
      param("app_token", "params", "string", true, "", { description: "Feishu Bitable app token identifier." }),
      param("table_id", "params", "string", true, "", { description: "Feishu table identifier." }),
      param("keyword_field", "params", "string", true, "关键词", { description: "Column containing the search term." }),
      param("status_field", "params", "string", false, "状态", { description: "Optional eligibility status column." }),
      param("eligible_status", "params", "string", false, "待采集", { description: "Optional value required before a row is collected." }),
      param("max_rows", "params", "number", false, 500, { min: 1, max: 5000, description: "Hard row bound per run." }),
    ],
    ["sourceId must resolve to an enabled feishu_table DataSource", "tenant token must remain in encrypted source credentials", "each item must retain source_row_id lineage"],
  ),
  "intelligence.source.doubao-research": contract(
    "intelligence.source.doubao-research",
    "Doubao Research",
    "keyword items[] -> research items[]",
    [port("in", "input", "items[]", false, "Consumes Feishu keyword rows.")],
    [port("out", "output", "items[]", true, "Emits Doubao answers and captured citations.")],
    [
      param("question", "params", "string", true, "", { description: "Research question; runtime may interpolate an upstream keyword." }),
      param("site_session", "params", "string", false, "ephemeral", { enum: ["ephemeral", "persistent"], description: "OpenCLI Doubao session policy." }),
      param("executionMode", "params", "string", false, "channel", { enum: ["channel", "agent"], description: "Use the legacy channel or a connected local browser Agent." }),
      param("agentRuntime", "params", "string", false, "bbx", { enum: ["bbx", "codex", "claude-code"], description: "Preferred local Agent runtime when executionMode is agent." }),
    ],
    ["Doubao session readiness must be verified before live execution", "answers must preserve upstream keyword lineage"],
  ),
  "intelligence.source.pool": contract(
    "intelligence.source.pool",
    "Source Pool",
    "need -> trigger",
    [port("in", "input", "trigger", false, "Consumes package demand or trigger context.")],
    [port("out", "output", "trigger", true, "Fans out the trigger to OpenCLI source slots.")],
    [
      param("sourceCount", "params", "number", true, 0, {
        min: 0,
        description: "Number of materialized source slots.",
      }),
      param("sourceGroups", "params", "string[]", false, [], {
        description: "Source groups selected by the demand/package planner.",
      }),
      param("fanout", "params", "string", true, "parallel", {
        enum: ["parallel"],
        description: "OpenCLI HDA source slots always run as parallel fanout.",
      }),
    ],
    [
      "source pool must fan out only to package-owned source slots",
      "resource credentials and runtime identities must stay outside node params",
    ],
  ),
  "intelligence.processing.normalize": contract(
    "intelligence.processing.normalize",
    "Normalize Items",
    "items[] -> recordCandidate[]",
    [port("in", "input", "items[]", true, "Consumes source items.")],
    [port("out", "output", "recordCandidate[]", true, "Emits normalized record candidates.")],
    [
      param("language", "params", "string", true, "zh-CN", { description: "Language metadata annotation; content is not translated." }),
      param("preserveSourceRefs", "params", "boolean", false, true, { description: "Keeps source references available downstream." }),
    ],
    ["normalized item count should match fetched item count in deterministic simulation"],
  ),
  "intelligence.processing.dedupe": contract(
    "intelligence.processing.dedupe",
    "Dedupe Items",
    "recordCandidate[] -> recordCandidate[]",
    [port("in", "input", "recordCandidate[]", true, "Consumes candidate items.")],
    [port("out", "output", "recordCandidate[]", true, "Emits unique candidates.")],
    [
      param("key", "params", "string", true, "title+source+publishedAt", { description: "Deduplication key expression." }),
      param("window", "params", "string", true, "24h", { description: "Deduplication time window." }),
    ],
    ["dedupe key must be explicit"],
  ),
  "intelligence.data.generate": dataOperatorContract("intelligence.data.generate", "Generate Data"),
  "intelligence.data.filter": dataOperatorContract("intelligence.data.filter", "Filter Data"),
  "intelligence.data.evaluate": dataOperatorContract("intelligence.data.evaluate", "Evaluate Data"),
  "intelligence.data.refine": dataOperatorContract("intelligence.data.refine", "Refine Data"),
  "collection.source.web": collectorContract("web", "网页采集"),
  "collection.source.api": collectorContract("api", "API 采集"),
  "collection.source.rss": collectorContract("rss", "RSS 采集"),
  "collection.source.cli": collectorContract("cli", "CLI 采集"),
  "intelligence.flow.merge": contract(
    "intelligence.flow.merge",
    "Merge",
    "CollectorMergeInputV1* -> recordCandidate[]",
    [
      port("in", "input", "CollectorMergeInputV1", true, "Consumes collector envelopes or candidate streams.", {
        cardinality: "many",
        minConnections: 1,
        legacyIds: ["in1", "in2"],
      }),
    ],
    [port("out", "output", "recordCandidate[]", true, "Emits merged candidates with lineage preserved.")],
    [
      param("strategy", "params", "string", true, "concat", {
        enum: ["concat", "key_join", "dedupe", "priority", "windowed"],
        description: "Merge strategy. The first loop executes concat and preserves lineage.",
      }),
      param("preserveLineage", "params", "boolean", true, true, {
        description: "Lineage preservation is required for first-loop merges.",
      }),
      param("inputType", "params", "string", true, "recordCandidate[]", {
        enum: ["recordCandidate[]", "record[]", "runtimeArtifact[]"],
        description: "Typed upstream stream accepted by this merge preset.",
      }),
    ],
    [
      "merge requires at least one compatible upstream input",
      "legacy in1/in2 edges remain load-compatible",
      "lineage must be preserved for every output item",
    ],
  ),
  "intelligence.agent.summary": contract(
    "intelligence.agent.summary",
    "LLM Summary",
    "items[] -> summary[]",
    [port("in", "input", "items[]", true, "Consumes normalized items.")],
    [port("out", "output", "summary[]", true, "Emits item summaries with source evidence.")],
    [
      param("model", "params", "string", true, "deepseek", {
        enum: ["deepseek", "gpt", "claude"],
        description: "LLM provider family.",
      }),
      param("style", "params", "string", true, "macro-brief", {
        enum: ["macro-brief", "risk-brief", "headline"],
        description: "Summary prompt preset.",
      }),
      param("maxChars", "params", "number", true, 280, { min: 80, max: 1200, description: "Maximum summary length." }),
    ],
    ["summary output must preserve source ids"],
  ),
  "intelligence.agent.score": contract(
    "intelligence.agent.score",
    "Importance Score",
    "items[] -> scoredItems[]",
    [port("in", "input", "items[]", true, "Consumes normalized or summarized items.")],
    [port("out", "output", "scoredItems[]", true, "Emits items with score fields.")],
    [
      param("threshold", "params", "number", true, 0.7, { min: 0, max: 1, description: "High-signal threshold." }),
      param("dimensions", "params", "string[]", true, ["market", "policy", "urgency"], {
        description: "Scoring dimensions used for explanation.",
      }),
    ],
    ["threshold must be between 0 and 1", "scored items must expose score"],
  ),
  "intelligence.agent.tag": contract(
    "intelligence.agent.tag",
    "Auto Tag",
    "items[] -> items[]",
    [port("in", "input", "items[]", true, "Consumes items.")],
    [port("out", "output", "items[]", true, "Emits tagged items.")],
    [
      param("taxonomy", "params", "string[]", true, ["macro", "fx", "commodity", "policy", "risk"], {
        description: "Allowed topic taxonomy.",
      }),
    ],
    ["taxonomy must not be empty"],
  ),
  "intelligence.router.importance": contract(
    "intelligence.router.importance",
    "Importance Router",
    "items[] -> review | notify",
    [port("in", "input", "items[]", true, "Consumes items with important/score fields.")],
    [
      port("review", "output", "items[]", true, "Sends reviewable items to inbox."),
      port("notify", "output", "items[]", true, "Sends high-signal items to notification."),
    ],
    [
      param("expression", "params", "string", true, "item.important === true || item.score >= 0.7", {
        description: "Boolean expression evaluated for routing.",
      }),
    ],
    ["router expression must be present", "router should have at least one downstream edge"],
  ),
  "intelligence.control.record-acceptance": contract(
    "intelligence.control.record-acceptance",
    "Record Acceptance Gate",
    "recordCandidate[] -> record[]",
    [port("candidates", "input", "recordCandidate[]", true, "Consumes normalized record candidates.")],
    [port("records", "output", "record[]", true, "Emits accepted records.")],
    [
      param("mode", "params", "string", true, "automatic_with_review", {
        enum: ["automatic_with_review", "manual_review", "automatic_strict"],
        description: "Acceptance policy for candidates that pass or need review.",
      }),
      param("schema", "params", "string", true, "record.v1", {
        description: "Record schema expected before acceptance.",
      }),
      param("dedupe", "params", "string", true, "required", {
        enum: ["required", "advisory", "off"],
        description: "Deduplication requirement before acceptance.",
      }),
      param("lineageRequired", "params", "boolean", true, true, {
        description: "Candidates without lineage cannot become records.",
      }),
      param("minQuality", "params", "number", false, 0, {
        min: 0,
        max: 1,
        description: "Minimum candidate quality score.",
      }),
    ],
    [
      "accepted records must satisfy schema and lineage requirements",
      "raw artifacts or candidates cannot bypass this gate into the record sink",
    ],
  ),
  "package.processing.record-hygiene": contract(
    "package.processing.record-hygiene",
    "Record Hygiene & Acceptance",
    "items[] -> record[]",
    [port("in", "input", "items[]", true, "Consumes raw source items for the default cleaning pipeline.")],
    [port("out", "output", "record[]", true, "Emits records accepted by the internal gate.")],
    [
      param("language", "params", "string", true, "zh-CN", { description: "Language metadata annotation; content is not translated." }),
      param("preserveSourceRefs", "params", "boolean", true, true, { description: "Preserves source references and lineage evidence." }),
      param("key", "params", "string", true, "title+source+publishedAt", { description: "Stable deduplication key expression." }),
      param("window", "params", "string", true, "24h", { description: "Deduplication time window." }),
      param("mode", "params", "string", true, "automatic_with_review", {
        enum: ["automatic_with_review", "manual_review", "automatic_strict"],
        description: "Record acceptance policy.",
      }),
      param("schema", "params", "string", true, "record.v1", { description: "Required output record schema." }),
      param("lineageRequired", "params", "boolean", true, true, { description: "Rejects candidates without lineage." }),
      param("minQuality", "params", "number", false, 0, { min: 0, max: 1, description: "Minimum accepted candidate quality." }),
    ],
    [
      "internal graph must remain Normalize -> Dedupe -> Record Acceptance Gate",
      "package parameters must bind to their canonical internal nodes",
      "accepted records must retain source lineage",
    ],
  ),
  "intelligence.output.inbox": contract(
    "intelligence.output.inbox",
    "Inbox Store",
    "items[] -> storedItems[]",
    [port("in", "input", "items[]", true, "Consumes reviewable items.")],
    [port("out", "output", "storedItems[]", false, "Emits stored item references for audit.")],
    [
      param("queue", "params", "string", true, "macro-watch", { description: "Inbox queue name." }),
      param("archive", "params", "boolean", false, true, { description: "Whether to archive stored items." }),
    ],
    ["queue must be present", "stored item ids must be traceable"],
  ),
  "intelligence.sink.records": contract(
    "intelligence.sink.records",
    "Record Sink",
    "record[] -> storedItems[]",
    [port("records", "input", "record[]", true, "Consumes accepted records only.")],
    [port("stored", "output", "storedItems[]", false, "Emits stored record references.")],
    [
      param("target", "params", "string", true, "records", {
        enum: ["records"],
        description: "Authoritative records system target.",
      }),
      param("writeMode", "params", "string", true, "append", {
        enum: ["append", "upsert"],
        description: "Record write mode.",
      }),
      param("preserveLineage", "params", "boolean", true, true, {
        description: "Stored records keep lineage and run trace pointers.",
      }),
      param("feishuWriteback", "params", "object", false, { enabled: false }, {
        description: "Optional guarded Feishu Sheets projection, including dynamic columns and mappings.",
      }),
    ],
    [
      "record sink only accepts records emitted by an acceptance gate",
      "stored records must keep lineage references",
    ],
  ),
  "intelligence.output.collection-result": contract(
    "intelligence.output.collection-result",
    "Collection Output",
    "recordCandidate[] -> items[]",
    [port("in", "input", "recordCandidate[]", true, "Consumes normalized package candidates.")],
    [port("out", "output", "items[]", false, "Exposes normalized package items with lineage.")],
    [
      param("queue", "params", "string", true, "opencli-hda-output", {
        description: "Internal artifact queue for package output.",
      }),
      param("archive", "params", "boolean", false, false, {
        description: "Whether the package output should be archived.",
      }),
    ],
    [
      "collection output must expose package items without credential params",
      "downstream nodes consume items through the package boundary",
    ],
  ),
  "intelligence.output.webhook": contract(
    "intelligence.output.webhook",
    "Webhook Notify",
    "items[] -> delivery",
    [port("in", "input", "items[]", true, "Consumes high-signal notification candidates.")],
    [port("out", "output", "delivery", false, "Emits a delivery preview or webhook result.")],
    [
      param("mode", "adapter.mode", "string", true, "mock", {
        enum: ["mock", "webhook"],
        description: "Notification adapter mode.",
      }),
      param("target", "adapter.config", "string", false, "operator-preview", { description: "Preview or webhook target." }),
      param("template", "params", "string", true, "brief", {
        enum: ["brief", "full", "headline"],
        description: "Notification payload template.",
      }),
    ],
    ["real sends require explicit permission", "delivery payload should be inspectable before send"],
  ),
  "intelligence.output.turbopush-publish": contract(
    "intelligence.output.turbopush-publish",
    "TurboPush Publish",
    "items[] -> delivery",
    [port("in", "input", "items[]", true, "Consumes publishable content or upstream items.")],
    [port("out", "output", "delivery", false, "Emits TurboPush publish result or blocked resource state.")],
    [
      param("mode", "adapter.mode", "string", true, "live", {
        enum: ["live"],
        description: "TurboPush runs through the local service/MCP bridge.",
      }),
      param("contentType", "params", "string", true, "graph_text", {
        enum: ["article", "graph_text", "video"],
        description: "TurboPush content type used to choose create_* and publish_* tools.",
      }),
      param("contentSource", "params", "string", true, "upstream", {
        enum: ["upstream", "inline", "existing_article"],
        description: "Where publishable content comes from.",
      }),
      param("title", "params", "string", true, "{{item.title}}", {
        description: "Title template or inline title.",
      }),
      param("targetPlatforms", "params", "string[]", true, ["xiaohongshu"], {
        description: "Platform intent. Logged accounts and platform settings are resolved by TurboPush.",
      }),
      param("accountSelector", "params", "string", true, "logged_accounts_by_platform", {
        enum: ["logged_accounts_by_platform", "all_logged"],
        description: "Account resolution strategy; account credentials stay in TurboPush.",
      }),
      param("platformSettings", "params", "object", false, {}, {
        description: "Implicit platform settings injected by runtime/agent assembly, not a user credential form.",
      }),
      param("syncDraft", "params", "boolean", false, false, {
        description: "When true, creates/syncs drafts instead of direct publish.",
      }),
    ],
    [
      "account/session/browser resources must be resolved through TurboPush, not node params",
      "publishing requires workflow send permission and local TurboPush service resource",
      "runtime must use list_logged_accounts, platform settings defaults, create_*, publish_*, and SSE result events",
    ],
  ),
  "package.opencli.multi-source-hda": contract(
    "package.opencli.multi-source-hda",
    "多站点采集执行",
    "trigger -> items[]",
    [port("in", "input", "trigger", true, "Consumes a workflow schedule trigger.")],
    [port("out", "output", "items[]", true, "Emits normalized items from locked OpenCLI internal sources.")],
    [
      param("template", "params", "string", true, "opencli-multi-source", {
        enum: ["opencli-multi-source"],
        description: "Locked package template id.",
      }),
      param("runtime", "params", "string", true, "iii", {
        enum: ["iii"],
        description: "Backend runtime plane used by OpenCLI source dispatch.",
      }),
      param("lockedInternals", "params", "boolean", true, true, {
        description: "Internal source graph is package-owned and not assembled by the web AI.",
      }),
      param("sources", "params", "object[]", true, [], {
        description: "Structured OpenCLI source slots. AI may select, add, remove, or fill source args here.",
      }),
      param("execution", "params", "object", true, { fanout: "parallel" }, {
        description: "Package execution policy. Source slots fan out in parallel by default.",
      }),
    ],
    [
      "package internals must include OpenCLI source slots generated from params.sources",
      "source slots must fan out in parallel before internal normalize",
      "OpenCLI dispatch must resolve through III/OpenCLI runtime resources",
    ],
  ),
}

export function getNodeContractByCatalogId(catalogId: string | undefined): NodeContract | undefined {
  return catalogId ? CONTRACTS[catalogId] : undefined
}

export function getNodeContract(node: WorkflowProjectNode | undefined): NodeContract | undefined {
  if (!node) return undefined
  return getNodeContractByCatalogId(
    typeof node.ui?.catalogId === "string" ? node.ui.catalogId : undefined,
  )
}

export function buildProjectContractReport(project: WorkflowProject): ProjectContractReport {
  const nodeContracts = project.nodes.flatMap((node) => {
    const contract = getNodeContract(node)
    if (!contract) return []
    return [{
      nodeId: node.id,
      contractId: contract.id,
      title: contract.title,
      ports: contract.ports,
      params: contract.params,
      assertions: contract.assertions,
    }]
  })
  const contractedIds = new Set(nodeContracts.map((entry) => entry.nodeId))
  const missingNodeIds = project.nodes.map((node) => node.id).filter((nodeId) => !contractedIds.has(nodeId))
  const findings = project.nodes.flatMap((node) => validateNodeContract(node, project.adapters.find((adapter) => adapter.id === node.adapter)))
  findings.push(...validateEdgeContracts(project))
  if (missingNodeIds.length > 0) {
    findings.push({
      nodeId: "*",
      contractId: "missing-contracts",
      status: "warn",
      summary: "Some workflow nodes do not have a variable/port contract.",
      evidence: { missingNodeIds },
    })
  }

  return {
    status: aggregateStatus(findings),
    nodeContracts,
    portCoverage: {
      nodesWithContracts: nodeContracts.length,
      totalNodes: project.nodes.length,
      percent: project.nodes.length === 0 ? 100 : roundMetric((nodeContracts.length / project.nodes.length) * 100),
      missingNodeIds,
    },
    findings,
  }
}

export function validateEdgeContracts(project: WorkflowProject): NodeContractFinding[] {
  return project.edges.flatMap((edge) => {
    const resolution = resolveEdgeContract(project, edge)
    const contractId = `edge:${edge.id}`
    if (!resolution.sourcePort) {
      return [finding(edge.source, contractId, "fail", `Edge "${edge.id}" has no compatible source output port.`, { edge, resolution })]
    }
    if (!resolution.targetPort) {
      return [finding(edge.target, contractId, "fail", `Edge "${edge.id}" has no compatible target input port.`, { edge, resolution })]
    }
    if (!resolution.compatible) {
      return [
        finding(edge.target, contractId, "fail", `Edge "${edge.id}" connects incompatible port types.`, {
          edge,
          sourceType: resolution.sourcePort.type,
          targetType: resolution.targetPort.type,
          resolution,
        }),
      ]
    }
    return []
  })
}

export function resolveEdgeContract(project: WorkflowProject, edge: WorkflowProjectEdge): EdgeContractResolution {
  const sourceNode = project.nodes.find((node) => node.id === edge.source)
  const targetNode = project.nodes.find((node) => node.id === edge.target)
  const sourceContract = getNodeContract(sourceNode)
  const targetContract = getNodeContract(targetNode)
  const outputs = sourceContract?.ports.filter((port) => port.direction === "output") ?? []
  const inputs = targetContract?.ports.filter((port) => port.direction === "input") ?? []
  const targetPort = edge.targetPort
    ? inputs.find((port) => (
      port.id === edge.targetPort || port.legacyIds?.includes(edge.targetPort ?? "")
    )) ?? null
    : inferTargetPort(inputs)
  const sourcePort = edge.sourcePort
    ? outputs.find((port) => port.id === edge.sourcePort) ?? null
    : inferSourcePort(outputs, edge, targetNode, targetPort)

  return {
    edgeId: edge.id,
    sourceNodeId: edge.source,
    targetNodeId: edge.target,
    sourcePort,
    targetPort,
    compatible: Boolean(sourcePort && targetPort && portTypesCompatible(sourcePort.type, targetPort.type)),
    explicit: {
      sourcePort: Boolean(edge.sourcePort),
      targetPort: Boolean(edge.targetPort),
    },
  }
}

export function validateNodeContract(node: WorkflowProjectNode, adapter?: AdapterBinding): NodeContractFinding[] {
  const contract = getNodeContract(node)
  if (!contract) {
    return [{
      nodeId: node.id,
      contractId: "unknown",
      status: "warn",
      summary: "Node has no registered variable/port contract.",
      evidence: { kind: node.kind, capability: node.capability, adapter: node.adapter },
    }]
  }

  const paramFindings = contract.params.flatMap((paramSpec) => {
    const value = readParamValue(node, adapter, paramSpec)
    if ((value === undefined || value === "") && paramSpec.required) {
      return [finding(node.id, contract.id, "fail", `Required param "${paramSpec.id}" is missing.`, { param: paramSpec })]
    }
    if (value === undefined || value === "") return []
    if (!matchesType(value, paramSpec.type)) {
      return [finding(node.id, contract.id, "fail", `Param "${paramSpec.id}" should be ${paramSpec.type}.`, { value, param: paramSpec })]
    }
    if (paramSpec.type === "number" && typeof value === "number") {
      if (typeof paramSpec.min === "number" && value < paramSpec.min) {
        return [finding(node.id, contract.id, "fail", `Param "${paramSpec.id}" is below minimum.`, { value, min: paramSpec.min })]
      }
      if (typeof paramSpec.max === "number" && value > paramSpec.max) {
        return [finding(node.id, contract.id, "fail", `Param "${paramSpec.id}" is above maximum.`, { value, max: paramSpec.max })]
      }
    }
    if (paramSpec.enum && typeof value === "string" && !paramSpec.enum.includes(value)) {
      return [finding(node.id, contract.id, "fail", `Param "${paramSpec.id}" is outside allowed options.`, { value, allowed: paramSpec.enum })]
    }
    if (paramSpec.type === "string[]" && Array.isArray(value) && value.length === 0 && paramSpec.required) {
      return [finding(node.id, contract.id, "fail", `Param "${paramSpec.id}" must not be empty.`, { value })]
    }
    return []
  })
  return [...paramFindings, ...validateCollectorNode(node, contract)]
}

function collectorContract(kind: "web" | "api" | "rss" | "cli", title: string): NodeContract {
  return contract(
    `collection.source.${kind}`,
    title,
    "trigger -> CollectorOutputV1 { items[], sourceResults[] }",
    [port("in", "input", "trigger", false, "Optionally consumes a workflow trigger.")],
    [port("out", "output", "CollectorOutputV1", true, "Emits items and sourceResults as one typed envelope.")],
    [
      param("version", "params", "number", true, 1, {
        min: 1,
        max: 1,
        description: "Collector node contract version.",
      }),
      param("execution", "params", "object", true, {}, {
        description: "Concurrency, timeout, and retry policy.",
      }),
      param("sources", "params", "object[]", true, [], {
        description: `Ordered ${kind} source definitions with stable sourceId values.`,
      }),
    ],
    [
      `every source kind must be ${kind}`,
      "disabled sources are preserved and reported as skipped",
      "publishedAt and fetchedAt must remain distinct",
      ...(kind === "cli"
        ? ["CLI sources select registered adapterNodeId values and structured typed args; free shell text is forbidden"]
        : []),
    ],
  )
}

const FORBIDDEN_COLLECTOR_KEYS = new Set([
  "apikey",
  "xapikey",
  "accesstoken",
  "refreshtoken",
  "authtoken",
  "bearertoken",
  "clientsecret",
  "secret",
  "shell",
  "commandline",
  "scripttext",
  "rawcommand",
  "token",
  "password",
  "cookie",
  "authorization",
])

function validateCollectorNode(
  node: WorkflowProjectNode,
  nodeContract: NodeContract,
): NodeContractFinding[] {
  if (!nodeContract.id.startsWith("collection.source.")) return []
  const expectedKind = nodeContract.id.slice("collection.source.".length)
  const sources = node.params.sources
  if (!Array.isArray(sources)) return []
  const findings: NodeContractFinding[] = []
  const sourceIds = new Set<string>()
  sources.forEach((source, index) => {
    const evidence = { index, source }
    if (!source || typeof source !== "object" || Array.isArray(source)) return
    const candidate = source as Record<string, unknown>
    if (candidate.kind !== expectedKind) {
      findings.push(finding(
        node.id,
        nodeContract.id,
        "fail",
        `Source ${index + 1} must have kind "${expectedKind}".`,
        evidence,
      ))
    }
    const sourceId = typeof candidate.sourceId === "string" ? candidate.sourceId.trim() : ""
    if (!sourceId) {
      findings.push(finding(
        node.id,
        nodeContract.id,
        "fail",
        `Source ${index + 1} needs a stable sourceId.`,
        evidence,
      ))
    } else if (sourceIds.has(sourceId)) {
      findings.push(finding(
        node.id,
        nodeContract.id,
        "fail",
        `Source id "${sourceId}" is duplicated.`,
        evidence,
      ))
    } else {
      sourceIds.add(sourceId)
    }
    if (containsForbiddenCollectorKey(candidate)) {
      findings.push(finding(
        node.id,
        nodeContract.id,
        "fail",
        `Source "${sourceId || index + 1}" contains a forbidden secret or free-command field.`,
        evidence,
      ))
    }
    if (expectedKind === "cli") {
      if (typeof candidate.adapterNodeId !== "string" || !candidate.adapterNodeId.trim()) {
        findings.push(finding(
          node.id,
          nodeContract.id,
          "fail",
          `CLI source "${sourceId || index + 1}" must select adapterNodeId.`,
          evidence,
        ))
      }
      if (!candidate.args || typeof candidate.args !== "object" || Array.isArray(candidate.args)) {
        findings.push(finding(
          node.id,
          nodeContract.id,
          "fail",
          `CLI source "${sourceId || index + 1}" must use structured typed args.`,
          evidence,
        ))
      }
    }
  })
  return findings
}

function containsForbiddenCollectorKey(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  if (Array.isArray(value)) return value.some(containsForbiddenCollectorKey)
  return Object.entries(value as Record<string, unknown>).some(
    ([key, nested]) => (
      FORBIDDEN_COLLECTOR_KEYS.has(normalizeCollectorKey(key)) ||
      containsForbiddenCollectorKey(nested)
    ),
  )
}

function normalizeCollectorKey(key: string): string {
  return [...key.toLowerCase()]
    .filter((character) => /[a-z0-9]/.test(character))
    .join("")
}

function contract(
  id: string,
  title: string,
  dataModel: string,
  inputs: PortContract[],
  outputs: PortContract[],
  params: ParamContract[],
  assertions: string[],
): NodeContract {
  return { id, title, dataModel, ports: [...inputs, ...outputs], params, assertions }
}

function dataOperatorContract(id: string, title: string): NodeContract {
  return contract(
    id,
    title,
    "recordCandidate[] -> recordCandidate[]",
    [port("in", "input", "recordCandidate[]", true, "Consumes record candidates with lineage.")],
    [port("out", "output", "recordCandidate[]", true, "Emits transformed candidates with lineage.")],
    [
      param("operatorId", "params", "string", true, "", {
        description: "Versioned operator id selected from the backend capability manifest.",
      }),
      param("packVersion", "params", "string", true, "", {
        description: "Pinned operator pack version; persisted workflows never auto-upgrade it.",
      }),
      param("config", "params", "object", false, {}, {
        description: "Operator-specific JSON configuration.",
      }),
    ],
    [
      "operatorId must resolve to a backend manifest operator of the matching kind",
      "candidate lineage must survive the operator boundary",
    ],
  )
}

function port(
  id: string,
  direction: PortDirection,
  type: PortDataType,
  required: boolean,
  description: string,
  options: Pick<PortContract, "cardinality" | "minConnections" | "legacyIds"> = {},
): PortContract {
  return { id, direction, type, required, description, ...options }
}

function param(
  id: string,
  source: ParamContract["source"],
  type: ParamDataType,
  required: boolean,
  defaultValue: unknown,
  options: Omit<ParamContract, "id" | "source" | "type" | "required" | "defaultValue">,
): ParamContract {
  return { id, source, type, required, defaultValue, ...options }
}

function readParamValue(node: WorkflowProjectNode, adapter: AdapterBinding | undefined, paramSpec: ParamContract): unknown {
  if (paramSpec.source === "adapter.mode") return adapter?.mode
  if (paramSpec.source === "adapter.config") return adapter?.config[paramSpec.id]
  return node.params[paramSpec.id]
}

function matchesType(value: unknown, type: ParamDataType): boolean {
  if (type === "string[]") return Array.isArray(value) && value.every((item) => typeof item === "string")
  if (type === "object") return Boolean(value) && typeof value === "object" && !Array.isArray(value)
  if (type === "object[]") return Array.isArray(value) && value.every((item) => Boolean(item) && typeof item === "object" && !Array.isArray(item))
  return typeof value === type
}

function finding(
  nodeId: string,
  contractId: string,
  status: ContractStatus,
  summary: string,
  evidence: Record<string, unknown>,
): NodeContractFinding {
  return { nodeId, contractId, status, summary, evidence }
}

function aggregateStatus(findings: NodeContractFinding[]): ContractStatus {
  if (findings.some((finding) => finding.status === "fail")) return "fail"
  if (findings.some((finding) => finding.status === "warn")) return "warn"
  return "pass"
}

function roundMetric(value: number): number {
  return Math.round(value * 1000) / 1000
}

function inferTargetPort(inputs: PortContract[]): PortContract | null {
  if (inputs.length === 0) return null
  return inputs.find((port) => port.required) ?? inputs[0]
}

function inferSourcePort(
  outputs: PortContract[],
  edge: WorkflowProjectEdge,
  targetNode: WorkflowProjectNode | undefined,
  targetPort: PortContract | null,
): PortContract | null {
  if (outputs.length === 0) return null
  if (outputs.length === 1) return outputs[0]

  const label = `${edge.label ?? ""} ${edge.condition ?? ""}`.toLowerCase()
  if (label.includes("review") || targetNode?.kind === "inbox") {
    return outputs.find((port) => port.id === "review") ?? outputs[0]
  }
  if (label.includes("notify") || label.includes("webhook") || targetNode?.kind === "notify") {
    return outputs.find((port) => port.id === "notify") ?? outputs[0]
  }
  if (targetPort) {
    const compatible = outputs.find((port) => portTypesCompatible(port.type, targetPort.type))
    if (compatible) return compatible
  }
  return outputs.find((port) => port.required) ?? outputs[0]
}

function portTypesCompatible(source: PortDataType, target: PortDataType): boolean {
  if (source === target) return true
  if (
    target === "CollectorMergeInputV1" &&
    (source === "CollectorOutputV1" || source === "recordCandidate[]")
  ) return true
  if (source === "unknown" || target === "unknown") return true
  return false
}
