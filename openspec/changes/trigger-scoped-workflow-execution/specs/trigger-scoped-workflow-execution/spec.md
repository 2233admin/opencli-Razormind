## Purpose

Define how Studio selects, validates, publishes, and runs one trigger-reachable workflow graph while keeping disconnected canvas work visible without allowing it to block or masquerade as executed work.

## ADDED Requirements

### Requirement: Run scope is selected before authoritative compilation
The system SHALL determine the selected trigger and its downstream reachable nodes from the authored workflow before compiling the Run. Ordinary nodes outside that scope SHALL NOT participate in compilation, dispatch, node-state projection, or result counts for that Run.

#### Scenario: Valid collection chain runs beside incomplete parked nodes
- **WHEN** a draft contains ten nodes and three edges, the selected collection trigger reaches four nodes, and four of the six disconnected nodes have invalid node-library bindings
- **THEN** the Run compiles and executes only the four trigger-reachable nodes
- **AND** the six disconnected nodes produce no Run events, dispatches, batches, items, or failed node states.

#### Scenario: Active-chain defect remains a hard failure
- **WHEN** an invalid adapter, runtime binding, edge, permission contract, or required parameter belongs to the selected trigger-reachable graph
- **THEN** the Run is invalid or blocked according to the existing error contract
- **AND** no downstream dispatch occurs past the applicable safety gate.

#### Scenario: Trigger selection remains explicit
- **WHEN** the requested trigger id is missing, does not match the requested trigger kind, or is ambiguous among multiple entries
- **THEN** the Run returns the existing node-anchored trigger-selection error
- **AND** the system SHALL NOT fall back to compiling or running every canvas node.

#### Scenario: One matching trigger may be selected without an id
- **WHEN** `triggerNodeId` is absent and exactly one supported trigger matches the normalized requested kind
- **THEN** the system selects that trigger
- **AND** request kind `ai` is normalized to `manual` before matching.

#### Scenario: Supported trigger kinds remain bounded
- **WHEN** source-level trigger discovery runs
- **THEN** it recognizes only the existing manual/schedule trigger binding and webhook trigger binding
- **AND** manual is distinguished by existing `builder.nodeType=manual-trigger` or `mode=manual` metadata while all other schedule-binding entries remain schedule triggers.

#### Scenario: Governed external workflow exception is preserved
- **WHEN** a node is explicitly marked as a governed `externalWorkflow` import under the existing runtime contract
- **THEN** the existing inclusion behavior remains unchanged
- **AND** the exception SHALL NOT make arbitrary disconnected nodes runnable.

### Requirement: Studio validation separates active errors from parked diagnostics
Studio validation SHALL evaluate the union of nodes reachable from supported trigger entries as the publishable active graph. Nodes outside that graph SHALL be classified as parked and SHALL NOT make an otherwise valid active graph invalid.

#### Scenario: Parked invalid nodes become warnings
- **WHEN** every active trigger-reachable component is valid and one or more parked nodes are incomplete or use unknown bindings
- **THEN** validation returns `valid=true` with no active errors
- **AND** `warnings` contains node-anchored parked-node diagnostics including the original diagnostic code and node id.

#### Scenario: Parked warning shape and counts are deterministic
- **WHEN** validation classifies a node as parked
- **THEN** `warnings` contains exactly one membership entry with `code=parked_node`, that node's `node_id`, and path `nodes/<node_id>`
- **AND** any configuration diagnostic for that node follows as another warning retaining its original code and node id
- **AND** clients derive parked count from unique `parked_node` membership warnings and active count from current canvas node count minus that parked count.

#### Scenario: Every parked node is visible
- **WHEN** validation finds nodes outside every supported trigger-reachable component
- **THEN** the response exposes deterministic parked-node warnings from which clients can derive the parked count and node identifiers
- **AND** no parked node is silently deleted, connected, repaired, enabled, or published.

#### Scenario: Invalid active component blocks validation
- **WHEN** any node or edge in a supported trigger-reachable component is invalid
- **THEN** validation returns `valid=false`
- **AND** the defect remains in `errors`, not only in `warnings`.

#### Scenario: Draft without a supported trigger preserves legacy behavior
- **WHEN** the draft has no supported trigger entry
- **THEN** validation and Run preserve the existing full-graph behavior and diagnostics
- **AND** no node is classified as parked solely because the graph uses a legacy, media-canvas, or other non-trigger workflow shape.

#### Scenario: Multiple supported triggers remain independent
- **WHEN** a draft contains multiple supported trigger entries
- **THEN** validation checks the union of their reachable components for publication
- **AND** each Run still selects exactly one requested trigger component.

### Requirement: Published versions contain only executable graph authority
The immutable graph stored for a successfully validated and published version SHALL contain the validated active graph plus only the existing governed external-workflow inclusions. The editable draft SHALL retain parked nodes for later authoring.

#### Scenario: Parked nodes stay in draft but not published version
- **WHEN** a valid draft with parked nodes is validated and published
- **THEN** reopening the draft still shows the parked nodes
- **AND** the published version used by schedules, API, MCP, and Agent execution excludes those parked nodes.

#### Scenario: Reconnecting a parked node re-enters validation
- **WHEN** an author connects a formerly parked node downstream of a supported trigger and validates a new draft revision
- **THEN** that node becomes part of the active graph
- **AND** its invalid configuration becomes a hard validation error.

### Requirement: Canvas status communicates execution authority
The Studio canvas SHALL distinguish active nodes from parked nodes in validation and Run feedback without implying that validation executes nodes or that compile-failure events are runtime dispatch evidence.

#### Scenario: Validation summary reports both scopes
- **WHEN** validation completes for a graph with active and parked nodes
- **THEN** the UI reports active and parked counts separately
- **AND** parked diagnostics identify the affected nodes without changing the active-chain pass result.

#### Scenario: Run trace does not count parked compile diagnostics as execution
- **WHEN** a Run is scoped to an active component
- **THEN** Run Trace node, event, batch, and item counts describe only that component
- **AND** the UI SHALL NOT label a parked-node diagnostic as a dispatched or executed node.

### Requirement: Existing trust boundaries remain strict
This change SHALL NOT weaken authentication, permission checks, node-library authority, adapter provenance, idempotency, immutable versioning, or runtime evidence requirements.

#### Scenario: Scope selection does not auto-repair unknown nodes
- **WHEN** a parked node references an unknown node-library binding
- **THEN** the system reports the diagnostic as parked
- **AND** it SHALL NOT register a binding, import n8n capability, create an edge, or substitute a similarly named primitive.

#### Scenario: Runtime completion still requires real evidence
- **WHEN** an active workflow Run reports completion
- **THEN** existing dispatch, event, item-count, and persisted-output evidence requirements remain unchanged
- **AND** a successful scoped compile alone SHALL NOT be presented as successful data collection.
