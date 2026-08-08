# PTT Acceptance Gate

PTT here means the production trial test gate for the real fleet path. A pass
means the system can be deployed on a NAS or edge host, register an Agent, expose
its runtime capabilities to Fleet, dispatch a workflow to the matched node, and
leave a durable run trace.

Product compass: `openspec/changes/internet-situation-awareness-loop`.
Deployment compass: `openspec/changes/durable-deployment-compass`.

## Scope

PTT-0 is local/static validation. PTT-1 through PTT-5 require a running center
and at least one real Agent node. NetBird is the preferred cross-network path;
LAN is allowed only for a local dry run.

| Gate | Purpose | Pass condition |
|---|---|---|
| PTT-0 Repo sanity | Code, compose, and packaging preflight | Target tests pass, compose config parses, Agent image includes runtime modules |
| PTT-1 Center deploy | Bring up the center on Docker or NAS compose | API responds, `/docs` opens, `docker compose ps` is healthy |
| PTT-2 Agent enroll | Start one remote Agent through install script or Docker run | `/api/v1/nodes` shows the node online with `protocol=ws` or `http` |
| PTT-3 Fleet inventory | Project browser/node/runtime inventory | `/api/v1/workflows/fleet/inventory` shows `runtime.miniflow` for the Agent |
| PTT-4 Runtime dispatch | Send one real `agent_task` to the matched Agent | Agent emits `agent_event` frames and a terminal `agent_result` |
| PTT-5 Workflow trace | Run the Market Situation Monitor workflow through Fleet match | `workflow_run_events` contains match, dispatch, runtime events, result, and failure reason if any |
| PTT-6 OpenTabs smoke | Validate OpenTabs compatibility on a prepared node | `/tools` manifest is projected and a read-only tool call succeeds |

## PTT-0 Local Commands

Run these from the repository root. `uv sync --extra dev` creates and maintains
the repository-local `.venv`; do not call its interpreter directly.

```powershell
uv run pytest `
  tests/unit/test_agent_image_runtime_packaging.py `
  tests/unit/test_agent_server.py `
  tests/unit/test_ws_agent_manager.py `
  tests/unit/agent_runtimes/test_miniflow_adapter.py `
  tests/unit/agent_runtimes/test_opentabs_adapter.py `
  tests/integration/test_workflow_fleet_api.py `
  tests/integration/test_workflow_opencli_hda_trace_api.py `
  --no-cov
```

```powershell
docker compose -f docker-compose.yml -f docker-compose.build.yml config --quiet
```

```powershell
C:\c\Users\Administrator\projects\code-intel-pipeline\Invoke-SentruxAgentTool.ps1 check_rules C:\c\Users\Administrator\projects\opencli-admin-backend
```

## PTT-1 Center Docker Bring-up

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d api agent-1
curl -fsS http://127.0.0.1:8031/docs >/dev/null
```

Evidence:

- `docker compose ps`
- API health or `/docs` reachable
- build log proves `agent/Dockerfile` copied `backend/agent_runtimes` and `backend/miniflow`

## PTT-2 Agent Enroll

For a NetBird node, set the setup key only in the operator shell, not in the
repository:

```bash
export API_AUTH_TOKEN=<center-token>
export NETBIRD_SETUP_KEY=<netbird-setup-key>
curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center-netbird-ip-or-dns>:8031/api/v1/nodes/install/agent.sh | \
  FLEET_NETWORK_PROVIDER=netbird AGENT_REGISTER=ws AGENT_MODE=bridge bash -s -- docker
```

For WireGuard, SSH tunnel, or a custom network, the provider only declares the
reachability path. Bring that path up outside the installer, then pass the
center-reachable agent URL explicitly:

```bash
export API_AUTH_TOKEN=<center-token>
export AGENT_ADVERTISE_URL=http://<agent-wireguard-ip-or-forwarded-host>:19823
curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center-reachable-host>:8031/api/v1/nodes/install/agent.sh | \
  FLEET_NETWORK_PROVIDER=wireguard AGENT_REGISTER=ws AGENT_MODE=bridge bash -s -- docker
```

Pass condition:

```bash
curl -fsS -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center>/api/v1/nodes
```

The enrolled node must be online and include a stable label, deploy type, mode,
protocol, last-seen timestamp, and runtime list.

## PTT-3 Fleet Inventory

```bash
curl -fsS -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center>/api/v1/workflows/fleet/inventory
```

Pass condition:

- The Agent appears in `agents[]`.
- `connected=true` for WS enrollment.
- `capabilities[]` includes `runtime.miniflow`.
- OpenTabs nodes include `runtime.opentabs` only when `OPENTABS_BASE_URL` is set
  or the `opentabs` binary is available on that node.

## PTT-4 Runtime Dispatch

Dispatch a MiniFlow workflow file that already exists on the Agent node. For
NAS, use a whitelisted path such as `/volume1/opencli/workflows/market_situation.py`.

Pass condition:

- Center sends an `agent_task` with `runtime=miniflow`.
- Agent emits `started`, per-step `tool_call` and `tool_result`, `state`, then
  terminal `done` or structured `error`.
- Terminal failure still counts as transport pass if it records a typed error
  and audit artifact.

## PTT-5 Workflow Run Trace

Run Market Situation Monitor through the workflow API/UI with Fleet match
enabled.

Pass condition in persisted trace:

- Fleet match event includes selected node endpoint and runtime requirement.
- Dispatch event references the selected Agent.
- Runtime events are written to `workflow_run_events`.
- Result event links any audit artifact path.
- UI can display every step, terminal state, and failure reason.

## PTT-6 OpenTabs Smoke

On the Agent node:

```bash
export OPENTABS_BASE_URL=http://127.0.0.1:9515
export OPENTABS_SECRET=<secret-if-enabled>
```

Pass condition:

- OpenTabs `/health` returns ok.
- `tool.list` returns a non-empty `/tools` manifest.
- One read-only tool call succeeds through `runtime=opentabs`.

## Current Hard Stops

- Docker Agent packaging is part of PTT-0 and must include `backend/agent_runtimes`
  plus `backend/miniflow`; otherwise runtime registration inside the container is
  a false pass.
- Shell/systemd Python install mode currently downloads only `agent_server.py`.
  Treat that mode as blocked for runtime PTT until the installer can distribute
  the runtime adapter package as well.
- MiniFlow workflow file distribution is not solved by Fleet itself. PTT uses a
  pre-positioned NAS path until Git sync, file upload, or a managed workflow
  bundle API is implemented.
- NAS Agent runtime execution is local code execution. PTT must record the
  configured allowlist, workflow directory, bearer token, and audit artifact
  path before the run is accepted.
