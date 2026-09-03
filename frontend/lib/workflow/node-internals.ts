import type { WorkflowProjectNode } from "./schema"
import type { ParameterBinding, ParameterFieldType } from "@/lib/flow/types"

export type NodeInternalStatus = "ready" | "simulated" | "future"

export type NodeInternalExposedParam = {
  id: string
  label: string
  groupId: string
  groupLabel: string
  type: ParameterFieldType
  binding?: Omit<ParameterBinding, "nodeId">
  description?: string
  order?: number
  groupOrder?: number
  readonly?: boolean
  optional?: boolean
  allowCustom?: boolean
  value?: unknown
  placeholder?: string
  min?: number
  max?: number
  step?: number
  options?: { value: string; label: string }[]
}

export type NodeInternalStep = {
  id: string
  label: string
  capability: string
  description: string
  evidence: string
  status: NodeInternalStatus
  exposedParams?: NodeInternalExposedParam[]
}

export type NodeInternals = {
  title: string
  summary: string
  steps: NodeInternalStep[]
}

const NODE_INTERNALS: Record<string, NodeInternals> = {
  "intelligence.input.collection-need": {
    title: "Collection Need Internals",
    summary: "Captures a business collection need and turns it into a reviewable patch over real catalog nodes.",
    steps: [
      step("input", "Need capture", "input", "Reads the user's collection need from node.params.text.", "params.text", "ready", [
        exposedParam("text", "Need", "input", "Input", "textarea", "抓小红书热帖", {
          order: 1,
          placeholder: "抓小红书热帖",
        }),
      ]),
      step("draft", "Patch draft", "assemble", "Calls the demand-draft endpoint to assemble existing real nodes.", "/api/v1/workflows/demand-draft", "ready", [
        exposedParam("locale", "Locale", "input", "Input", "select", "zh-CN", {
          options: [
            { value: "zh-CN", label: "zh-CN" },
            { value: "en-US", label: "en-US" },
          ],
          order: 2,
        }),
      ]),
      step("review", "Proposal review", "review", "Applies accepted changes through the global Agent confirmation flow.", "Global Agent patch", "ready"),
    ],
  },
  "intelligence.schedule.cron": {
    title: "Schedule Internals",
    summary: "Turns wall-clock intent into a deterministic workflow trigger.",
    steps: [
      step("interval", "Interval parser", "parse", "Reads interval/cron-like params.", "next tick preview", "ready", [
        exposedParam("interval", "Interval", "transform", "Transform", "text", "5m", { order: 1 }),
      ]),
      step("timezone", "Timezone resolver", "resolve", "Applies the workflow timezone.", "settings.timezone", "ready", [
        exposedParam("timezone", "Timezone", "transform", "Transform", "select", "Asia/Shanghai", {
          options: timezoneOptions(),
          order: 2,
        }),
      ]),
      step("gate", "Enabled gate", "guard", "Allows future pause/resume behavior.", "enabled flag", "future", [
        exposedParam("enabled", "Enabled", "misc", "Misc", "boolean", true, { groupOrder: 3 }),
      ]),
    ],
  },
  "intelligence.source.jin10": {
    title: "JIN10 Internals",
    summary: "Fetches and normalizes JIN10 flash news behind one source node.",
    steps: [
      step("fetch", "Fetch feed", "fetch", "Reads fixture data or live feed by adapter mode.", "item count", "ready", [
        exposedParam("mode", "Mode", "source", "Source", "select", "fixture", {
          binding: { source: "adapter", fieldId: "mode" },
          options: [
            { value: "fixture", label: "Fixture" },
            { value: "mock", label: "Mock" },
            { value: "live", label: "Live" },
          ],
          order: 1,
        }),
        exposedParam("feed", "Feed", "source", "Source", "text", "kuaixun", {
          binding: { source: "adapter", fieldId: "feed" },
          order: 2,
        }),
      ]),
      step("parse", "Parse payload", "parse", "Maps provider payload fields into local source items.", "schema parse", "ready", [
        exposedParam("channel", "Channel", "source", "Source", "text", "kuaixun", { order: 3 }),
      ]),
      step("filter", "Importance filter", "filter", "Applies importantOnly and limit params.", "filtered count", "ready", [
        exposedParam("limit", "Limit", "transform", "Transform", "number", 20, { min: 1, max: 200, step: 1, groupOrder: 2, order: 1 }),
        exposedParam("importantOnly", "Important Only", "transform", "Transform", "boolean", false, { groupOrder: 2, order: 2 }),
      ]),
      step("cache", "Cache window", "cache", "Provides a stable slot for dedupe/cache policy.", "cache key", "future"),
      step("errors", "Source errors", "guard", "Converts provider failures into adapter errors.", "error message", "ready"),
      step("output", "Output schema", "validate", "Ensures downstream receives items[].", "items[] contract", "simulated"),
    ],
  },
  "intelligence.source.rss": {
    title: "RSS / Atom Reader Internals",
    summary: "Reads an existing feed through the backend RSS channel and preserves its business source group in lineage.",
    steps: [
      step("feed", "Feed binding", "fetch", "Reads the configured RSS or Atom URL through the guarded network client.", "params.feedUrl", "ready", [
        exposedParam("feedUrl", "Feed URL", "source", "Source", "text", "https://www.federalreserve.gov/feeds/press_all.xml", { order: 1 }),
        exposedParam("sourceGroup", "Source Group", "source", "Source", "text", "macro-policy", { order: 2 }),
      ]),
      step("parse", "Parse RSS / Atom", "parse", "Maps feed entries to stable source items.", "RSSChannel", "ready"),
      step("limit", "Entry limit", "filter", "Caps entries by node and project run limits.", "params.maxEntries", "ready", [
        exposedParam("maxEntries", "Max Entries", "transform", "Transform", "number", 20, { min: 1, max: 500, step: 1, groupOrder: 2, order: 1 }),
      ]),
      step("output", "Output with lineage", "validate", "Returns items[] with sourceGroup and live RSS lineage.", "items[] contract", "ready"),
    ],
  },
  "intelligence.source.rsshub": {
    title: "RSSHub Reader Internals",
    summary: "Resolves a managed RSSHub Provider and route, then reads its generated feed through the same RSS runtime.",
    steps: [
      step("provider", "RSSHub Provider", "guard", "Resolves the selected RSSHub connection without exposing its credential.", "params.providerId", "ready", [
        exposedParam("providerId", "Provider ID", "source", "Source", "text", "", { order: 1 }),
        exposedParam("route", "RSSHub Route", "source", "Source", "text", "", {
          order: 2,
          placeholder: "/route/path",
        }),
        exposedParam("sourceGroup", "Source Group", "source", "Source", "text", "rsshub", { order: 3 }),
      ]),
      step("generate", "Generate feed URL", "resolve", "Builds the route URL with backend-only Provider credentials and non-secret parameters.", "RSSHub Provider", "ready"),
      step("read", "Read generated feed", "fetch", "Fetches and parses the generated RSS response.", "RSSChannel", "ready"),
      step("limit", "Entry limit", "filter", "Caps entries by node and project run limits.", "params.maxEntries", "ready", [
        exposedParam("maxEntries", "Max Entries", "transform", "Transform", "number", 20, { min: 1, max: 500, step: 1, groupOrder: 2, order: 1 }),
      ]),
      step("output", "Output with lineage", "validate", "Returns items[] with RSSHub route lineage.", "items[] contract", "ready"),
    ],
  },
  "intelligence.source.rss-bridge": {
    title: "RSS-Bridge Reader Internals",
    summary: "Resolves a managed RSS-Bridge Provider and bridge, then reads the generated feed through the RSS runtime.",
    steps: [
      step("provider", "RSS-Bridge Provider", "guard", "Resolves the selected RSS-Bridge connection without exposing its credential.", "params.providerId", "ready", [
        exposedParam("providerId", "Provider ID", "source", "Source", "text", "", { order: 1 }),
        exposedParam("bridge", "Bridge", "source", "Source", "text", "", {
          order: 2,
          placeholder: "Bridge name",
        }),
        exposedParam("sourceGroup", "Source Group", "source", "Source", "text", "rss-bridge", { order: 3 }),
      ]),
      step("generate", "Generate feed URL", "resolve", "Builds the bridge URL with backend-only Provider credentials and non-secret parameters.", "RSS-Bridge Provider", "ready"),
      step("read", "Read generated feed", "fetch", "Fetches and parses the generated RSS response.", "RSSChannel", "ready"),
      step("limit", "Entry limit", "filter", "Caps entries by node and project run limits.", "params.maxEntries", "ready", [
        exposedParam("maxEntries", "Max Entries", "transform", "Transform", "number", 20, { min: 1, max: 500, step: 1, groupOrder: 2, order: 1 }),
      ]),
      step("output", "Output with lineage", "validate", "Returns items[] with RSS-Bridge lineage.", "items[] contract", "ready"),
    ],
  },
  "intelligence.source.http": {
    title: "HTTP / API Reader Internals",
    summary: "Runs a guarded JSON request and exposes the selected response items to the same multi-source workflow.",
    steps: [
      step("request", "HTTP request", "fetch", "Executes an allowed GET or POST request through the guarded HTTP client.", "params.url", "ready", [
        exposedParam("url", "Endpoint URL", "source", "Source", "text", "", {
          order: 1,
          placeholder: "https://api.example.cn/items",
        }),
        exposedParam("method", "Method", "source", "Source", "select", "GET", {
          order: 2,
          options: [
            { value: "GET", label: "GET" },
            { value: "POST", label: "POST" },
          ],
        }),
        exposedParam("sourceGroup", "Source Group", "source", "Source", "text", "http-api", { order: 3 }),
      ]),
      step("select", "Result selector", "parse", "Selects an optional dot-separated result path from the JSON payload.", "params.resultPath", "ready", [
        exposedParam("resultPath", "Result Path", "transform", "Transform", "text", "", {
          groupOrder: 2,
          order: 1,
          placeholder: "data.items",
        }),
      ]),
      step("guard", "Network guard", "guard", "Checks the domain allow-list, response status, JSON shape, and size limit.", "agentPermissions.allowedDomains", "ready"),
      step("output", "Output with lineage", "validate", "Returns items[] with endpoint and source-group lineage.", "items[] contract", "ready"),
    ],
  },
  "intelligence.source.doubao-research": {
    title: "Doubao Research Internals",
    summary: "Asks each upstream question in the persistent Doubao browser and captures enriched visible evidence.",
    steps: [
      step("question", "Question binding", "input", "Interpolates the upstream keyword into the question template.", "params.question", "ready", [
        exposedParam("question", "Question", "source", "Source", "textarea", "{{keyword}}", { order: 1 }),
        exposedParam("site_session", "Session", "source", "Source", "select", "persistent", {
          options: [
            { value: "persistent", label: "Persistent" },
            { value: "ephemeral", label: "Ephemeral" },
          ],
          order: 2,
        }),
      ]),
      step("runtime", "Local browser runtime", "dispatch", "Uses the connected BBX/Codex/Claude runtime without requiring a network model provider.", "Agent runtime", "ready", [
        exposedParam("executionMode", "Execution Mode", "runtime", "Runtime", "select", "agent", {
          options: [
            { value: "agent", label: "Local Agent" },
            { value: "channel", label: "Legacy Channel" },
          ],
          groupOrder: 2,
          order: 1,
        }),
        exposedParam("agentRuntime", "Agent Runtime", "runtime", "Runtime", "select", "bbx", {
          options: [
            { value: "bbx", label: "BBX" },
            { value: "codex", label: "Codex" },
            { value: "claude-code", label: "Claude Code" },
          ],
          groupOrder: 2,
          order: 2,
        }),
      ]),
      step("evidence", "Enriched evidence", "extract", "Captures the full answer, search keywords and counts, references, follow-ups, product links, and video cards.", "gaojixing.capture-evidence.v1", "ready"),
    ],
  },
  "intelligence.source.opencli-slot": {
    title: "OpenCLI Source Slot Internals",
    summary: "A source slot keeps package-selected OpenCLI command params structured while delegating execution to the runtime resource resolver.",
    steps: [
      step("site", "Site binding", "fetch", "Selects the OpenCLI adapter site.", "params.site", "ready", [
        exposedParam("site", "Site", "source", "Source", "text", "bilibili", { readonly: true, order: 1 }),
        exposedParam("command", "Command", "source", "Source", "text", "search", { readonly: true, order: 2 }),
      ]),
      step("args", "Command args", "parse", "Carries planner-filled command args without flattening them into credential fields.", "params.args", "ready"),
      step("dispatch", "Runtime dispatch", "queue", "Queues the source request through OpenCLI runtime resources.", "runtime binding", "ready"),
      step("output", "Output schema", "validate", "Returns items[] with sourceGroup and source references.", "items[] contract", "simulated"),
    ],
  },
  "intelligence.source.pool": {
    title: "Source Pool Internals",
    summary: "Projects package demand into a parallel source-slot fanout without exposing credential or browser-resource fields.",
    steps: [
      step("registry", "Source registry projection", "select", "Reads the package-owned source slot set.", "params.sourceGroups", "ready"),
      step("fanout", "Parallel fanout", "route", "Emits one trigger per materialized OpenCLI source slot.", "fanout=parallel", "ready", [
        exposedParam("sourceCount", "Source Count", "source", "Source", "number", 0, {
          readonly: true,
          min: 0,
          step: 1,
          order: 1,
        }),
        exposedParam("fanout", "Fanout", "source", "Source", "text", "parallel", {
          readonly: true,
          order: 2,
        }),
      ]),
      step("resources", "Resource guard", "guard", "Leaves session identity and execution capacity resolution to backend runtime resources.", "runtime resource resolver", "ready"),
    ],
  },
  "intelligence.agent.summary": {
    title: "Summary Internals",
    summary: "Builds a brief while preserving source evidence.",
    steps: [
      step("prompt", "Prompt preset", "prompt", "Selects macro/risk/headline prompt style.", "style param", "ready", [
        exposedParam("style", "Style", "render", "Render", "select", "macro-brief", {
          options: [
            { value: "macro-brief", label: "Macro Brief" },
            { value: "risk-note", label: "Risk Note" },
            { value: "headline", label: "Headline" },
          ],
          order: 1,
        }),
      ]),
      step("model", "Model adapter", "model", "Routes to the selected LLM provider.", "model param", "simulated", [
        exposedParam("model", "Model", "render", "Render", "text", "deepseek", { order: 2 }),
      ]),
      step("budget", "Token budget", "budget", "Constrains summary length.", "maxChars", "ready", [
        exposedParam("maxChars", "Max Chars", "render", "Render", "number", 280, { min: 80, max: 2000, step: 10, order: 3 }),
      ]),
      step("schema", "Output schema", "validate", "Keeps generated summaries machine-readable.", "summary fields", "future"),
      step("fallback", "Fallback summary", "fallback", "Keeps deterministic simulation useful without a live model.", "local summary", "simulated"),
      step("refs", "Evidence refs", "evidence", "Carries source references into the brief.", "source ids", "future"),
    ],
  },
  "intelligence.processing.normalize": {
    title: "Normalize Internals",
    summary: "Turns provider-shaped items into the local intelligence item shape.",
    steps: [
      step("map-fields", "Map fields", "parse", "Maps provider fields into local item keys.", "field mapping", "ready"),
      step("language", "Language annotation", "annotate", "Records the requested language as metadata; it does not translate content.", "language annotation", "ready", [
        exposedParam("language", "Language Annotation", "transform", "Transform", "select", "zh-CN", {
          options: [
            { value: "zh-CN", label: "zh-CN" },
            { value: "en-US", label: "en-US" },
          ],
        }),
      ]),
      step("source-refs", "Source refs", "evidence", "Preserves source id and URL for audit.", "source refs", "ready", [
        exposedParam("preserveSourceRefs", "Preserve Source Refs", "misc", "Misc", "boolean", true, { groupOrder: 3 }),
      ]),
      step("schema", "Item schema", "validate", "Ensures downstream receives items[].", "items[] contract", "simulated"),
    ],
  },
  "intelligence.processing.dedupe": {
    title: "Dedupe Internals",
    summary: "Removes duplicate source items while keeping audit evidence.",
    steps: [
      step("key", "Dedupe key", "parse", "Builds a stable key from title/source/time fields.", "dedupe key", "ready", [
        exposedParam("key", "Key", "transform", "Transform", "text", "title+source+publishedAt", { order: 1 }),
      ]),
      step("window", "Batch window comparison", "compare", "Compares records inside the current input batch using the configured time window; it does not persist cross-run state.", "batch comparison metrics", "ready", [
        exposedParam("window", "Window", "transform", "Transform", "text", "24h", { order: 2 }),
      ]),
      step("filter", "Unique filter", "filter", "Keeps first-seen items and drops repeats.", "unique count", "simulated"),
      step("coverage", "Drop coverage", "validate", "Records dropped item evidence.", "dropped ids", "future"),
    ],
  },
  "intelligence.data.generate": dataOperatorInternals("Generate Data", "core.generate.instruction-pairs"),
  "intelligence.data.filter": dataOperatorInternals("Filter Data", "core.filter.quality"),
  "intelligence.data.evaluate": dataOperatorInternals("Evaluate Data", "core.evaluate.quality"),
  "intelligence.data.refine": dataOperatorInternals("Refine Data", "core.refine.text"),
  "intelligence.agent.score": {
    title: "Scoring Internals",
    summary: "Turns raw items into ranked signals with an explainable threshold.",
    steps: [
      step("dimensions", "Dimensions", "score", "Chooses scoring axes such as market, policy, and urgency.", "dimensions", "ready", [
        exposedParam("dimensions", "Dimensions", "transform", "Transform", "tokens", ["market", "policy", "urgency"], {
          options: [
            { value: "market", label: "market" },
            { value: "policy", label: "policy" },
            { value: "urgency", label: "urgency" },
            { value: "liquidity", label: "liquidity" },
          ],
          order: 1,
        }),
      ]),
      step("weights", "Weights", "weight", "Reserves a slot for weighted scoring.", "weight map", "future"),
      step("threshold", "Threshold", "threshold", "Defines the cutoff for high-signal items.", "threshold", "ready", [
        exposedParam("threshold", "Threshold", "contract", "Contract", "slider", 0.7, {
          min: 0,
          max: 1,
          step: 0.05,
          groupOrder: 2,
          order: 1,
        }),
      ]),
      step("calibration", "Calibration sample", "calibrate", "Compares scores against sample items.", "sample set", "future"),
      step("explanation", "Score explanation", "explain", "Explains why an item crossed the threshold.", "reason text", "simulated"),
      step("confidence", "Confidence", "confidence", "Separates score from certainty.", "confidence field", "future"),
    ],
  },
  "intelligence.agent.tag": {
    title: "Tag Internals",
    summary: "Classifies items into a controlled taxonomy.",
    steps: [
      step("taxonomy", "Taxonomy", "parse", "Loads the allowed tag vocabulary.", "taxonomy param", "ready", [
        exposedParam("taxonomy", "Taxonomy", "transform", "Transform", "tokens", ["macro", "fx", "commodity", "policy", "risk"], {
          options: [
            { value: "macro", label: "macro" },
            { value: "fx", label: "fx" },
            { value: "commodity", label: "commodity" },
            { value: "policy", label: "policy" },
            { value: "risk", label: "risk" },
          ],
          order: 1,
        }),
      ]),
      step("classifier", "Classifier", "model", "Assigns topic and risk labels.", "tag candidates", "simulated"),
      step("filter", "Allowed tags", "filter", "Drops tags outside the approved taxonomy.", "allowed tags", "ready"),
      step("schema", "Tag schema", "validate", "Keeps tags machine-readable downstream.", "tag fields", "future"),
    ],
  },
  "intelligence.router.importance": {
    title: "Router Internals",
    summary: "Routes scored items into review and notification paths.",
    steps: [
      step("expression", "Condition expression", "route", "Evaluates the route expression against each item.", "expression result", "ready", [
        exposedParam("expression", "Expression", "contract", "Contract", "textarea", "item.important === true || item.score >= 0.7", {
          description: "Route predicate evaluated against each scored item.",
          order: 1,
        }),
      ]),
      step("branches", "Branch list", "branch", "Maps true/false outputs to downstream edges.", "edge labels", "ready"),
      step("default", "Default branch", "fallback", "Keeps unmatched items recoverable.", "default route", "future"),
      step("preview", "Route preview", "preview", "Shows how sample items would route.", "simulation trace", "simulated"),
      step("warnings", "Missing branch warning", "validate", "Flags routers without expected outputs.", "edge count", "ready"),
    ],
  },
  "intelligence.control.record-acceptance": {
    title: "Record Acceptance Gate Internals",
    summary: "Accepts only canonical record candidates that satisfy schema, lineage, dedupe, and quality policy.",
    steps: [
      step("policy", "Acceptance policy", "accept", "Selects automatic, reviewed, or strict acceptance.", "accepted record[]", "ready", [
        exposedParam("mode", "Mode", "acceptance", "Acceptance", "select", "automatic_with_review", {
          options: [
            { value: "automatic_with_review", label: "Automatic With Review" },
            { value: "manual_review", label: "Manual Review" },
            { value: "automatic_strict", label: "Automatic Strict" },
          ],
          order: 1,
        }),
        exposedParam("schema", "Schema", "acceptance", "Acceptance", "text", "record.v1", { order: 2 }),
        exposedParam("dedupe", "Dedupe", "acceptance", "Acceptance", "select", "required", {
          options: [
            { value: "required", label: "Required" },
            { value: "advisory", label: "Advisory" },
            { value: "off", label: "Off" },
          ],
          order: 3,
        }),
      ]),
      step("evidence", "Lineage and quality", "validate", "Rejects candidates that lack required lineage or quality.", "validation evidence", "ready", [
        exposedParam("lineageRequired", "Lineage Required", "evidence", "Evidence", "boolean", true, { groupOrder: 2, order: 1 }),
        exposedParam("minQuality", "Minimum Quality", "evidence", "Evidence", "slider", 0, {
          groupOrder: 2,
          order: 2,
          min: 0,
          max: 1,
          step: 0.05,
        }),
      ]),
    ],
  },
  "intelligence.sink.records": {
    title: "Record Sink Internals",
    summary: "Stores authoritative records first, then optionally projects verified rows to a configured Feishu sheet.",
    steps: [
      step("store", "Authoritative record store", "store", "Persists accepted records with lineage before any external projection.", "stored refs", "ready", [
        exposedParam("target", "Target", "storage", "Storage", "text", "records", { readonly: true, order: 1 }),
        exposedParam("writeMode", "Write Mode", "storage", "Storage", "select", "append", {
          options: [
            { value: "append", label: "Append" },
            { value: "upsert", label: "Upsert" },
          ],
          order: 2,
        }),
        exposedParam("preserveLineage", "Preserve Lineage", "storage", "Storage", "boolean", true, { order: 3 }),
      ]),
      step("feishu", "Feishu sheet projection", "send", "Uses a guarded host bridge, stable row identity, and full-row readback verification.", "verified sheet rows", "ready", [
        exposedParam("feishuWriteback", "Feishu Writeback (JSON)", "projection", "Projection", "json", { enabled: false }, {
          description: "Configure enabled, target sheet, dynamic columns, and optional columnMapping.",
          groupOrder: 2,
          order: 1,
        }),
      ]),
    ],
  },
  "intelligence.output.webhook": {
    title: "Notify Internals",
    summary: "Binds outbound delivery to the guarded backend webhook notifier.",
    steps: [
      step("template", "Template", "format", "Formats brief/headline/full payloads.", "template param", "ready", [
        exposedParam("template", "Template", "render", "Render", "select", "brief", {
          options: [
            { value: "brief", label: "brief" },
            { value: "full", label: "full" },
            { value: "headline", label: "headline" },
          ],
          order: 1,
        }),
      ]),
      step("target", "Target", "target", "Selects the real webhook notifier target.", "target param", "ready", [
        exposedParam("target", "Target", "runtime", "Runtime", "select", "webhook", {
          options: notificationTargetOptions(),
          order: 1,
          groupOrder: 2,
        }),
        exposedParam("mode", "Adapter Mode", "runtime", "Runtime", "select", "webhook", {
          binding: { source: "adapter", fieldId: "mode" },
          options: [
            { value: "webhook", label: "webhook" },
          ],
          order: 2,
          groupOrder: 2,
        }),
      ]),
      step("payload", "Payload draft", "preview", "Shows exactly what the notifier sink will receive.", "payload sample", "ready"),
      step("binding", "Notifier sink", "send", "Binds to backend WebhookNotifier guarded delivery.", "workflow.notifier.webhook.send", "ready"),
      step("retry", "Retry policy", "retry", "Reserves retry behavior for webhook delivery.", "retry policy", "future"),
      step("guard", "Delivery guard", "guard", "Checks permissions and URL configuration before sends.", "agentPermissions", "ready"),
    ],
  },
  "intelligence.output.turbopush-publish": {
    title: "TurboPush Publish Internals",
    summary: "Publishes articles, graph-text, or videos through the local TurboPush service and logged accounts.",
    steps: [
      step("content", "Content binding", "format", "Chooses content type and reads publishable content from upstream or inline params.", "contentType/contentSource", "ready", [
        exposedParam("contentType", "Content Type", "publish", "Publish", "select", "graph_text", {
          options: turboPushContentTypeOptions(),
          order: 1,
        }),
        exposedParam("contentSource", "Content Source", "publish", "Publish", "select", "upstream", {
          options: [
            { value: "upstream", label: "Upstream Items" },
            { value: "inline", label: "Inline Content" },
            { value: "existing_article", label: "Existing Article ID" },
          ],
          order: 2,
        }),
        exposedParam("title", "Title", "publish", "Publish", "text", "{{item.title}}", {
          order: 3,
        }),
      ]),
      step("body", "Body and files", "format", "Carries Markdown, desc, files, and thumbnails as structured content fields.", "markdown/desc/files/thumb", "ready", [
        exposedParam("markdown", "Markdown", "content", "Content", "textarea", "{{item.markdown}}", {
          order: 1,
        }),
        exposedParam("desc", "Description", "content", "Content", "textarea", "{{item.summary}}", {
          order: 2,
        }),
        exposedParam("files", "Files", "content", "Content", "tokens", [], {
          order: 3,
        }),
      ]),
      step("accounts", "Logged account resolver", "resolve", "Calls TurboPush list_logged_accounts and filters by target platform intent.", "list_logged_accounts", "ready", [
        exposedParam("targetPlatforms", "Platforms", "target", "Target", "tokens", ["xiaohongshu"], {
          options: turboPushPlatformOptions(),
          order: 1,
        }),
        exposedParam("accountSelector", "Accounts", "target", "Target", "select", "logged_accounts_by_platform", {
          options: [
            { value: "logged_accounts_by_platform", label: "Logged Accounts By Platform" },
            { value: "all_logged", label: "All Logged Accounts" },
          ],
          order: 2,
        }),
      ]),
      step("schema", "Platform settings schema", "validate", "Calls get_platform_setting_schema before building postAccounts.", "get_platform_setting_schema", "ready"),
      step("create", "Create content", "send", "Calls create_article, create_graph_text, or create_video.", "create_*", "ready"),
      step("publish", "SSE publish", "send", "Calls publish_article, publish_graph_text, or publish_video and consumes SSE results.", "publish_* SSE", "ready", [
        exposedParam("syncDraft", "Sync Draft", "runtime", "Runtime", "boolean", false, {
          groupOrder: 4,
          order: 1,
        }),
      ]),
      step("records", "Publish records", "evidence", "Reads list_records/get_record_info for publish evidence.", "record APIs", "ready"),
      step("guard", "Runtime guard", "guard", "Blocks until local TurboPush service and send permission are present.", "service + permission", "ready"),
    ],
  },
  "intelligence.output.inbox": {
    title: "Inbox Internals",
    summary: "Writes reviewable intelligence items into a durable operator queue.",
    steps: [
      step("queue", "Queue resolver", "target", "Resolves the target review queue.", "queue param", "ready", [
        exposedParam("queue", "Queue", "runtime", "Runtime", "select", "macro-watch", {
          options: reviewQueueOptions(),
          order: 1,
        }),
      ]),
      step("write", "Inbox write", "send", "Persists review items into the simulated inbox.", "stored ids", "ready"),
      step("archive", "Archive policy", "cache", "Applies archive/retention settings.", "archive param", "simulated", [
        exposedParam("archive", "Archive", "misc", "Misc", "boolean", true, { groupOrder: 2, order: 1 }),
      ]),
      step("audit", "Audit refs", "evidence", "Keeps stored item ids traceable.", "stored refs", "ready"),
    ],
  },
  "intelligence.output.collection-result": {
    title: "Collection Output Internals",
    summary: "Collects normalized package items and exposes them as the HDA output boundary.",
    steps: [
      step("collect", "Collect normalized items", "store", "Receives items[] from internal normalize.", "items[]", "ready"),
      step("artifact", "Artifact boundary", "archive", "Creates a traceable package output reference.", "opencli-hda-output", "ready"),
      step("handoff", "Downstream handoff", "emit", "Makes package output available to downstream nodes.", "storedItems[]", "simulated"),
    ],
  },
  "package.intelligence.pipeline": {
    title: "Intelligence Pipeline Package",
    summary: "A DOP-level package for the default AI intelligence automation workflow.",
    steps: [
      step("schedule", "Schedule trigger", "trigger", "Starts the pipeline on a cadence.", "tick", "ready"),
      step("source", "JIN10 adapter", "fetch", "Reads the first real data-source adapter.", "items[]", "ready"),
      step("normalize", "Normalize items", "resolve", "Maps provider payloads into canonical items.", "items[] contract", "ready"),
      step("summarize", "Prompt summary", "prompt", "Builds deterministic prompt-ready summaries.", "prompt version", "simulated"),
      step("score", "Score dimensions", "score", "Ranks items by market importance.", "scorecard", "simulated"),
      step("review", "Human review", "audit", "Stores high-signal evidence for review.", "inbox refs", "ready"),
      step("notify", "Mock notify", "send", "Records delivery evidence without live side effects.", "delivery trace", "ready"),
    ],
  },
  "package.processing.record-hygiene": {
    title: "Record Hygiene & Acceptance",
    summary: "A locked, reusable cleaning pipeline that normalizes, deduplicates, and accepts canonical records.",
    steps: [
      step("normalize", "Normalize Items", "normalize", "Normalizes fields, records a language annotation, and preserves source evidence; it does not translate content.", "recordCandidate[]", "ready", [
        exposedParam("language", "Language Annotation", "normalize", "Normalize", "select", "zh-CN", {
          options: [
            { value: "zh-CN", label: "zh-CN" },
            { value: "en-US", label: "en-US" },
          ],
          order: 1,
        }),
        exposedParam("preserveSourceRefs", "Preserve Source Refs", "normalize", "Normalize", "boolean", true, { order: 2 }),
      ]),
      step("dedupe", "Dedupe Items", "dedupe", "Removes duplicate candidates by comparing a stable key and time window within the current input batch; it does not persist cross-run state.", "unique recordCandidate[]", "ready", [
        exposedParam("key", "Dedupe Key", "dedupe", "Dedupe", "text", "title+source+publishedAt", { groupOrder: 2, order: 1 }),
        exposedParam("window", "Window", "dedupe", "Dedupe", "text", "24h", { groupOrder: 2, order: 2 }),
      ]),
      step("record-acceptance", "Record Acceptance Gate", "accept", "Applies schema, lineage, and quality acceptance policy.", "record[]", "ready", [
        exposedParam("mode", "Mode", "acceptance", "Acceptance", "select", "automatic_with_review", {
          options: [
            { value: "automatic_with_review", label: "Automatic With Review" },
            { value: "manual_review", label: "Manual Review" },
            { value: "automatic_strict", label: "Automatic Strict" },
          ],
          groupOrder: 3,
          order: 1,
        }),
        exposedParam("schema", "Schema", "acceptance", "Acceptance", "text", "record.v1", { groupOrder: 3, order: 2 }),
        exposedParam("lineageRequired", "Lineage Required", "acceptance", "Acceptance", "boolean", true, { groupOrder: 3, order: 3 }),
        exposedParam("minQuality", "Minimum Quality", "acceptance", "Acceptance", "slider", 0, {
          groupOrder: 3,
          order: 4,
          min: 0,
          max: 1,
          step: 0.05,
        }),
      ]),
    ],
  },
  "package.opencli.multi-source-hda": {
    title: "多站点采集执行",
    summary: "按业务配置展开多个网站来源，执行并行采集并统一结果；OpenCLI 仅作为底层执行能力。",
    steps: [
      step("trigger", "Schedule input", "trigger", "Receives the workflow tick from the outer canvas.", "trigger edge", "ready"),
      step("source-slots", "Parallel source slots", "fetch", "Expands params.sources into source-* slots so user/AI can choose sources without editing internals.", "node.params.sources[]", "ready"),
      step("fanout", "Runtime fanout", "queue", "Dispatches source slots in parallel through OpenCLI runtime capacity.", "execution.fanout=parallel", "ready"),
      step("normalize", "Internal normalize", "resolve", "Normalizes all source results before returning items[] to the outer canvas.", "items[] contract", "ready"),
      step("trace", "Runtime trace fold", "evidence", "Backend trace dispatches are folded onto this package node in the run panel.", "opencli-hda trace", "ready"),
    ],
  },
  "package.ops.event": {
    title: "Ops Event Package",
    summary: "A DOP-level package for task automation events and execution evidence.",
    steps: [
      step("manual", "Manual trigger", "trigger", "Allows operator/API launch.", "launch event", "ready"),
      step("schedule", "Schedule trigger", "trigger", "Allows timed launches.", "schedule rule", "ready"),
      step("queue", "Queue limit", "queue", "Applies queue/backpressure policy.", "queue state", "simulated"),
      step("retry", "Retry policy", "retry", "Plans retry attempts after failure.", "retry plan", "simulated"),
      step("artifact", "Run artifact", "evidence", "Persists execution evidence.", "artifact pointer", "ready"),
    ],
  },
  "package.ops.monitor-guard": {
    title: "Monitor Guard Package",
    summary: "A package for metric extraction, threshold checks, and guarded routing.",
    steps: [
      step("metric", "Metric expression", "monitor", "Extracts a numeric signal.", "metric sample", "simulated"),
      step("delta", "Delta monitor", "score", "Computes trend/delta signal.", "delta sample", "simulated"),
      step("threshold", "Threshold gate", "threshold", "Routes when the signal crosses a limit.", "gate result", "ready"),
      step("rate", "Rate limit", "queue", "Prevents noisy repeated alerts.", "rate window", "simulated"),
      step("coverage", "Coverage mark", "evidence", "Records guard coverage.", "coverage mark", "ready"),
    ],
  },
  "package.ops.alert-response": {
    title: "Alert Response Package",
    summary: "A package for notification, ticketing, snapshots, and escalation.",
    steps: [
      step("format", "Payload format", "format", "Formats alert payload.", "payload preview", "ready"),
      step("channel", "Channel action", "send", "Sends through a mocked channel.", "delivery trace", "simulated"),
      step("ticket", "Ticket action", "ticket", "Creates incident ticket evidence.", "ticket ref", "simulated"),
      step("snapshot", "Snapshot action", "snapshot", "Captures server/context snapshot.", "snapshot ref", "simulated"),
      step("escalate", "Escalation gate", "route", "Routes unresolved incidents.", "branch", "future"),
    ],
  },
  "package.ai.prompt-experiment": {
    title: "Prompt Experiment Package",
    summary: "A package for prompt versioning, tests, model comparison, and experiment evidence.",
    steps: [
      step("template", "Prompt template", "prompt", "Builds prompt input.", "prompt", "ready"),
      step("version", "Prompt version", "version", "Pins a prompt revision.", "version id", "ready"),
      step("case", "Prompt test case", "experiment", "Binds test input and expected output.", "case id", "simulated"),
      step("model", "Model compare", "model", "Compares mock/model outputs.", "comparison", "simulated"),
      step("record", "Experiment run", "evaluator", "Records experiment evidence.", "experiment id", "ready"),
    ],
  },
  "package.verify.regression-gate": {
    title: "Regression Gate Package",
    summary: "A package for verification-grade scorecards and regression decisions.",
    steps: [
      step("dataset", "Eval dataset", "experiment", "Selects fixture/eval cases.", "dataset", "ready"),
      step("evaluator", "Evaluator", "evaluator", "Computes deterministic scores.", "scores", "simulated"),
      step("scorecard", "Scorecard", "score", "Summarizes quality gates.", "scorecard", "ready"),
      step("regression", "Regression gate", "threshold", "Blocks quality regressions.", "pass/fail", "ready"),
      step("coverage", "Coverage mark", "evidence", "Records verification coverage.", "coverage", "ready"),
    ],
  },
  "package.map.knowledge-map": {
    title: "Knowledge Map Package",
    summary: "A package for TurnMap-style anchors, semantic links, topic groups, and exports.",
    steps: [
      step("anchor", "Source anchor", "anchor", "Stores jumpable source metadata.", "anchor", "simulated", [
        exposedParam("artifactPath", "Artifact Path", "source", "Source", "text", "runs/{{runId}}/artifact.json", {
          description: "Durable run artifact path used by jump-back.",
          order: 1,
        }),
        exposedParam("runId", "Run ID", "source", "Source", "text", "{{runId}}", {
          description: "Runtime id that produced the source evidence.",
          order: 2,
        }),
        exposedParam("selector", "Selector", "source", "Source", "text", "{{source.selector}}", {
          description: "Optional source selector for jump-back focus.",
          order: 3,
        }),
      ]),
      step("semantic", "Semantic link", "semantic", "Creates typed relationship edges.", "semantic edge", "simulated", [
        exposedParam("relationship", "Relationship", "contract", "Contract", "select", "evidence", {
          options: [
            { value: "related", label: "Related" },
            { value: "depends-on", label: "Depends On" },
            { value: "evidence", label: "Evidence" },
            { value: "contradicts", label: "Contradicts" },
            { value: "implements", label: "Implements" },
          ],
          order: 1,
          groupOrder: 2,
        }),
        exposedParam("confidence", "Confidence", "contract", "Contract", "slider", 0.72, {
          min: 0,
          max: 1,
          step: 0.01,
          order: 2,
          groupOrder: 2,
        }),
        exposedParam("contractId", "Contract ID", "contract", "Contract", "text", "edge.contract.semantic", {
          description: "Contract key used by semantic edge validation.",
          order: 3,
          groupOrder: 2,
        }),
      ]),
      step("weight", "Link weight", "weight", "Stores edge weight and visual strength.", "weight", "ready", [
        exposedParam("weight", "Weight", "contract", "Contract", "slider", 0.75, {
          min: 0,
          max: 1,
          step: 0.01,
          order: 4,
          groupOrder: 2,
        }),
      ]),
      step("topic", "Topic collapse", "topic", "Creates recoverable topic groups.", "topic group", "simulated", [
        exposedParam("mode", "Mode", "runtime", "Runtime", "select", "draft", {
          options: [
            { value: "draft", label: "Draft" },
            { value: "locked", label: "Locked" },
          ],
          order: 1,
          groupOrder: 3,
        }),
        exposedParam("nodeCount", "Node Count", "runtime", "Runtime", "number", 0, {
          readonly: true,
          order: 2,
          groupOrder: 3,
        }),
      ]),
      step("export", "Knowledge export", "format", "Exports Canvas/OPML/Markdown.", "export artifact", "ready", [
        exposedParam("latestArtifact", "Latest Artifact", "runtime", "Runtime", "text", "runs/{{runId}}/knowledge-export.md", {
          readonly: true,
          order: 3,
          groupOrder: 3,
        }),
      ]),
    ],
  },
  "package.review.human-review": {
    title: "Human Review Package",
    summary: "A package for human approval, inbox persistence, and audit evidence.",
    steps: [
      step("approval", "Human approval", "route", "Splits approved and rejected items.", "approval branch", "simulated"),
      step("inbox", "Inbox write", "send", "Stores reviewable items.", "stored refs", "ready"),
      step("audit", "Evidence pack", "audit", "Bundles source and decision evidence.", "evidence pack", "ready"),
      step("notify", "Review notification", "send", "Notifies reviewers in mock mode.", "delivery trace", "simulated"),
    ],
  },
}

