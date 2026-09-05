# Frontend page health and development compilation

Verified locally on 2026-09-05 with Next.js 16.3.3, on Windows.

## Scope and limits

- All 47 authenticated page templates returned HTTP 200 from the local frontend. This checks route compilation and server response, not every authenticated business operation. Dynamic entity routes used an existing project ID or an explicitly nonexistent diagnostic ID; their entity data was not validated by the HTTP check.
- Eight browser tests passed against Turbopack: sidebar navigation across all ten primary entries, related views, query-only tabs, reduced motion, history, rapid navigation, runtime reduced-motion changes, and API/MCP copy feedback. These use isolated API fixtures and do not prove production backend correctness.
- The real logged-in browser confirmed the restored Linear Inbox structure. A separate real plugin-registry authentication error was reproduced and is tracked below.

## Compilation measurements

Each bundler was started separately with a new output/cache directory, using the same source tree, host, and sequence of six HTTP requests. Each sequence was then repeated warm. Requests consumed the full HTML response. This is one local sequential sample, not a general benchmark or an interactive browser rendering measurement.

| Route | Webpack first request | Turbopack first request | Turbopack warm request |
| --- | ---: | ---: | ---: |
| `/dashboard` | 31,549 ms | 9,443 ms | 81 ms |
| `/inbox?tab=pending` | 8,828 ms | 1,587 ms | 63 ms |
| `/studio` | 3,878 ms | 1,650 ms | 76 ms |
| `/plugins` | 1,926 ms | 992 ms | 89 ms |
| `/providers` | 1,699 ms | 803 ms | 77 ms |
| `/studio/workflow` | 12,972 ms | 7,724 ms | 62 ms |

The existing long-running service was explicitly launched with `next dev --webpack`. Its logs show 20–30 second Next.js waits during route compilation, while warm responses usually take tens of milliseconds. Its private memory reached 19,631 MB. A fresh Turbopack process after the broader page check used about 4,925 MB private memory; these processes have different lifetimes, so the memory difference alone does not establish a leak or a causal speedup.

The local port 8030 service now uses default `next dev` (Turbopack). The local launcher was corrected to stop forcing Webpack. No dependency upgrade, authentication bypass, animation-duration change, or application layout redesign was needed for this change.

The project README already prescribes `pnpm dev`. Keep that default for development. `next dev` compiles routes on demand; first opening a heavy workflow can still take several seconds. Production `build`/`start` is a different mode and was not benchmarked here. See the [Next.js local-development guide](https://nextjs.org/docs/app/guides/local-development).

## Test portability

Turbopack serializes CSS tokens as `.18s`/`.32s` and may omit leading zeroes in cubic-bezier values. Initial motion tests incorrectly compared these strings with `180ms`/`320ms` and the expanded decimal spelling. The test now compares numeric CSS durations and bezier control points, while retaining exact 180/320 ms, direction, single animation owner, and actual Web Animations API measurements. The final complete run passed 8/8 in 49.9 seconds without retries.

## Local evidence

- `.git/compile-webpack-results.json` and `.git/compile-turbopack-results.json`: six cold and six warm responses for each bundler.
- `.git/page-http-health.json`: 47 page templates, zero non-200 responses.
- `.git/turbopack-compatibility-final.log`: eight browser tests passed.
- `.git/frontend-warm-after.json`: live port 8030 warm responses after switching, 71–403 ms for the same six pages.

The temporary benchmark servers on ports 8032 and 8033 were stopped. The user-facing frontend on port 8030 remains running. Cleanup of the two isolated `.next/compile-*` benchmark caches was rejected by automatic command approval (`blocked by policy`, no more specific reason); those caches remain. Diagnostic TypeScript include entries and automatically generated instruction files were removed.

## Plugin registry verification

The real logged-in plugin page initially displayed `Invalid or missing API token`. Its plugin installation and node capability clients constructed fleet-only Authorization headers and omitted the current user identity. Both now use the existing shared `getApiAuthHeaders()` function, including the existing import/update requests. Backend authorization, request payloads, and error handling are unchanged.

A focused test imports the actual clients and shared authentication modules, substitutes only in-memory credential storage and fetch, and checks both global/workspace routes with identity-only, identity+fleet, fleet-only, and anonymous credentials. It also verifies structured 401 errors, non-JSON 503 errors, and no fabricated development identity. The old implementation reproduced an empty-header failure for the identity-only request.

Verification after the repair:

- `node --test scripts/check-plugin-auth-request-regressions.mjs scripts/check-dify-p0-regressions.mjs scripts/check-node-capability-catalog-regressions.mjs`: 23/23 passed.
- `pnpm exec tsc --noEmit`: passed.
- Scoped ESLint on the changed clients and tests: passed.
- Real logged-in browser: plugin registry error disappeared; actual RSS/API providers display available. The capability tab also loads the real backend catalog: 28 capabilities, including 10 runnable, 7 composed previews, and 11 blocked or needing a plugin. OpenCLI website adapters still report that configuration/adaptation is required, so this does not claim every integration is configured or runnable.

## Review and routing

The lead measured and switched the local development server, checked all page responses, and verified the actual browser. A Luna High explorer audited shared imports and independently reviewed CSS assertion normalization and timing evidence. A Sol High worker repaired the bounded authentication request defect and ran focused tests; the lead independently reviewed the minimal shared-header reuse. No broad dependency or UI refactor was undertaken. The only test rework addressed equivalent CSS serialization under the second bundler; actual animation timing assertions remain enforced.
