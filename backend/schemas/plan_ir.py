"""Plan IR (Intermediate Representation) — the versioned JSON schema for
Plan graphs (ADR-0008/0009, docs/plan-ir-PRD.md, issue 01).

A Plan is a free graph: any number of source / transform / merge / sink
nodes wired together by port-referenced edges. This module defines the
Pydantic shape of that graph — the same models back both the documented
JSON schema exposed at ``GET /api/v1/plan-ir/schema`` (the API contract
agents author Plans through, story 27) and the structural validator in
``backend.plan_ir.validation``.

Scope note (issue 01): this module defines the IR shape and its schema
only. Persistence (a ``plans`` table, issue 02) and execution (issues
03/04) are explicitly out of scope here — nothing in this module or its
router writes to a database.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

#: Bumped whenever the graph shape changes in a way that could break an
#: existing agent-authored Plan document. Exposed on every schema response
#: and echoed into projected Plans so consumers can pin against it.
PLAN_IR_VERSION = "1.0.0"

NodeKind = Literal["source", "transform", "merge", "sink"]


class PlanPort(BaseModel):
    """One input or output port on a node. ``type`` is the port's data
    type — edges may only connect ports whose types match (or one side is
    ``"any"``), enforced by the structural validator, not by this model."""

    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1, description="Port data type, e.g. 'records', 'any'.")


class PlanNode(BaseModel):
    """One node in the Plan graph.

    ``kind`` drives two mutually exclusive entity-reference fields (issue 01
    acceptance criterion: source nodes carry a source_id reference OR an
    explicit draft marker; transform/merge/sink nodes carry no entity
    references at all):

    - ``source`` nodes: exactly one of ``source_id`` (references an existing
      ``DataSource``) or ``draft=True`` (an unmaterialized sketch, ADR-0009)
      must be set.
    - ``transform`` / ``merge`` / ``sink`` nodes: ``source_id`` and ``draft``
      must both be absent/false — they are pure graph data, never DB
      entities (ADR-0009 "Persistence" decision).

    ``params`` is the node's typed configuration. For a source node this is
    the channel config shape (``backend.schemas.source.DataSourceCreate.
    channel_config``); for transform/merge/sink nodes it is node-type-
    specific and validated only for presence of ``required_params``
    declared alongside — issue 01 ships structural validation (cycles,
    orphan merges, missing required params, port type mismatches), not a
    per-node-type param schema registry (later issues build node-type
    catalogs on top of this).
    """

    id: str = Field(..., min_length=1, description="Graph-unique node id.")
    kind: NodeKind
    #: Node type name, e.g. "opencli_source", "dedupe", "merge", "db_sink".
    #: Free-form on purpose — issue 01 validates graph structure, not an
    #: enumerated node-type catalog (a later issue's Preset work owns that).
    type: str = Field(..., min_length=1)
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    #: Params that MUST be present (non-null) in ``params`` for this node to
    #: pass structural validation. Declared per-node so the validator can
    #: report "missing required param" without a central node-type registry.
    required_params: list[str] = Field(default_factory=list)
    inputs: list[PlanPort] = Field(default_factory=list)
    outputs: list[PlanPort] = Field(default_factory=list)

    # ── source-node entity reference (issue 01 acceptance criterion) ──────
    source_id: str | None = Field(
        None, description="References an existing DataSource. Source nodes only."
    )
    draft: bool = Field(
        False,
        description="Explicit draft marker for an unmaterialized source node "
        "(ADR-0009). Source nodes only.",
    )


class PlanEdge(BaseModel):
    """One directed wire between two node ports."""

    id: str = Field(..., min_length=1, description="Graph-unique edge id.")
    source_node: str = Field(..., min_length=1)
    source_port: str = Field(..., min_length=1)
    target_node: str = Field(..., min_length=1)
    target_port: str = Field(..., min_length=1)


class PlanGraph(BaseModel):
    """The Plan IR document: a versioned graph of nodes and edges.

    This is the shape returned by the degenerate-projection endpoint and
    the shape a future plans-table (issue 02) would persist as JSON — this
    module defines it once so both consume the identical model.
    """

    ir_version: str = Field(default=PLAN_IR_VERSION)
    name: str | None = None
    #: True for a Plan projected from a Data Source's legacy config (the
    #: "degenerate single-node Plan", ADR-0009) or otherwise not yet an
    #: authored/persisted Plan. Projection responses always set this True.
    draft: bool = False
    nodes: list[PlanNode] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)


def plan_ir_json_schema() -> dict[str, Any]:
    """The documented JSON schema for ``PlanGraph`` (story 27 — agents author
    Plans programmatically through this contract). Wraps Pydantic's
    generated schema with the IR version so a consumer can pin/compare
    versions without parsing ``$defs``."""
    return {
        "ir_version": PLAN_IR_VERSION,
        "title": "Plan IR",
        "schema": PlanGraph.model_json_schema(),
    }
