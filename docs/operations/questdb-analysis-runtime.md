---
slug: questdb-analysis-runtime
date: 2026-09-02
verdict: VIABLE
originating-phase: pvl
---

# Optional QuestDB Analysis Runtime

QuestDB is an optional External Analysis Runtime for user-requested Analysis Snapshots.
PostgreSQL or SQLite remains authoritative. Starting QuestDB does not add a dual write,
event subscriber, worker dependency, retry queue, outbox, background retry, or source-business
mutation.

Issue #100 installs only the runtime boundary and its probe. The two future production row
families are Workflow Trace events and acquisition execution metrics; this ticket does not
implement either family and does not introduce Agent runtime metrics.

## Opt In

The default Compose model does not contain the `questdb` service. Confirm that contract with:

```shell
docker compose config --services
```

Set the following values in `.env` when the API should report the runtime as enabled:

```dotenv
QUESTDB_ANALYSIS_RUNTIME_ENABLED=true
QUESTDB_ANALYSIS_RUNTIME_URL=http://questdb:9000
QUESTDB_ANALYSIS_RUNTIME_HEALTH_URL=http://questdb:9003
QUESTDB_ANALYSIS_RUNTIME_TIMEOUT_SECONDS=2
QUESTDB_HTTP_PORT=9000
QUESTDB_HEALTH_PORT=9003
```

Then start only the optional service:

```shell
docker compose --profile analysis-runtime up -d questdb
```

After changing any `QUESTDB_ANALYSIS_RUNTIME_*` value, recreate the API container so it
receives the new environment and private-network attachment:

```shell
docker compose up -d --force-recreate api
```

A plain `docker compose restart api` reuses the container's old environment and is not
sufficient after editing `.env`. For a native deployment, restart the API process instead.
Workers do not read or use the QuestDB settings in this ticket and do not need to be
recreated or restarted; that remains true unless a later ticket explicitly adds a worker
integration.

If `QUESTDB_HTTP_PORT` or `QUESTDB_HEALTH_PORT` changes after QuestDB has been created,
recreate that optional service so Docker applies the new host publication:

```shell
docker compose --profile analysis-runtime up -d --force-recreate questdb
```

The image is pinned to `questdb/questdb:10.0.1`. Host ports `9000` (QWP/web/query) and
`9003` (health) bind to `127.0.0.1` only. PostgreSQL wire `8812` and ILP `9009` are not
published because the adapter and probe do not use them. The API, frontend, and workers have
no `depends_on` relationship with QuestDB. The container runs with a read-only root,
dedicated internal network, dropped capabilities, and `DO_CHOWN=false`; the named data volume
therefore must retain the image-provided `questdb` ownership instead of relying on runtime
`CAP_CHOWN`.

## Capability Status

`QuestDBAnalysisRuntime.get_status()` performs only two internal checks: the fixed health
request and `select 1 as ready`. It does not accept caller-provided SQL. Its complete public
projection is `runtime`, `state`, and `reason_code`:

| State | Reason code | Meaning |
|---|---|---|
| `disabled` | `disabled_by_configuration` | The operator has not enabled the runtime. No request is made. |
| `unavailable` | `connection_failed` | A required health or query transport could not be reached within the configured timeout. |
| `unhealthy` | `health_check_failed` | The health endpoint was reachable but did not return the expected healthy response. |
| `unhealthy` | `readiness_check_failed` | The query endpoint was reachable but rejected or failed the fixed readiness contract. |
| `ready` | `ready` | Health and fixed query readiness both passed. |

Status never includes credentials, response bodies, endpoints, exception text, or SQL. A
disabled or unreachable runtime does not affect normal API or worker startup.

## Disposable Probe

Run the protocol probe from the repository root:

```shell
uv run python scripts/questdb/probe.py
```

Every invocation generates a unique `opencli-questdb-probe-*` Compose project, publishes each
container port against the dynamic host range `49152-65535`, and lets Docker select one
available loopback port for each mapping during container creation. The probe discovers the
mappings after startup, starts only `questdb`, and creates a project-scoped volume. It proves
the pinned image, dedicated internal network, container hardening, HTTP health, explicit
schema, designated timestamp, deterministic WAL deduplication, a bounded time-window
aggregate, table cleanup, and container/volume cleanup. Its `finally` path always runs
`down --volumes --remove-orphans` against that unique project, including after a failed
assertion or interruption. It never addresses a shared or developer Compose project.