const DATA_OPERATOR_KIND_BY_OPERATOR_ID: Record<string, string> = {
  "core.generate.instruction-pairs": "generate",
  "core.filter.quality": "filter",
  "core.evaluate.quality": "evaluate",
  "core.refine.text": "refine",
  "text.clean": "refine",
  "text.rule-filter": "filter",
  "text.deduplicate": "filter",
  "text.statistics": "evaluate",
  "data.project": "refine",
  "data.chunk": "generate",
  "data.qa-extract": "generate",
  "data.training-format": "refine",
}

export function getNodeInternals(node: WorkflowProjectNode | undefined): NodeInternals | undefined {
  if (!node) return undefined
  const catalogId = typeof node.ui?.catalogId === "string" ? node.ui.catalogId : undefined
  if (catalogId && NODE_INTERNALS[catalogId]) return NODE_INTERNALS[catalogId]

  if (isCollectionNeedNode(node)) return NODE_INTERNALS["intelligence.input.collection-need"]
  if (node.kind === "schedule" && node.capability === "trigger") return NODE_INTERNALS["intelligence.schedule.cron"]
  if (node.kind === "source" && node.adapter === "jin10-kuaixun") return NODE_INTERNALS["intelligence.source.jin10"]
  if (node.kind === "source" && node.adapter === "rss-feed") return NODE_INTERNALS["intelligence.source.rss"]
  if (node.kind === "source" && node.adapter?.startsWith("opencli-")) return NODE_INTERNALS["intelligence.source.opencli-slot"]
  if (node.kind === "agent" && node.capability === "normalize" && node.params.fanout === "parallel") return NODE_INTERNALS["intelligence.source.pool"]
  if (node.kind === "agent" && typeof node.params.operatorId === "string") {
    const kind = DATA_OPERATOR_KIND_BY_OPERATOR_ID[node.params.operatorId]
    if (kind && NODE_INTERNALS[`intelligence.data.${kind}`]) return NODE_INTERNALS[`intelligence.data.${kind}`]
  }
  if (node.kind === "agent" && node.capability === "normalize") return NODE_INTERNALS["intelligence.processing.normalize"]
  if (node.kind === "agent" && node.capability === "dedupe") return NODE_INTERNALS["intelligence.processing.dedupe"]
  if (node.kind === "agent" && node.capability === "summarize") return NODE_INTERNALS["intelligence.agent.summary"]
  if (node.kind === "agent" && node.capability === "score") return NODE_INTERNALS["intelligence.agent.score"]
  if (node.kind === "agent" && node.capability === "tag") return NODE_INTERNALS["intelligence.agent.tag"]
  if (node.kind === "router" && node.capability === "route") return NODE_INTERNALS["intelligence.router.importance"]
  if (node.kind === "control" && node.capability === "accept") return NODE_INTERNALS["intelligence.control.record-acceptance"]
  if (node.kind === "sink" && node.capability === "store") return NODE_INTERNALS["intelligence.sink.records"]
  if (node.kind === "inbox" && node.capability === "store" && node.params.queue === "opencli-hda-output") return NODE_INTERNALS["intelligence.output.collection-result"]
  if (node.kind === "inbox" && node.capability === "store") return NODE_INTERNALS["intelligence.output.inbox"]
  if (node.kind === "notify" && node.adapter?.startsWith("turbopush")) return NODE_INTERNALS["intelligence.output.turbopush-publish"]
  if (node.kind === "notify" && node.capability === "send") return NODE_INTERNALS["intelligence.output.webhook"]
  return undefined
}

