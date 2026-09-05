# ruff: noqa: N815, UP045
"""WorkflowProject API contracts for Canvas-authored workflows.

The frontend owns the canonical authoring graph. These schemas mirror the
TypeScript WorkflowProject shape closely enough for the backend compiler to
validate and preview execution without persisting or dispatching work.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.plan_ir import PlanGraph
from backend.schemas.workflow_evidence import WorkflowEvidenceBatchDetail as WorkflowEvidenceBatchDetail
from backend.schemas.workflow_evidence import WorkflowEvidenceBatchListResponse as WorkflowEvidenceBatchListResponse
from backend.schemas.workflow_evidence import WorkflowEvidenceProjection as WorkflowEvidenceProjection
from backend.schemas.workflow_runtime import WorkflowNodeRunEventType as WorkflowNodeRunEventType
from backend.schemas.workflow_runtime import WorkflowRunBatchReference as WorkflowRunBatchReference
from backend.schemas.workflow_runtime import WorkflowRunBlockReason as WorkflowRunBlockReason
from backend.schemas.workflow_runtime import WorkflowRunCheckpoint as WorkflowRunCheckpoint
from backend.schemas.workflow_runtime import WorkflowRunNodeState as WorkflowRunNodeState
from backend.schemas.workflow_runtime import WorkflowRunStatus as WorkflowRunStatus
from backend.schemas.workflow_compile import WorkflowCompileError
from backend.schemas.workflow_runtime import (
    WORKFLOW_NODE_PATH_SEPARATOR,
    WorkflowRunProjection,
)
from backend.schemas.workflow_runtime import (
    WorkflowNodeRunEvent as _WorkflowNodeRunEvent,
)
from backend.schemas.workflow_runtime import (
    legacy_workflow_node_location as _legacy_workflow_node_location,
)
from backend.schemas.workflow_runtime import (
    normalize_workflow_node_path as _normalize_workflow_node_path,
)

WorkflowNodeRunEvent = _WorkflowNodeRunEvent
WORKFLOW_COMPILE_VERSION = "1.1.0"
RunId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=36,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,35}$",
    ),
]
WorkflowRunTriggerKind = Literal["manual", "ai", "schedule", "webhook"]
WorkflowRunInputSource = Literal["operator", "agent", "external"]
WorkflowRunResponseMode = Literal["async", "sync-short-wait", "callback"]



WorkflowProfile = Literal["intelligence", "agent-debug", "sdk-dev"]
WorkflowNodeKind = Literal[
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
]
WorkflowCapability = Literal[
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
]
AdapterBindingType = Literal["source", "notification", "storage", "agent", "utility"]
AdapterBindingMode = Literal["fixture", "mock", "webhook", "live"]
WorkflowCapabilitySurface = Literal[
    "catalog",
    "primitive",
    "channel",
    "notifier",
    "trigger",
    "resource",
]
WorkflowCapabilityStatus = Literal["runnable", "blocked", "preview_only", "design_only"]
CollectorSourceKind = Literal["web", "api", "rss", "cli"]

COLLECTOR_NODE_KIND_BY_CATALOG_ID: dict[str, CollectorSourceKind] = {
    "collection.source.web": "web",
    "collection.source.api": "api",
    "collection.source.rss": "rss",
    "collection.source.cli": "cli",
}

_COLLECTOR_PLAINTEXT_CREDENTIAL_FIELDS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "authtoken",
        "bearertoken",
        "clientsecret",
        "cookie",
        "password",
        "secret",
        "token",
        "xapikey",
    }
)
_COLLECTOR_CLI_COMMAND_FIELDS = frozenset({"commandline", "rawcommand", "scripttext", "shell"})


def _normalized_config_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _find_forbidden_collector_fields(
    value: object,
    *,
    cli: bool,
    path: tuple[str, ...] = (),
) -> list[str]:
    forbidden = set(_COLLECTOR_PLAINTEXT_CREDENTIAL_FIELDS)
    if cli:
        forbidden.update(_COLLECTOR_CLI_COMMAND_FIELDS)
    matches: list[str] = []
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            nested_path = (*path, str(key))
            if _normalized_config_key(key) in forbidden:
                matches.append(".".join(nested_path))
            matches.extend(
                _find_forbidden_collector_fields(
                    nested_value,
                    cli=cli,
                    path=nested_path,
                )
            )
    elif isinstance(value, list | tuple):
        for index, nested_value in enumerate(value):
            matches.extend(
                _find_forbidden_collector_fields(
                    nested_value,
                    cli=cli,
                    path=(*path, str(index)),
                )
            )
    return matches


class CollectorRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maxAttempts: int = Field(..., ge=1, le=5)
    backoffMs: Optional[int] = Field(None, ge=0, le=30_000)


class CollectorExecutionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrency: Optional[int] = Field(None, ge=1, le=16)
    timeoutMs: Optional[int] = Field(None, ge=1, le=120_000)
    retry: Optional[CollectorRetryPolicy] = None

    @model_validator(mode="after")
    def validate_total_source_budget(self) -> CollectorExecutionOptions:
        attempts = self.retry.maxAttempts if self.retry else 1
        timeout_ms = self.timeoutMs or 60_000
        backoff_ms = (self.retry.backoffMs or 0) if self.retry else 0
        retry_delay_ms = sum(
            min(backoff_ms * (2**attempt), 30_000)
            for attempt in range(max(0, attempts - 1))
        )
        if attempts * timeout_ms + retry_delay_ms > 600_000:
            raise ValueError("collector per-source execution budget must not exceed 600000ms")
        return self


class CollectorSourceBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceId: str = Field(..., min_length=1, max_length=128)
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    enabled: bool = True
    credentialRef: Optional[str] = Field(
        None,
        min_length=1,
        max_length=200,
        pattern=r"^credential://[A-Za-z0-9][A-Za-z0-9_-]{0,35}$",
    )
    credentialScheme: Optional[Literal["bearer", "api_key", "basic"]] = None

    @model_validator(mode="after")
    def validate_credential_pair(self) -> CollectorSourceBase:
        if bool(self.credentialRef) != bool(self.credentialScheme):
            raise ValueError("credentialRef and credentialScheme must be configured together")
        if getattr(self, "kind", None) != "api" and self.credentialRef:
            raise ValueError(
                "credential references are currently supported only for api collectors"
            )
        return self


class WebSourceDefinition(CollectorSourceBase):
    kind: Literal["web"]
    url: str = Field(..., min_length=1)
    fetchMode: Literal["auto", "http", "browser"] = "auto"
    selector: Optional[str] = None
    extraction: dict[str, Any] = Field(default_factory=dict)
    pagination: Optional[dict[str, Any]] = None
    timeWindow: Optional[dict[str, Any]] = None


class APISourceDefinition(CollectorSourceBase):
    kind: Literal["api"]
    url: str = Field(..., min_length=1)
    method: Literal["GET", "HEAD"] = "GET"
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    pagination: Optional[dict[str, Any]] = None
    responseMapping: dict[str, Any] = Field(default_factory=dict)


class RSSSourceDefinition(CollectorSourceBase):
    kind: Literal["rss"]
    feedUrl: str = Field(..., min_length=1)
    timeWindow: Optional[dict[str, Any]] = None
    itemLimit: Optional[int] = Field(None, ge=1, le=10_000)


class CLISourceDefinition(CollectorSourceBase):
    kind: Literal["cli"]
    adapterNodeId: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


CollectorSourceDefinition = Annotated[
    WebSourceDefinition | APISourceDefinition | RSSSourceDefinition | CLISourceDefinition,
    Field(discriminator="kind"),
]


class CollectorNodeParams(BaseModel):
    """Versioned, persistable parameters shared by the four L1 collectors."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    execution: CollectorExecutionOptions = Field(default_factory=CollectorExecutionOptions)
    sources: list[CollectorSourceDefinition] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_source_ids_and_sensitive_fields(self) -> CollectorNodeParams:
        source_ids = [source.sourceId for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("collector sourceId values must be unique within a node")
        for source in self.sources:
            forbidden = _find_forbidden_collector_fields(
                source.model_dump(mode="python", exclude_none=True),
                cli=source.kind == "cli",
            )
            if forbidden:
                raise ValueError(
                    "collector source contains forbidden persisted fields: "
                    + ", ".join(sorted(forbidden))
                )
        return self


class CollectedItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itemId: str = Field(..., min_length=1)
    sourceId: str = Field(..., min_length=1)
    sourceType: CollectorSourceKind
    title: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    data: Any = None
    publishedAt: Optional[str] = None
    fetchedAt: str = Field(..., min_length=1)
    lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("publishedAt", mode="before")
    @classmethod
    def normalize_blank_published_at(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


class CollectorSourceError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    retryable: bool


class SourceExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceId: str = Field(..., min_length=1)
    status: Literal["completed", "failed", "skipped"]
    itemCount: int = Field(..., ge=0)
    attempts: int = Field(..., ge=0)
    startedAt: str = Field(..., min_length=1)
    finishedAt: str = Field(..., min_length=1)
    error: Optional[CollectorSourceError] = None

    @model_validator(mode="after")
    def validate_status_error(self) -> SourceExecutionResult:
        if self.status == "failed" and self.error is None:
            raise ValueError("failed collector source result requires error details")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only failed collector source results may contain error details")
        return self


class CollectorOutputV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CollectedItemV1] = Field(default_factory=list)
    sourceResults: list[SourceExecutionResult] = Field(default_factory=list)


def validate_collector_node_params(
    catalog_id: str,
    params: CollectorNodeParams | Mapping[str, Any],
) -> CollectorNodeParams:
    """Validate one L1 collector against its catalog-declared source kind."""

    expected_kind = COLLECTOR_NODE_KIND_BY_CATALOG_ID.get(catalog_id)
    if expected_kind is None:
        raise ValueError(f"unsupported collector catalog id: {catalog_id}")
    validated = (
        params
        if isinstance(params, CollectorNodeParams)
        else CollectorNodeParams.model_validate(params)
    )
    mismatched = [source.sourceId for source in validated.sources if source.kind != expected_kind]
    if mismatched:
        raise ValueError(
            f"{catalog_id} only accepts {expected_kind} sources; mismatched sourceIds: "
            + ", ".join(mismatched)
        )
    return validated


def normalize_collector_node_params(
    catalog_id: str,
    params: Mapping[str, Any],
) -> CollectorNodeParams:
    """Project legacy CLI ``site + command`` to a v1 source without mutation."""

    copied = deepcopy(dict(params))
    if "sources" in copied or "version" in copied:
        return validate_collector_node_params(catalog_id, copied)
    if catalog_id != "collection.source.cli":
        return validate_collector_node_params(catalog_id, copied)

    site = copied.get("site")
    command = copied.get("command")
    if not isinstance(site, str) or not site.strip():
        raise ValueError("legacy CLI collector params require non-empty site")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("legacy CLI collector params require non-empty command")

    def safe_id(value: str) -> str:
        normalized = "".join(
            character if character.isalnum() else "-" for character in value.strip().lower()
        )
        return "-".join(part for part in normalized.split("-") if part) or "adapter"

    legacy_identity = f"{site.strip()}\0{command.strip()}".encode()
    source_id = f"legacy-{sha256(legacy_identity).hexdigest()[:16]}"
    args = copied.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("legacy CLI collector args must be an object")
    normalized = {
        "version": 1,
        "execution": deepcopy(copied.get("execution", {})),
        "sources": [
            {
                "kind": "cli",
                "sourceId": source_id,
                "name": f"{site.strip()} · {command.strip()}",
                "enabled": copied.get("enabled", True),
                "adapterNodeId": f"opencli.adapter.{safe_id(site)}.{safe_id(command)}",
                "args": deepcopy(args),
            }
        ],
    }
    credential_ref = copied.get("credentialRef")
    if isinstance(credential_ref, str) and credential_ref:
        normalized["sources"][0]["credentialRef"] = credential_ref
    return validate_collector_node_params(catalog_id, normalized)
WorkflowOpenCLIAdapterPresetKind = Literal["source_slot", "tool_capability"]
WorkflowOpenCLIAdapterReadiness = Literal[
    "source_slot_ready",
    "source_slot_requires_params",
    "tool_capability_review_required",
]


class WorkflowSourceAnchor(BaseModel):
    kind: Literal["artifact", "url", "message", "selector"]
    label: str = Field(..., min_length=1)
    href: Optional[str] = None
    artifactPath: Optional[str] = None
    selector: Optional[str] = None
    runId: Optional[str] = None


class WorkflowRunArtifact(BaseModel):
    runId: str = Field(..., min_length=1)
    artifactPath: str = Field(..., min_length=1)
    apiPath: Optional[str] = None


class WorkflowMiniNetwork(BaseModel):
    nodes: int = Field(..., ge=0)
    edges: int = Field(..., ge=0)
    mode: Literal["title-only", "ports", "contract"]


class WorkflowTopicCollapse(BaseModel):
    groupId: str = Field(..., min_length=1)
    nodeCount: int = Field(..., ge=0)
    mode: Literal["draft", "locked"]
    packageInternal: bool


class WorkflowParameterBinding(BaseModel):
    nodeId: str = Field(..., min_length=1)
    source: Literal["params", "adapter", "data"]
    fieldId: str = Field(..., min_length=1)


class WorkflowParameterInterfaceGroup(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    order: Optional[float] = None


class WorkflowParameterInterfaceField(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    groupId: str = Field(..., min_length=1)
    type: Literal["text", "textarea", "json", "number", "slider", "select", "boolean", "tokens"]
    binding: WorkflowParameterBinding
    description: Optional[str] = None
    order: Optional[float] = None
    readonly: Optional[bool] = None
    value: Any = None
    placeholder: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: list[dict[str, str]] | None = None


class WorkflowParameterInterface(BaseModel):
    groups: list[WorkflowParameterInterfaceGroup] = Field(default_factory=list)
    fields: list[WorkflowParameterInterfaceField] = Field(default_factory=list)


class WorkflowProjectNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1)
    kind: WorkflowNodeKind
    capability: WorkflowCapability
    adapter: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    sourceAnchor: Optional[WorkflowSourceAnchor] = None
    runArtifact: Optional[WorkflowRunArtifact] = None
    miniNetwork: Optional[WorkflowMiniNetwork] = None
    topicCollapse: Optional[WorkflowTopicCollapse] = None
    proposalState: Optional[Literal["draft", "proposed", "accepted"]] = None
    parameterInterface: Optional[WorkflowParameterInterface] = None
    internals: Optional[WorkflowPackageInternals] = None
    ui: Optional[dict[str, Any]] = None

    @field_validator("id")
    @classmethod
    def validate_local_node_id(cls, value: str) -> str:
        if WORKFLOW_NODE_PATH_SEPARATOR in value or "__" in value:
            raise ValueError('node id must not contain reserved path separators "::" or "__"')
        return value

    @model_validator(mode="after")
    def validate_collector_params(self) -> WorkflowProjectNode:
        catalog_id = (self.ui or {}).get("catalogId")
        if isinstance(catalog_id, str) and catalog_id in COLLECTOR_NODE_KIND_BY_CATALOG_ID:
            normalize_collector_node_params(catalog_id, self.params)
        return self


class WorkflowSemanticLink(BaseModel):
    relationship: Literal["related", "depends-on", "evidence", "contradicts", "implements"]
    reason: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)


class WorkflowProjectEdge(BaseModel):
    id: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    target: str = Field(..., min_length=1)
    sourcePort: Optional[str] = None
    targetPort: Optional[str] = None
    label: Optional[str] = None
    condition: Optional[str] = None
    semantic: Optional[WorkflowSemanticLink] = None
    weight: Optional[float] = Field(None, ge=0, le=1)
    contractId: Optional[str] = None
    proposalState: Optional[Literal["draft", "proposed", "accepted"]] = None
    ui: Optional[dict[str, Any]] = None


class WorkflowPackageInternals(BaseModel):
    locked: Optional[bool] = None
    nodes: list[WorkflowProjectNode] = Field(default_factory=list)
    edges: list[WorkflowProjectEdge] = Field(default_factory=list)


class WorkflowSettings(BaseModel):
    timezone: str = "Asia/Shanghai"
    deterministicSimulation: bool = True
    maxItemsPerRun: int = Field(20, gt=0)


class WorkflowAdapterBinding(BaseModel):
    id: str = Field(..., min_length=1)
    type: AdapterBindingType
    provider: str = Field(..., min_length=1)
    mode: AdapterBindingMode = "fixture"
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowAgentPermissions(BaseModel):
    canFetchNetwork: bool = False
    canSendNotifications: bool = False
    canWriteInbox: bool = True
    canMutateExternalSites: bool = False
    allowedDomains: list[str] = Field(default_factory=list)


class WorkflowProject(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    profile: WorkflowProfile
    version: Literal[1] = 1
    nodes: list[WorkflowProjectNode] = Field(..., min_length=1)
    edges: list[WorkflowProjectEdge] = Field(default_factory=list)
    settings: WorkflowSettings = Field(
        default_factory=lambda: WorkflowSettings(
            timezone="Asia/Shanghai",
            deterministicSimulation=True,
            maxItemsPerRun=20,
        )
    )
    adapters: list[WorkflowAdapterBinding] = Field(default_factory=list)
    agentPermissions: WorkflowAgentPermissions = Field(
        default_factory=lambda: WorkflowAgentPermissions()
    )


class WorkflowCompileRequest(BaseModel):
    project: WorkflowProject


class WorkflowPatchOperation(BaseModel):
    op: Literal[
        "add_node",
        "connect_nodes",
        "update_parameters",
        "add_adapter",
        "materialize_opencli_adapter",
        "package_nodes",
        "request_missing_capability",
    ]
    node: Optional[WorkflowProjectNode] = None
    edge: Optional[WorkflowProjectEdge] = None
    adapter: Optional[WorkflowAdapterBinding] = None
    nodeId: Optional[str] = None
    adapterNodeId: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    packageNode: Optional[WorkflowProjectNode] = None
    internalNodeIds: list[str] = Field(default_factory=list)
    capability: Optional[str] = None
    reason: Optional[str] = None


class WorkflowPatchRequest(BaseModel):
    project: WorkflowProject
    operations: list[WorkflowPatchOperation] = Field(..., min_length=1)


class WorkflowDemandDraftRequest(BaseModel):
    project: WorkflowProject
    text: str = Field(..., min_length=1)
    locale: Optional[str] = None


ExternalWorkflowRuntime = Literal["langgraph", "langchain", "dataflow"]


class WorkflowExternalImportRequest(BaseModel):
    project: WorkflowProject
    runtime: ExternalWorkflowRuntime
    graph: dict[str, Any] = Field(..., min_length=1)
    name: Optional[str] = None
    locale: Optional[str] = None



class CompiledWorkflowAdapterBinding(BaseModel):
    id: str
    type: AdapterBindingType
    provider: str
    mode: AdapterBindingMode
    config: dict[str, Any] = Field(default_factory=dict)


class CompiledWorkflowNode(BaseModel):
    id: str
    kind: WorkflowNodeKind
    capability: WorkflowCapability
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    adapter: Optional[CompiledWorkflowAdapterBinding] = None
    sourceAnchor: Optional[WorkflowSourceAnchor] = None
    runArtifact: Optional[WorkflowRunArtifact] = None
    package: Optional[dict[str, Any]] = None
    runtime: dict[str, Any] = Field(default_factory=dict)


class CompiledWorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    sourcePort: str
    targetPort: str
    contractId: Optional[str] = None
    condition: Optional[str] = None


class WorkflowAuthoringMetadata(BaseModel):
    project_id: str
    project_name: str
    project_version: int
    profile: WorkflowProfile
    node_count: int
    edge_count: int
    adapter_count: int
    settings: WorkflowSettings
    agentPermissions: WorkflowAgentPermissions


class WorkflowRuntimePreview(BaseModel):
    execution_mode: Literal["preview"] = "preview"
    dispatch: Literal["none"] = "none"
    node_ids: list[str] = Field(default_factory=list)
    nodes: list[CompiledWorkflowNode] = Field(default_factory=list)
    edges: list[CompiledWorkflowEdge] = Field(default_factory=list)
    plan_ir: PlanGraph


class WorkflowCompiledPlanPreview(BaseModel):
    compile_version: str = WORKFLOW_COMPILE_VERSION
    authoring: WorkflowAuthoringMetadata
    runtime: WorkflowRuntimePreview


class WorkflowCompileResponse(BaseModel):
    valid: bool
    errors: list[WorkflowCompileError] = Field(default_factory=list)
    plan: Optional[WorkflowCompiledPlanPreview] = None


class WorkflowRuntimeCapability(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    surface: WorkflowCapabilitySurface
    status: WorkflowCapabilityStatus
    backendAvailable: bool = False
    kind: Optional[WorkflowNodeKind] = None
    capability: Optional[WorkflowCapability] = None
    provider: Optional[str] = None
    channelType: Optional[str] = None
    notifierType: Optional[str] = None
    runtimeBinding: Optional[str] = None
    reason: Optional[str] = None
    missing: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: Optional[str] = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorkflowCapabilitiesResponse(BaseModel):
    version: str = WORKFLOW_COMPILE_VERSION
    catalog: list[WorkflowRuntimeCapability] = Field(default_factory=list)
    primitives: list[WorkflowRuntimeCapability] = Field(default_factory=list)
    channels: list[WorkflowRuntimeCapability] = Field(default_factory=list)
    notifiers: list[WorkflowRuntimeCapability] = Field(default_factory=list)
    triggers: list[WorkflowRuntimeCapability] = Field(default_factory=list)
    resources: list[WorkflowRuntimeCapability] = Field(default_factory=list)


class WorkflowToolCapabilityPort(BaseModel):
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)


class WorkflowToolCapabilityExecutor(BaseModel):
    mode: Literal[
        "fixture",
        "okx_market_ticker_snapshot",
        "joyai_vl_interaction",
        "situation_awareness",
        "swarm_simulation",
        "native_intelligence",
        "opentabs",
        "bbx",
        "kats_runtime",
        "wigolo_embedded",
        "gaojixing_doubao_batch",
        "gaojixing_batch_certify",
    ]
    description: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)


class WorkflowCapabilityVersionPin(BaseModel):
    package: str = Field(..., min_length=1)
    packageVersion: str = Field(..., min_length=1)
    capabilityVersion: str = Field(..., min_length=1)
    provenance: Literal["built-in", "verified", "unverified"]


class WorkflowToolCapability(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: Optional[str] = None
    status: Literal["runnable", "blocked"] = "runnable"
    provider: str = "opencli-admin"
    inputPorts: list[WorkflowToolCapabilityPort] = Field(default_factory=list)
    outputPorts: list[WorkflowToolCapabilityPort] = Field(default_factory=list)
    executor: WorkflowToolCapabilityExecutor
    # None means the capability's registry entry does not declare a pinned
    # package version, so workflow-side pin enforcement is skipped for it.
    versionPin: Optional[WorkflowCapabilityVersionPin] = None
    tags: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorkflowToolCapabilitiesResponse(BaseModel):
    version: str = WORKFLOW_COMPILE_VERSION
    tools: list[WorkflowToolCapability] = Field(default_factory=list)


class WorkflowOpenCLIAdapterNodeArg(BaseModel):
    name: str = Field(..., min_length=1)
    type: Optional[str] = None
    required: bool = False
    valueRequired: bool = False
    positional: bool = False
    choices: list[Any] = Field(default_factory=list)
    default: Any = None
    help: Optional[str] = None


class WorkflowOpenCLIAdapterNode(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: str = ""
    status: WorkflowCapabilityStatus
    site: str = Field(..., min_length=1)
    command: str = Field(..., min_length=1)
    access: str = "read"
    browser: bool = False
    strategy: Optional[str] = None
    domain: Optional[str] = None
    catalogId: str = Field(..., min_length=1)
    kind: WorkflowNodeKind
    capability: WorkflowCapability
    presetKind: WorkflowOpenCLIAdapterPresetKind
    runtimeReadiness: WorkflowOpenCLIAdapterReadiness
    requiredArgs: list[str] = Field(default_factory=list)
    args: list[WorkflowOpenCLIAdapterNodeArg] = Field(default_factory=list)
    adapter: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorkflowOpenCLIAdapterNodeFacets(BaseModel):
    site: dict[str, int] = Field(default_factory=dict)
    capability: dict[str, int] = Field(default_factory=dict)
    access: dict[str, int] = Field(default_factory=dict)
    browser: dict[str, int] = Field(default_factory=dict)
    status: dict[str, int] = Field(default_factory=dict)
    presetKind: dict[str, int] = Field(default_factory=dict)
    runtimeReadiness: dict[str, int] = Field(default_factory=dict)


class WorkflowOpenCLIAdapterNodesResponse(BaseModel):
    total: int = Field(..., ge=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    facets: WorkflowOpenCLIAdapterNodeFacets = Field(
        default_factory=WorkflowOpenCLIAdapterNodeFacets
    )
    nodes: list[WorkflowOpenCLIAdapterNode] = Field(default_factory=list)


class WorkflowOpenTabsToolNode(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: str = ""
    status: WorkflowCapabilityStatus
    plugin: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    access: Literal["read", "write"]
    catalogId: str = "external.tool.capability"
    kind: WorkflowNodeKind = "action"
    capability: WorkflowCapability = "store"
    requiredArgs: list[str] = Field(default_factory=list)
    args: list[WorkflowOpenCLIAdapterNodeArg] = Field(default_factory=list)
    inputSchema: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorkflowOpenTabsToolNodesResponse(BaseModel):
    available: bool = False
    total: int = Field(..., ge=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    nodes: list[WorkflowOpenTabsToolNode] = Field(default_factory=list)


class WorkflowBbxToolNode(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: str = ""
    status: WorkflowCapabilityStatus
    group: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    access: Literal["read", "write"]
    catalogId: str = "external.tool.capability"
    kind: WorkflowNodeKind = "action"
    capability: WorkflowCapability = "store"
    requiredArgs: list[str] = Field(default_factory=list)
    args: list[WorkflowOpenCLIAdapterNodeArg] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)


class WorkflowBbxToolNodesResponse(BaseModel):
    available: bool = False
    total: int = Field(..., ge=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    nodes: list[WorkflowBbxToolNode] = Field(default_factory=list)


class WorkflowFleetSiteBinding(BaseModel):
    site: str = Field(..., min_length=1)
    browserEndpoint: str = Field(..., min_length=1)
    notes: Optional[str] = None


class WorkflowFleetAgent(BaseModel):
    endpoint: str = Field(..., min_length=1)
    label: str = ""
    mode: str = "bridge"
    nodeType: str = "docker"
    agentUrl: Optional[str] = None
    agentProtocol: Optional[str] = None
    status: str = "unknown"
    connected: bool = False
    available: bool = False
    sites: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    source: str = "runtime"


class WorkflowFleetInventoryResponse(BaseModel):
    version: str = WORKFLOW_COMPILE_VERSION
    summary: dict[str, Any] = Field(default_factory=dict)
    agents: list[WorkflowFleetAgent] = Field(default_factory=list)
    siteBindings: list[WorkflowFleetSiteBinding] = Field(default_factory=list)


class WorkflowFleetCapabilityMatchRequest(BaseModel):
    adapterNodeId: Optional[str] = None
    site: Optional[str] = None
    command: Optional[str] = None


class WorkflowFleetCapabilityCandidate(BaseModel):
    endpoint: str = Field(..., min_length=1)
    label: str = ""
    mode: str = "bridge"
    agentUrl: Optional[str] = None
    agentProtocol: Optional[str] = None
    status: str = "unknown"
    connected: bool = False
    available: bool = False
    score: int = 0
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    sites: list[str] = Field(default_factory=list)


class WorkflowFleetCapabilityMatchResponse(BaseModel):
    matched: bool
    adapterNodeId: Optional[str] = None
    site: Optional[str] = None
    command: Optional[str] = None
    requiresBrowser: bool = False
    requiresSiteBinding: bool = False
    selected: Optional[WorkflowFleetCapabilityCandidate] = None
    candidates: list[WorkflowFleetCapabilityCandidate] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class WorkflowOpenCLIHDATraceRequest(BaseModel):
    project: WorkflowProject
    packageNodeId: Optional[str] = None
    runId: Optional[RunId] = None
    traceId: Optional[str] = None


class WorkflowOpenCLIHDATraceDispatch(BaseModel):
    taskId: str
    nodeId: str
    nodePath: list[str] = Field(default_factory=list)
    packageNodeId: Optional[str] = None
    internalNodeId: Optional[str] = None
    sourceGroup: str
    site: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    sourceBindingId: Optional[str] = None
    sourceBindingRevisionId: Optional[str] = None
    sourceBindingRevisionNumber: Optional[int] = Field(default=None, ge=1)
    iii: dict[str, Any]

    @model_validator(mode="after")
    def normalize_node_location(self) -> WorkflowOpenCLIHDATraceDispatch:
        self.nodePath = _normalize_workflow_node_path(
            node_id=self.nodeId,
            node_path=self.nodePath,
            package_node_id=self.packageNodeId,
            internal_node_id=self.internalNodeId,
        )
        package_node_id, internal_node_id = _legacy_workflow_node_location(self.nodePath)
        self.packageNodeId = package_node_id
        self.internalNodeId = internal_node_id
        return self


class WorkflowOpenCLIHDATraceResponse(BaseModel):
    valid: bool
    errors: list[WorkflowCompileError] = Field(default_factory=list)
    workflowId: str
    runId: str
    traceId: str
    packageNodeId: Optional[str] = None
    dispatch: dict[str, Any] = Field(default_factory=dict)
    dispatches: list[WorkflowOpenCLIHDATraceDispatch] = Field(default_factory=list)







class WorkflowRunTrigger(BaseModel):
    kind: WorkflowRunTriggerKind = "manual"
    triggerNodeId: Optional[str] = None
    requestId: Optional[str] = None
    idempotencyKey: Optional[str] = None


class WorkflowRunInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    source: WorkflowRunInputSource = "operator"
    sourceId: Optional[str] = None


class WorkflowRunStartRequest(BaseModel):
    project: WorkflowProject
    packageNodeId: Optional[str] = None
    runId: Optional[RunId] = None
    traceId: Optional[str] = None
    sourceOutputs: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    trigger: WorkflowRunTrigger = Field(default_factory=WorkflowRunTrigger)
    input: WorkflowRunInput = Field(default_factory=WorkflowRunInput)
    responseMode: WorkflowRunResponseMode = "async"


class WorkflowWebhookIngressRequest(BaseModel):
    workflowProject: WorkflowProject
    input: WorkflowRunInput = Field(
        default_factory=lambda: WorkflowRunInput(source="external")
    )
    requestId: Optional[str] = None
    idempotencyKey: Optional[str] = None
    runId: Optional[RunId] = None
    traceId: Optional[str] = None
    responseMode: WorkflowRunResponseMode = "async"


class WorkflowRunSourceOutputsRequest(BaseModel):
    sourceOutputs: dict[str, list[dict[str, Any]]] = Field(..., min_length=1)



class WorkflowWebhookIngressResponse(BaseModel):
    workflowId: str
    runId: str
    traceId: str
    triggerNodeId: str
    requestId: str
    sourceId: Optional[str] = None
    idempotencyKey: Optional[str] = None
    projectionPath: str
    eventsPath: str
    projection: WorkflowRunProjection




class WorkflowMissingCapability(BaseModel):
    capability: str
    reason: Optional[str] = None
    n8n_search_hint: Optional[str] = None


class WorkflowPatchPreview(BaseModel):
    operations: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowPatchResponse(BaseModel):
    valid: bool
    errors: list[WorkflowCompileError] = Field(default_factory=list)
    missing_capabilities: list[WorkflowMissingCapability] = Field(default_factory=list)
    patch: WorkflowPatchPreview
    project: Optional[WorkflowProject] = None
    compile: Optional[WorkflowCompileResponse] = None