A successful run ends with JSON containing:

```json
{
  "bounded_aggregation": {"result": [2, 12.0], "status": "PASS"},
  "cleanup": {"containers": "PASS", "volumes": "PASS"},
  "designated_timestamp": "PASS",
  "deterministic_duplicate": {
    "aggregate": [3, 112.0],
    "replacement": 5.0,
    "status": "PASS"
  },
  "docker_assigned_loopback_ports": "PASS",
  "explicit_schema": "PASS",
  "health": "PASS",
  "image": "questdb/questdb:10.0.1",
  "image_pin": "PASS",
  "isolation": {
    "capabilities_dropped": "PASS",
    "entrypoint_chown_disabled": "PASS",
    "internal_network": "PASS",
    "minimal_startup_capabilities": "PASS",
    "no_new_privileges": "PASS",
    "pids_limit": "PASS",
    "read_only_root": "PASS",
    "writable_tmpfs": "PASS"
  },
  "readiness": "PASS",
  "result": "PASS",
  "table_cleanup": "PASS"
}
```

## Teardown

Stop and remove only the optional runtime container while preserving its snapshot volume:

```shell
docker compose --profile analysis-runtime stop questdb
docker compose --profile analysis-runtime rm -f questdb
```

Set `QUESTDB_ANALYSIS_RUNTIME_ENABLED=false`, then recreate the API container as shown above
if the capability should report `disabled`. A native API process must likewise be restarted
to receive the change; workers remain unrelated and need no restart. Do not use
`docker compose down --volumes` on a shared OpenCLI project merely to remove QuestDB; that
command can remove unrelated authoritative service volumes. The disposable probe handles
its own isolated volume deletion.

## Feasibility Verdict

## Hypothesis

QuestDB 10.0.1 can support the narrow HTTP health/readiness and disposable time-series
semantics required by Issue #100 without PGWire, ILP, or a new Python dependency.

## Mechanism Under Test

The official `questdb/questdb:10.0.1` image, min-health HTTP endpoint on `9003`, SQL REST
endpoint on `9000`, explicit WAL table creation, designated timestamp metadata, DEDUP UPSERT
KEYS replacement, bounded timestamp filtering, and cleanup.

## Probe Family

`5 - Container exec / internal-port HTTP`.

## Probe Cost Class

`needs-container`. The safety gate was met by using a generated disposable Compose project;
no shared or development container was inspected, executed in, stopped, or removed.

## Probe Method

The pre-implementation feasibility command started `questdb/questdb:10.0.1` under a generated
container name, queried `9003` and `/api/v1/sql/execute`, ran fixed DDL/DML/aggregate statements,
dropped the table, and removed the container in `finally`. The committed reproducer is:

```shell
uv run python scripts/questdb/probe.py
```

## Evidence Captured

The image reported `Status: Healthy`; the explicit five-column schema reported designated
timestamp index `4`; replaying one deduplication key yielded three rows with sum `112.0` and
replacement value `5.0`; the two-minute bounded window yielded two rows with sum `12.0`.
Table cleanup, disposable project container cleanup, and project volume cleanup all passed.

## Verdict

VIABLE

## Resulting Design Constraint

- **What this licenses:** Issue #100 may depend on loopback ports `9000` and `9003`, the fixed
  HTTP readiness query, explicit WAL schemas with designated timestamps, deterministic DEDUP
  UPSERT KEYS behavior, and bounded timestamp aggregates in a disposable QuestDB 10.0.1 runtime.
- **What this forbids:** The design must not require PGWire `8812`, ILP `9009`, arbitrary SQL
  from API/UI callers, a new client dependency, shared containers, or any authoritative/continuous
  data path.
- **What remains uncertain (known-gap):** Production schemas, redaction, export receipts,
  retention, and the two future row-family mappings belong to later tickets and are not proven
  by this infrastructure probe. QWP-specific ingestion is not used or asserted by Issue #100.

## Official References

- [QuestDB Docker deployment](https://questdb.com/docs/deployment/docker/)
- [QuestDB REST API](https://questdb.com/docs/connect/compatibility/rest-api/)
- [QuestDB monitoring and health](https://questdb.com/docs/operations/monitoring-alerting/)
- [QuestDB deduplication](https://questdb.com/docs/concepts/deduplication/)
- [QuestDB designated timestamp](https://questdb.com/docs/concepts/designated-timestamp)
- [QuestDB 10.0.1 release notes](https://questdb.com/release-notes)