function step(
  id: string,
  label: string,
  capability: string,
  description: string,
  evidence: string,
  status: NodeInternalStatus,
  exposedParams?: NodeInternalExposedParam[],
): NodeInternalStep {
  return { id, label, capability, description, evidence, status, exposedParams }
}

function dataOperatorInternals(title: string, operatorId: string): NodeInternals {
  return {
    title: `${title} Internals`,
    summary: "Runs a backend-projected Data Operator Pack implementation while preserving candidate lineage.",
    steps: [
      step("operator", "Operator binding", "dispatch", "Selects a same-kind operator from the backend capability manifest.", operatorId, "ready", [
        exposedParam("operatorId", "Operator", "operator", "Data Operator", "text", operatorId, { order: 1 }),
        exposedParam("config", "Config (JSON)", "operator", "Data Operator", "json", {}, {
          description: "Operator-specific configuration object.",
          order: 2,
        }),
      ]),
      step("lineage", "Lineage guard", "validate", "Preserves input candidate lineage on every emitted candidate.", "recordCandidate[]", "ready"),
      step("metrics", "Metrics projection", "evidence", "Projects metrics and rejected candidate ids into the runtime trace.", "metrics", "ready"),
    ],
  }
}

function exposedParam(
  id: string,
  label: string,
  groupId: string,
  groupLabel: string,
  type: ParameterFieldType,
  value: unknown,
  options: Partial<Omit<NodeInternalExposedParam, "id" | "label" | "groupId" | "groupLabel" | "type" | "value">> = {},
): NodeInternalExposedParam {
  return {
    id,
    label,
    groupId,
    groupLabel,
    type,
    value,
    binding: options.binding ?? { source: "params", fieldId: id },
    ...options,
  }
}

