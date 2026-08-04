## Why

Studio currently compiles every node on the canvas before selecting the trigger-reachable runtime graph. A disconnected, incomplete node can therefore prevent a valid collection chain from validating or running even though runtime selection explicitly excludes ordinary disconnected nodes.

This blocks the A-share whole-market collection workflow: its four-node collection chain compiles cleanly, while four unrelated parked nodes make the ten-node draft fail before execution begins.

## What Changes

- Define trigger-scoped graph selection as a source-level operation that occurs before authoritative Run compilation.
- Validate the selected trigger and every downstream reachable node as the active execution graph.
- Classify ordinary disconnected nodes as parked nodes: exclude them from the selected Run and report their diagnostics separately without converting active-chain validity into failure.
- Preserve strict failures for invalid nodes, edges, permissions, adapters, and runtime bindings inside the active execution graph.
- Preserve the existing governed exception for explicitly imported `externalWorkflow` nodes; do not broaden it to arbitrary disconnected nodes.
- Make Studio validation distinguish active-chain errors from parked-node diagnostics and expose deterministic counts and node identifiers.
- Add regression and browser acceptance coverage for the observed `10 nodes / 3 edges` shape without coupling tests to the user's persisted database identifiers.

## Capabilities

### New Capabilities

- `trigger-scoped-workflow-execution`: Selection, validation, execution, and diagnostics for one trigger-reachable workflow component with parked canvas nodes.

### Modified Capabilities

- None.

## Impact

- Backend workflow compiler/run orchestration and Studio draft validation projections.
- Studio workflow lifecycle API response schemas when parked-node diagnostics are added.
- Canvas validation and Run Trace presentation for active and parked node counts.
- Focused unit/integration tests around trigger selection, compilation ordering, validation, and zero-dispatch failure behavior.
- No database migration, node-library catalog expansion, automatic edge creation, or change to OpenCLI adapter execution is authorized by this change.
