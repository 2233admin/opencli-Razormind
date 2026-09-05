"""Stable workflow runtime node I/O contract declarations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from backend.schemas.workflow import (
    CollectorNodeParams,
    CollectorSourceKind,
    normalize_collector_node_params,
)

RuntimeIOContractStatus = Literal[
    "executable",
    "dispatch_only",
    "projection_only",
    "blocked_until_preconditions",
]


@dataclass(frozen=True)
class RuntimeIOContract:
    binding_id: str
    status: RuntimeIOContractStatus
    input_ports: tuple[tuple[str, str], ...]
    output_ports: tuple[tuple[str, str], ...]
    input_params: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    permission_gate: tuple[str, ...]
    config_gate: tuple[str, ...]
    event_shape: tuple[str, ...]
    fixture_coverage: tuple[str, ...]
    real_webhook_delivery: bool = False
    resource_gate: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    provenance_fields: tuple[str, ...] = ()
    limits: tuple[tuple[str, int], ...] = ()
    variadic_input_port: tuple[str, str] | None = None
    min_input_connections: int | None = None
    legacy_input_ports: tuple[str, ...] = ()

    def to_manifest(self) -> dict[str, object]:
        input_ports = [_port(name, type_) for name, type_ in self.input_ports]
        if self.variadic_input_port is not None:
            name, type_ = self.variadic_input_port
            input_ports = [
                _port(
                    name,
                    type_,
                    cardinality="many",
                    min_connections=self.min_input_connections,
                    legacy_aliases=self.legacy_input_ports,
                )
            ]
        return {
            "schemaVersion": 1,
            "bindingId": self.binding_id,
            "status": self.status,
            "inputShape": {
                "ports": input_ports,
                "params": list(self.input_params),
            },
            "outputShape": {
                "ports": [_port(name, type_) for name, type_ in self.output_ports],
                "artifacts": list(self.output_artifacts),
            },
            "permissionGate": {
                "required": list(self.permission_gate),
            },
            "configGate": {
                "required": list(self.config_gate),
            },
            "resourceGate": {
                "required": list(self.resource_gate),
            },
            "errors": [{"code": code, "stable": True} for code in self.errors],
            "provenance": {
                "required": bool(self.provenance_fields),
                "fields": list(self.provenance_fields),
            },
            "limits": dict(self.limits),
            "eventShape": {
                "events": list(self.event_shape),
            },
            "fixtureCoverage": {
                "cases": list(self.fixture_coverage),
            },
            "certification": {
                "realNodeIoContract": True,
                "realWebhookDelivery": self.real_webhook_delivery,
            },
            "canvas": {
                "exposeResourceInternals": False,
            },
        }


def _native_contract(
    binding_id: str,
    *,
    input_type: str,
    output_type: str,
    input_params: tuple[str, ...],
    output_ports: tuple[tuple[str, str], ...] | None = None,
    output_artifacts: tuple[str, ...] = ("output",),
) -> RuntimeIOContract:
    return RuntimeIOContract(
        binding_id=binding_id,
        status="executable",
        input_ports=(("in", input_type),),
        output_ports=output_ports or (("out", output_type),),
        input_params=input_params,
        output_artifacts=output_artifacts,
        permission_gate=(),
        config_gate=("native_config_valid",),
        event_shape=("started", "partial", "completed"),
        fixture_coverage=("native-node-runtime",),
    )


RUNTIME_IO_CONTRACTS: dict[str, RuntimeIOContract] = {
    "workflow.native.template-transform": _native_contract(
        "workflow.native.template-transform",
        input_type="object",
        output_type="object",
        input_params=("template", "output_key?"),
    ),
    "workflow.native.variable-assign": _native_contract(
        "workflow.native.variable-assign",
        input_type="object",
        output_type="object",
        input_params=("assignments",),
    ),
    "workflow.native.variable-aggregate": _native_contract(
        "workflow.native.variable-aggregate",
        input_type="object",
        output_type="object",
        input_params=("variables", "strategy?", "output_key?"),
    ),
    "workflow.native.list-filter": _native_contract(
        "workflow.native.list-filter",
        input_type="any[]",
        output_type="any[]",
        input_params=("field?", "operator", "value?"),
    ),
    "workflow.native.list-sort": _native_contract(
        "workflow.native.list-sort",
        input_type="any[]",
        output_type="any[]",
        input_params=("field?", "direction?"),
    ),
    "workflow.native.if": _native_contract(
        "workflow.native.if",
        input_type="any",
        output_type="any",
        input_params=("condition",),
        output_ports=(("true", "any"), ("false", "any")),
        output_artifacts=("output", "route"),
    ),
    "workflow.native.switch": _native_contract(
        "workflow.native.switch",
        input_type="any",
        output_type="any",
        input_params=("cases", "default?"),
        output_artifacts=("output", "route"),
    ),
    "workflow.native.iteration": _native_contract(
        "workflow.native.iteration",
        input_type="any[]",
        output_type="iteration[]",
        input_params=("max_items?",),
        output_artifacts=("iterations", "itemCount"),
    ),
    "workflow.native.loop": _native_contract(
        "workflow.native.loop",
        input_type="loopState",
        output_type="loopState",
        input_params=("condition", "max_iterations?"),
        output_ports=(("continue", "loopState"), ("done", "loopState")),
        output_artifacts=("output", "route", "iteration"),
    ),
    "workflow.compat.dify.graphon": RuntimeIOContract(
        binding_id="workflow.compat.dify.graphon",
        status="blocked_until_preconditions",
        input_ports=(("in", "any"),),
        output_ports=(("out", "any"),),
        input_params=(
            "sourceSha256",
            "appMode",
            "policy",
            "dependencies",
            "sourceNodeIndex",
            "runtimeInputEnvelope",
            "ephemeralGrants",
        ),
        output_artifacts=(
            "workflowOutput",
            "nestedNodeEvents",
            "items[]?",
            "EvidenceBatch?",
        ),
        permission_gate=("canFetchNetwork?", "allowedDomains?"),
        config_gate=("graphon_runtime", "inspection_ready"),
        event_shape=(
            "queued",
            "started",
            "partial",
            "tool_call_started",
            "tool_call_completed",
            "blocked",
            "completed",
            "failed",
        ),
        fixture_coverage=("dify-pure-logic", "dify-policy-blockers"),
    ),
    "workflow.demand-draft.patch": RuntimeIOContract(
        binding_id="workflow.demand-draft.patch",
        status="projection_only",
        input_ports=(("in", "collectionNeed"),),
        output_ports=(("patch", "workflowPatch"),),
        input_params=("text", "locale"),
        output_artifacts=("workflowPatch", "compilePreview"),
        permission_gate=("canvas_review_required",),
        config_gate=("capability_catalog",),
        event_shape=("patch_preview", "compile_preview"),
        fixture_coverage=("workflow-capabilities-api",),
    ),
    "workflow.trigger.schedule_tick": RuntimeIOContract(
        binding_id="workflow.trigger.schedule_tick",
        status="executable",
        input_ports=(),
        output_ports=(("tick", "trigger"),),
        input_params=("interval", "timezone", "enabled"),
        output_artifacts=("workflowRunTrigger",),
        permission_gate=(),
        config_gate=(),
        event_shape=("queued", "started", "completed"),
        fixture_coverage=("workflow-capabilities-api", "workflow-run-default-node"),
    ),
    "workflow.trigger.webhook_input": RuntimeIOContract(
        binding_id="workflow.trigger.webhook_input",
        status="dispatch_only",
        input_ports=(),
        output_ports=(("request", "webhookRequest"),),
        input_params=("method", "path"),
        output_artifacts=("runtimeInputEnvelope",),
        permission_gate=(),
        config_gate=(),
        event_shape=("queued", "started", "completed"),
        fixture_coverage=(
            "workflow-compile-api",
            "workflow-capabilities-api",
            "workflow-webhook-ingress-api",
        ),
    ),
    "workflow.source-pool.parallel-fanout": RuntimeIOContract(
        binding_id="workflow.source-pool.parallel-fanout",
        status="executable",
        input_ports=(("in", "trigger"),),
        output_ports=(("out", "trigger"),),
        input_params=("sourceCount", "sourceGroups", "fanout"),
        output_artifacts=("sourceFanoutPlan",),
        permission_gate=(),
        config_gate=("source_slots_present",),
        event_shape=("partial:sourceCount", "completed"),
        fixture_coverage=("workflow-capabilities-api", "opencli-hda-trace-api"),
    ),
    "iii.collector-opencli.snapshot": RuntimeIOContract(
        binding_id="iii.collector-opencli.snapshot",
        status="dispatch_only",
        input_ports=(("in", "trigger"),),
        output_ports=(("out", "items[]"),),
        input_params=("site", "command", "args", "format"),
        output_artifacts=("batch_ready", "tool_call_completed", "items[]"),
        permission_gate=(
            "canFetchNetwork",
            "write:proposalState=accepted",
            "write:canMutateExternalSites",
        ),
        config_gate=("site", "command", "opencli_channel"),
        event_shape=(
            "read:batch_ready",
            "write:tool_call_started",
            "write:tool_call_completed",
            "partial:itemCount",
            "completed",
        ),
        fixture_coverage=(
            "happy-path",
            "opencli-write-approval",
            "opencli-write-permission",
            "opencli-write-browser-profile-lock",
            "sse-parity",
            "odp-redis-mirror",
        ),
    ),
    "workflow.source.fetch": RuntimeIOContract(
        binding_id="workflow.source.fetch",
        status="blocked_until_preconditions",
        input_ports=(("in", "trigger"),),
        output_ports=(("items", "items[]"),),
        input_params=("provider", "channelType", "liveMode", "sourceId"),
        output_artifacts=("sourceOutputs", "fixtureItems", "boundSourceRecords"),
        permission_gate=("canFetchNetwork",),
        config_gate=("sourceOutputs_or_fixtureItems_or_boundSourceRecords", "sourceCredential?"),
        event_shape=("partial:itemCount", "blocked:source_output_required", "completed"),
        fixture_coverage=(
            "happy-path",
            "permission-blocked",
            "missing-source-credential",
        ),
    ),
    "workflow.collection-output.items": RuntimeIOContract(
        binding_id="workflow.collection-output.items",
        status="executable",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("out", "items[]"),),
        input_params=("queue", "archive"),
        output_artifacts=("runTraceItems", "items[]"),
        permission_gate=(),
        config_gate=("run_trace",),
        event_shape=("partial:itemCount", "completed"),
        fixture_coverage=("workflow-capabilities-api", "opencli-hda-trace-api"),
    ),
    "workflow.transform.normalize": RuntimeIOContract(
        binding_id="workflow.transform.normalize",
        status="executable",
        input_ports=(("in", "items[]"),),
        output_ports=(("out", "recordCandidate[]"),),
        input_params=("language", "preserveSourceRefs"),
        output_artifacts=("recordCandidate[]",),
        permission_gate=(),
        config_gate=(),
        event_shape=("partial:recordCandidateCount", "completed"),
        fixture_coverage=("happy-path", "sse-parity", "odp-redis-mirror"),
    ),
    **{
        f"workflow.data.{operation}": RuntimeIOContract(
            binding_id=f"workflow.data.{operation}",
            status="executable",
            input_ports=(("in", "recordCandidate[]"),),
            output_ports=(("out", "recordCandidate[]"),),
            input_params=("operatorId", "packVersion", "config"),
            output_artifacts=("recordCandidate[]", "metrics", "rejectedCandidateIds"),
            permission_gate=(),
            config_gate=("data_operator_registry",),
            event_shape=("partial:outputItemCount", "completed", "failed"),
            fixture_coverage=("data-operator-unit", "workflow-data-operator-e2e"),
        )
        for operation in ("generate", "filter", "evaluate", "refine")
    },
    "workflow.transform.dedupe": RuntimeIOContract(
        binding_id="workflow.transform.dedupe",
        status="executable",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("out", "recordCandidate[]"),),
        input_params=(
            "key",
            "window",
            "identityFields",
            "eventTimeField",
            "windowSeconds",
            "strategy",
        ),
        output_artifacts=("recordCandidate[]", "rejected[]", "metrics"),
        permission_gate=(),
        config_gate=(),
        event_shape=(
            "partial:deduplicatedCandidateCount",
            "partial:rejectedCount",
            "partial:metrics",
            "completed",
        ),
        fixture_coverage=("workflow-opencli-hda-trace-api",),
    ),
    "workflow.flow.merge": RuntimeIOContract(
        binding_id="workflow.flow.merge",
        status="executable",
        input_ports=(("in1", "recordCandidate[]"), ("in2", "recordCandidate[]")),
        output_ports=(("out", "recordCandidate[]"),),
        input_params=("strategy", "preserveLineage"),
        output_artifacts=("recordCandidate[]",),
        permission_gate=(),
        config_gate=("typed_port_contract_registered",),
        event_shape=("partial:mergedCandidateCount", "completed"),
        fixture_coverage=("workflow-capabilities-api",),
    ),
    "workflow.router.route": RuntimeIOContract(
        binding_id="workflow.router.route",
        status="executable",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("out", "recordCandidate[]"),),
        input_params=("expression", "mode"),
        output_artifacts=("recordCandidate[]",),
        permission_gate=(),
        config_gate=(),
        event_shape=("partial:routedCandidateCount", "completed"),
        fixture_coverage=("happy-path", "sse-parity", "odp-redis-mirror"),
    ),
    "workflow.gate.record-acceptance": RuntimeIOContract(
        binding_id="workflow.gate.record-acceptance",
        status="executable",
        input_ports=(("candidates", "recordCandidate[]"),),
        output_ports=(("records", "record[]"),),
        input_params=("mode", "schema", "dedupe", "lineageRequired", "minQuality"),
        output_artifacts=("record[]", "reviewRequiredCount"),
        permission_gate=("record_acceptance_policy",),
        config_gate=("record_schema_registry",),
        event_shape=("partial:acceptedRecordCount", "partial:reviewRequiredCount", "completed"),
        fixture_coverage=("workflow-capabilities-api",),
    ),
    "workflow.record-sink.records": RuntimeIOContract(
        binding_id="workflow.record-sink.records",
        status="executable",
        input_ports=(("records", "record[]"),),
        output_ports=(("stored", "storedItems[]"),),
        input_params=("target", "writeMode", "preserveLineage", "feishuWriteback"),
        output_artifacts=("storedRefs", "collected_records"),
        permission_gate=("canWriteInbox", "canMutateExternalSites?"),
        config_gate=("data_sources", "collection_tasks", "collected_records"),
        event_shape=("partial:storedRefs", "failed:feishu_sheet_writeback_*", "completed"),
        fixture_coverage=("workflow-capabilities-api",),
        errors=(
            "feishu_sheet_writeback_config_invalid",
            "feishu_sheet_writeback_bridge_unavailable",
            "feishu_sheet_writeback_timeout",
            "feishu_sheet_writeback_http_error",
            "feishu_sheet_writeback_invalid_response",
            "feishu_sheet_writeback_invalid_receipt",
            "feishu_sheet_writeback_rejected",
        ),
    ),
    "workflow.feishu-bitable.records": RuntimeIOContract(
        binding_id="workflow.feishu-bitable.records",
        status="blocked_until_preconditions",
        input_ports=(("records", "storedItems[]"),),
        output_ports=(("delivery", "deliveryAttempt[]"),),
        input_params=("connectionId", "appToken", "tableId", "fieldMap"),
        output_artifacts=("deliveryAttempts",),
        permission_gate=(),
        config_gate=("saved_feishu_connection", "existing_bitable_target"),
        event_shape=("partial:deliveryAttempts", "blocked", "completed"),
        fixture_coverage=("feishu-bitable-delivery",),
        errors=("feishu_delivery_failed",),
    ),
    "workflow.inbox.store": RuntimeIOContract(
        binding_id="workflow.inbox.store",
        status="executable",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("stored", "storedItems[]"),),
        input_params=("queue", "writeMode", "archive", "preserveLineage"),
        output_artifacts=("storedRefs",),
        permission_gate=("canWriteInbox",),
        config_gate=("queue",),
        event_shape=("partial:storedRecordCount", "completed"),
        fixture_coverage=("happy-path", "sse-parity", "odp-redis-mirror"),
    ),
    "workflow.notify.send": RuntimeIOContract(
        binding_id="workflow.notify.send",
        status="projection_only",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("payload", "notificationPayload"),),
        input_params=("notifier_type", "template", "target", "delivery_configured"),
        output_artifacts=("notificationPayload",),
        permission_gate=("canSendNotifications",),
        config_gate=("delivery_projection", "configured_notifier_target"),
        event_shape=("partial:inputItemCount", "blocked:missing_delivery_projection", "completed"),
        fixture_coverage=("happy-path", "permission-blocked", "missing-webhook-url"),
    ),
    "workflow.notifier.webhook.send": RuntimeIOContract(
        binding_id="workflow.notifier.webhook.send",
        status="blocked_until_preconditions",
        input_ports=(("in", "EvidenceBatch"),),
        output_ports=(("delivery", "webhookDeliveryAttempt"),),
        input_params=("template", "target", "adapter_mode"),
        output_artifacts=("webhookDeliveryAttempt",),
        permission_gate=("canSendNotifications",),
        config_gate=("evidencebatch_projection_api", "delivery_projection", "webhook_url"),
        event_shape=(
            "partial:webhookDeliveryAttempt",
            "completed",
            "blocked:missing_delivery_projection",
        ),
        fixture_coverage=(
            "webhook-real-delivery",
            "webhook-missing-permission",
            "webhook-missing-projection",
            "missing-webhook-url",
            "workflow-capabilities-api",
        ),
        real_webhook_delivery=True,
    ),
    "turbopush.local.publish": RuntimeIOContract(
        binding_id="turbopush.local.publish",
        status="blocked_until_preconditions",
        input_ports=(("in", "recordCandidate[]"),),
        output_ports=(("publish", "turbopushPublishResult"),),
        input_params=("contentType", "contentSource", "targetPlatforms", "accountSelector"),
        output_artifacts=("publishResult",),
        permission_gate=("canSendNotifications",),
        config_gate=("turbopush_local_service", "contentType"),
        event_shape=("partial:publishResult", "blocked:missing_turbopush_service", "completed"),
        fixture_coverage=("missing-runtime-resource", "workflow-turbopush-publish-api"),
    ),
    "workflow.external-tool.capability": RuntimeIOContract(
        binding_id="workflow.external-tool.capability",
        status="blocked_until_preconditions",
        input_ports=(("in", "unknown"),),
        output_ports=(("out", "unknown"),),
        input_params=("toolCapabilityId", "executorMode", "toolParams"),
        output_artifacts=("toolOutput",),
        permission_gate=("canvas_review_required",),
        config_gate=("tool_capability_registry", "node_params.toolCapability"),
        event_shape=("tool_call_started", "partial:outputItemCount", "tool_call_completed"),
        fixture_coverage=("workflow-capabilities-api", "workflow-tool-capabilities-api"),
    ),
    "workflow.media.image-generation": RuntimeIOContract(
        binding_id="workflow.media.image-generation",
        status="dispatch_only",
        input_ports=(("prompt", "text"), ("assets", "mediaAsset[]")),
        output_ports=(
            ("assets", "mediaAsset[]"),
            ("generation", "mediaGenerationResult"),
        ),
        input_params=("canvasSnapshotId",),
        output_artifacts=("mediaAsset[]", "mediaGenerationResult"),
        permission_gate=("workspace_asset_write",),
        config_gate=("canvasSnapshotId", "image_runtime_capability"),
        event_shape=("queued", "started", "waiting", "partial", "completed"),
        fixture_coverage=("workflow-image-runtime",),
    ),
    "workflow.media.image-asset": RuntimeIOContract(
        binding_id="workflow.media.image-asset",
        status="executable",
        input_ports=(),
        output_ports=(("assets", "mediaAsset[]"),),
        input_params=("assetIds",),
        output_artifacts=("mediaAsset[]",),
        permission_gate=("workspace_asset_read",),
        config_gate=("assetIds",),
        event_shape=("queued", "started", "partial", "completed"),
        fixture_coverage=("workflow-image-runtime",),
    ),
}

for _collector_catalog_id, _collector_kind in {
    "collection.source.web": "web",
    "collection.source.api": "api",
    "collection.source.rss": "rss",
    "collection.source.cli": "cli",
}.items():
    RUNTIME_IO_CONTRACTS[_collector_catalog_id] = RuntimeIOContract(
        binding_id=_collector_catalog_id,
        status="dispatch_only",
        input_ports=(("in", "trigger"),),
        output_ports=(("out", "CollectorOutputV1"),),
        input_params=("version", "execution", "sources"),
        output_artifacts=("CollectorOutputV1", "CollectedItemV1[]", "SourceExecutionResult[]"),
        permission_gate=("canFetchNetwork",),
        config_gate=(f"{_collector_kind}_sources_valid",),
        event_shape=(
            "source_started",
            "source_completed",
            "source_failed",
            "partial:itemCount",
            "completed",
        ),
        fixture_coverage=(f"collector-{_collector_kind}-contract-v1",),
        errors=("collector_source_invalid", "collector_all_enabled_sources_failed"),
        provenance_fields=("sourceId", "sourceType", "lineage", "fetchedAt"),
    )

# Preserve the legacy ``in1``/``in2`` aliases while exposing a true many-input
# contract to new clients.  Runtime execution reads actual upstream edges.
RUNTIME_IO_CONTRACTS["workflow.flow.merge"] = RuntimeIOContract(
    binding_id="workflow.flow.merge",
    status="executable",
    input_ports=(("in1", "CollectorMergeInputV1"), ("in2", "CollectorMergeInputV1")),
    output_ports=(("out", "recordCandidate[]"),),
    input_params=("preserveLineage",),
    output_artifacts=("recordCandidate[]",),
    permission_gate=(),
    config_gate=("at_least_one_upstream", "typed_port_contract_registered"),
    event_shape=("partial:mergedCandidateCount", "completed"),
    fixture_coverage=("workflow-capabilities-api",),
    variadic_input_port=("in", "CollectorMergeInputV1"),
    min_input_connections=1,
    legacy_input_ports=("in1", "in2"),
)

NATIVE_INTELLIGENCE_ACTION_CONTRACT_ROWS = (
    ("research", "storedItems[]", "researchArtifact", ("research_input_required",), True),
    ("ontology", "researchArtifact", "ontologyArtifact", ("research_artifact_missing",), True),
    ("graph", "ontologyArtifact", "graphArtifact", ("ontology_artifact_missing",), True),
    ("personas", "graphArtifact", "personaArtifact", ("graph_artifact_missing",), True),
    (
        "simulation.prepare",
        "personaArtifact",
        "simulationPlan",
        ("persona_artifact_missing",),
        False,
    ),
    (
        "simulation.start",
        "personaArtifact",
        "simulationStatus",
        ("persona_artifact_missing",),
        True,
    ),
    ("simulation.step", "simulationStatus", "simulationStatus", ("simulation_not_running",), True),
    ("simulation.run", "simulationStatus", "simulationArtifact", ("simulation_not_running",), True),
    ("simulation.stop", "simulationStatus", "simulationStatus", ("simulation_not_running",), True),
    (
        "simulation.resume",
        "simulationStatus",
        "simulationStatus",
        ("simulation_not_stopped",),
        True,
    ),
    (
        "simulation.status",
        "intelligenceSession",
        "simulationStatus",
        ("intelligence_session_not_found",),
        False,
    ),
    (
        "simulation.actions",
        "intelligenceSession",
        "simulationAction[]",
        ("simulation_not_available",),
        False,
    ),
    (
        "simulation.timeline",
        "intelligenceSession",
        "simulationTimeline[]",
        ("simulation_not_available",),
        False,
    ),
    (
        "simulation.stats",
        "intelligenceSession",
        "simulationStats",
        ("simulation_not_available",),
        False,
    ),
    ("interviews.one", "simulationArtifact", "interviewStatus", ("persona_id_required",), True),
    ("interviews.batch", "simulationArtifact", "interviewStatus", ("persona_ids_required",), True),
    (
        "interviews.all",
        "simulationArtifact",
        "interviewStatus",
        ("simulation_artifact_missing",),
        True,
    ),
    (
        "interviews.step",
        "interviewStatus",
        "interviewArtifact",
        ("interview_not_in_progress",),
        True,
    ),
    (
        "interviews.run",
        "interviewStatus",
        "interviewArtifact[]",
        ("interview_not_in_progress",),
        True,
    ),
    (
        "interviews.history",
        "intelligenceSession",
        "interviewArtifact[]",
        ("intelligence_session_not_found",),
        False,
    ),
    ("report.start", "interviewArtifact[]", "reportStatus", ("interview_artifact_missing",), True),
    ("report.step", "reportStatus", "reportStatus", ("report_not_in_progress",), True),
    ("report.run", "reportStatus", "reportArtifact", ("report_not_in_progress",), True),
    ("report.progress", "intelligenceSession", "reportProgress", ("report_not_available",), False),
    ("report.read", "intelligenceSession", "reportArtifact", ("report_artifact_missing",), False),
    ("report.ask", "reportArtifact", "reportAnswerArtifact", ("question_required",), True),
    (
        "report.answers",
        "intelligenceSession",
        "reportAnswerArtifact[]",
        ("intelligence_session_not_found",),
        False,
    ),
    ("cancel", "intelligenceSession", "intelligenceSession", ("session_not_cancellable",), True),
    ("close", "reportArtifact", "closeArtifact", ("report_artifact_missing",), True),
)
NATIVE_INTELLIGENCE_COMMON_ERRORS = (
    "intelligence_artifact_ref_hash_mismatch",
    "intelligence_artifact_ref_invalid",
    "intelligence_artifact_ref_kind_mismatch",
    "intelligence_session_ref_invalid",
    "intelligence_session_id_invalid",
    "intelligence_session_not_found",
    "intelligence_version_conflict",
    "intelligence_idempotency_conflict",
    "intelligence_artifact_not_found",
    "operation_in_progress",
)

for (
    _action,
    _input_type,
    _output_type,
    _errors,
    _mutates,
) in NATIVE_INTELLIGENCE_ACTION_CONTRACT_ROWS:
    _binding_id = f"workflow.native-intelligence.{_action.replace('.', '-')}"
    RUNTIME_IO_CONTRACTS[_binding_id] = RuntimeIOContract(
        binding_id=_binding_id,
        status="executable",
        input_ports=(
            (
                "in",
                "storedItems[]" if _action == "research" else "intelligenceSessionEnvelope",
            ),
        ),
        output_ports=(("out", "intelligenceSessionEnvelope"),),
        input_params=("intelligenceSessionRef", "seed"),
        output_artifacts=(_output_type,),
        permission_gate=(),
        config_gate=(),
        resource_gate=("database_session",),
        event_shape=(
            "tool_call_started",
            "partial:outputItemCount",
            "intelligence.transition",
            "tool_call_completed",
            "completed",
        ),
        fixture_coverage=("native-intelligence-offline-v1",),
        errors=tuple(dict.fromkeys((*_errors, *NATIVE_INTELLIGENCE_COMMON_ERRORS))),
        provenance_fields=(
            "source",
            "evidence_artifact_ids",
            "algorithm_version",
            "seed",
        ),
        limits=(
            ("commandPayloadBytes", 65_536),
            ("artifactPayloadBytes", 1_048_576),
            ("eventPayloadBytes", 16_384),
            ("queryPageSize", 100),
        ),
    )


def list_runtime_io_contracts() -> list[RuntimeIOContract]:
    return [RUNTIME_IO_CONTRACTS[key] for key in sorted(RUNTIME_IO_CONTRACTS)]


def runtime_io_contract(binding_id: str | None) -> RuntimeIOContract | None:
    if not binding_id:
        return None
    return RUNTIME_IO_CONTRACTS.get(binding_id)


def runtime_io_contract_manifest(binding_id: str | None) -> dict[str, object] | None:
    contract = runtime_io_contract(binding_id)
    return contract.to_manifest() if contract else None


def normalize_collector_runtime_params(
    catalog_id: str,
    params: Mapping[str, Any],
) -> CollectorNodeParams:
    """Project saved collector params to v1 without mutating authoring state."""

    return normalize_collector_node_params(catalog_id, params)


def collector_runtime_contract(kind: CollectorSourceKind) -> RuntimeIOContract:
    return RUNTIME_IO_CONTRACTS[f"collection.source.{kind}"]


def has_runtime_io_contract(binding_id: str | None) -> bool:
    return runtime_io_contract(binding_id) is not None


def _port(
    name: str,
    type_: str,
    *,
    cardinality: Literal["one", "many"] | None = None,
    min_connections: int | None = None,
    legacy_aliases: tuple[str, ...] = (),
) -> dict[str, object]:
    port: dict[str, object] = {"name": name, "type": type_}
    if cardinality is not None:
        port["cardinality"] = cardinality
    if min_connections is not None:
        port["minConnections"] = min_connections
    if legacy_aliases:
        port["legacyAliases"] = list(legacy_aliases)
    return port


__all__ = [
    "RUNTIME_IO_CONTRACTS",
    "RuntimeIOContract",
    "RuntimeIOContractStatus",
    "collector_runtime_contract",
    "has_runtime_io_contract",
    "list_runtime_io_contracts",
    "normalize_collector_runtime_params",
    "runtime_io_contract",
    "runtime_io_contract_manifest",
]