function isCollectionNeedNode(node: WorkflowProjectNode): boolean {
  if (node.ui?.catalogId === "intelligence.input.collection-need") return true
  if (node.kind !== "schedule" || node.capability !== "trigger") return false
  if (node.params.mode === "demand-draft") return true
  return hasNeedShape(node.params) && !hasScheduleShape(node.params)
}

function hasNeedShape(params: Record<string, unknown>): boolean {
  return typeof params.text === "string" || typeof params.locale === "string"
}

function hasScheduleShape(params: Record<string, unknown>): boolean {
  return typeof params.interval === "string" || typeof params.timezone === "string"
}

function timezoneOptions() {
  return [
    { value: "Asia/Shanghai", label: "Asia/Shanghai" },
    { value: "UTC", label: "UTC" },
    { value: "America/New_York", label: "America/New_York" },
  ]
}

function notificationTargetOptions() {
  return [
    { value: "webhook", label: "Webhook" },
    { value: "ops-alerts", label: "Ops Alerts" },
    { value: "human-review", label: "Human Review" },
  ]
}

function reviewQueueOptions() {
  return [
    { value: "macro-watch", label: "Macro Watch" },
    { value: "risk-review", label: "Risk Review" },
    { value: "ops-triage", label: "Ops Triage" },
  ]
}

function turboPushContentTypeOptions() {
  return [
    { value: "article", label: "Article" },
    { value: "graph_text", label: "Graph Text" },
    { value: "video", label: "Video" },
  ]
}

function turboPushPlatformOptions() {
  return [
    { value: "wechat", label: "WeChat" },
    { value: "wechat-video", label: "WeChat Channels" },
    { value: "douyin", label: "Douyin" },
    { value: "toutiaohao", label: "Toutiao" },
    { value: "kuaishou", label: "Kuaishou" },
    { value: "xiaohongshu", label: "Xiaohongshu" },
    { value: "bilibili", label: "Bilibili" },
    { value: "zhihu", label: "Zhihu" },
    { value: "sina", label: "Sina Weibo" },
    { value: "csdn", label: "CSDN" },
    { value: "juejin", label: "Juejin" },
    { value: "jianshuhao", label: "Jianshu" },
    { value: "tiktok", label: "TikTok" },
    { value: "youtube", label: "YouTube" },
    { value: "x", label: "X" },
    { value: "pinduoduo", label: "Pinduoduo" },
    { value: "acfun", label: "AcFun" },
    { value: "omtencent", label: "Tencent" },
    { value: "weishi", label: "Weishi" },
    { value: "baijiahao", label: "Baijiahao" },
  ]
}
