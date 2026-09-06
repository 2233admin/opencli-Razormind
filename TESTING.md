# 集成测试

覆盖所有部署组合的 10 场景集成测试流程，用于验证系统核心采集链路。

## 场景总览

| # | 部署方式 | 采集目标 | Chrome 连接模式 | Chrome 来源 | 关键验证点 |
|---|----------|----------|-----------------|-------------|-----------|
| 1 | Shell | 本地 | Bridge | 宿主机原生 | API 直接驱动 opencli，bridge daemon 连接本地 Chrome |
| 2 | Shell | 本地 | CDP | 宿主机原生 | 通过 API 切换模式，无需重启 |
| 3 | Shell | 边缘节点 | Bridge | 宿主机原生 | HTTP dispatch 到 shell 部署的 agent |
| 4 | Shell | 边缘节点 | CDP | 宿主机原生 | API 切换为 cdp，无需重启 agent |
| 5 | Docker | 本地 | Bridge | **宿主机 Chrome**（host.docker.internal） | agent 镜像 ~400MB，COLLECTION_MODE=local |
| 6 | Docker | 本地 | CDP | **宿主机 Chrome** | API 切换模式，无需重启 agent-1 容器 |
| 7 | Docker | 边缘节点 | Bridge | **宿主机 Chrome** | COLLECTION_MODE=agent，dispatch 到 agent-1 |
| 8 | Docker | 边缘节点 | CDP | **宿主机 Chrome** | API 切换 cdp，同一容器无重启 |
| 9 | Docker | 本地 | Bridge | **容器内置 Chrome**（-chrome 镜像） | 无需宿主机 Chrome，Chromium+Xvfb+daemon 内置 |
| 10 | Docker | 本地 | CDP | **容器内置 Chrome**（-chrome 镜像） | CDP 连接容器内 Chromium，完全自包含 |

> **关键原则**：bridge ↔ cdp 模式切换始终通过 `PATCH /api/v1/workers/chrome-pool/{ep}/mode` 完成，agent 容器/进程无需重启。COLLECTION_MODE（local/agent）是系统级配置，修改后需重启 API。

---

## 环境准备

### BMad 技能渲染运行时

`_bmad/scripts/render_skill.py`、`config_utils.py` 及许可证恢复自官方
[`bmad-code-org/BMAD-METHOD@beb368e5fc9b95bcec5e1de5bc7870dc15bece72`](https://github.com/bmad-code-org/BMAD-METHOD/tree/beb368e5fc9b95bcec5e1de5bc7870dc15bece72)；
脚本保持上游原样，仅恢复这两个脚本和许可证，不代表恢复了完整 BMad 安装。
`_bmad/config.toml` 是最小项目配置，不是找回的个人设置；不要提交私人技能覆盖文件。
在仓库根目录渲染已安装的 build 技能：

```bash
uv run python _bmad/scripts/render_skill.py --project-root . --skill .agents/skills/bmad-build
```

生成的机器相关快照位于 `_bmad/render/`，已忽略，不应提交。此命令只验证技能渲染，
不验证 Docker 镜像构建或容器运行。

### 服务准备

```bash
# 启动 Redis（Celery 模式需要；local 模式可跳过）
docker compose up -d redis

# 运行数据库迁移
cd /path/to/opencli-admin
alembic upgrade head         # Shell 模式（本地 DB）
# Docker 模式由 API 容器启动时自动执行
```

定义公共变量（后续步骤复用）：

```bash
# Shell 模式用 8000，Docker 模式用 8031
BASE_SHELL="http://localhost:8000"
BASE_DOCKER="http://localhost:8031"
```

---

## Shell 部署测试（Tests 1–4）

Shell 部署 = API 和 Agent 均以原生进程运行，不涉及 Docker。

### 准备：启动 Chrome

```bash
# 启动 Chrome，开启 CDP 调试端口
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
    --no-first-run --no-default-browser-check &

# 启动 Bridge Daemon（bridge 模式必须）
node $(npm root -g)/@jackwener/opencli/dist/daemon.js &
```

### 准备：启动 Shell API（COLLECTION_MODE=local）

```bash
OPENCLI_CDP_ENDPOINT=http://127.0.0.1:9222 \
COLLECTION_MODE=local \
    .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

API 启动后，pool 里会有 `http://127.0.0.1:9222`，默认 bridge 模式。

---

### Test 1 — Shell + 本地 + Bridge

```bash
# 1. 确认 pool 当前是 bridge 模式
curl -s $BASE_SHELL/api/v1/workers/chrome-pool

# 2. 创建数据源（不填 chrome_endpoint = 使用 pool 默认节点）
SOURCE_ID=$(curl -s -X POST $BASE_SHELL/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Test1-Shell-Local-Bridge","channel_type":"opencli",
       "channel_config":{"site":"v2ex","command":"hot","args":{},"format":"json"},
       "enabled":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

# 3. 手动触发
TASK_ID=$(curl -s -X POST $BASE_SHELL/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

# 4. 等待并查看结果（应 status=completed，records>0）
sleep 15
curl -s $BASE_SHELL/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，`records_collected > 0`，API 日志显示 `opencli bridge | daemon=127.0.0.1:19825`。

---

### Test 2 — Shell + 本地 + CDP

模式切换通过 API 完成，**无需重启任何进程**：

```bash
# 1. 切换 pool 节点到 CDP 模式
EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://127.0.0.1:9222').decode())")
curl -s -X PATCH $BASE_SHELL/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"cdp"}'
# → {"data":{"endpoint":"http://127.0.0.1:9222","mode":"cdp"}}

# 2. 复用 Test1 的数据源，再次触发
TASK_ID=$(curl -s -X POST $BASE_SHELL/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 15
curl -s $BASE_SHELL/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，API 日志显示 `opencli cdp | cdp=http://127.0.0.1:9222`。

---

### Test 3 — Shell + 边缘节点 + Bridge

```bash
# 1. 启动 shell 部署的 edge agent（bridge 模式），注册到中心 API
CENTRAL_API_URL=http://127.0.0.1:8000 \
AGENT_MODE=bridge \
AGENT_DEPLOY_TYPE=shell \
AGENT_PORT=8001 \
AGENT_LABEL="shell-edge-bridge" \
    .venv/bin/python -m uvicorn backend.agent_server:app --host 0.0.0.0 --port 8001 &

sleep 5
# 2. 确认节点已注册（node_type=shell, mode=bridge）
curl -s $BASE_SHELL/api/v1/nodes

# 3. 切换 API 为 agent 采集模式（重启 API 进程）
OPENCLI_CDP_ENDPOINT=http://127.0.0.1:9222 \
COLLECTION_MODE=agent \
    .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 &

# 4. 重启 agent 重新注册到新 API 实例，再触发采集
SOURCE_ID=$(curl -s -X POST $BASE_SHELL/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Test3-Shell-Edge-Bridge","channel_type":"opencli",
       "channel_config":{"site":"v2ex","command":"hot","args":{},"format":"json"},
       "enabled":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

TASK_ID=$(curl -s -X POST $BASE_SHELL/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 15
curl -s $BASE_SHELL/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，API 日志显示 `agent dispatch | url=http://127.0.0.1:8001/collect`，task_run 的 `node_url=http://127.0.0.1:8001`。

---

### Test 4 — Shell + 边缘节点 + CDP

```bash
# 1. 切换 edge agent 节点到 CDP 模式（通过 API，无需重启 agent）
EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://127.0.0.1:8001').decode())")
curl -s -X PATCH $BASE_SHELL/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"cdp"}'

# 2. 复用 Test3 数据源，触发
TASK_ID=$(curl -s -X POST $BASE_SHELL/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 15
curl -s $BASE_SHELL/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，agent 进程日志显示 `cdp | cmd=opencli v2ex hot -f json cdp=http://localhost:9222`。

---

## Docker 部署测试（Tests 5–8）

Docker 部署 = API 和 Agent 均以 Docker 容器运行。**Agent 镜像不含 Chrome**，连接宿主机 Chrome。

### 准备：启动宿主机 Chrome + 构建启动 Docker 服务

```bash
# ── 1. 宿主机启动 Chrome（监听所有网卡，容器可通过 host.docker.internal 访问）
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
    --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 \
    --no-first-run --no-default-browser-check &

# Bridge daemon（bridge 模式必须；cdp 模式可跳过）
node $(npm root -g)/@jackwener/opencli/dist/daemon.js &

# ── 2. 启动 API（COLLECTION_MODE=local，pool 预加载 agent-1）
COLLECTION_MODE=local docker compose up -d api

# ── 3. 启动 agent-1 sidecar（bridge 模式，连接宿主机 Chrome）
AGENT_MODE=bridge docker compose up -d agent-1

sleep 15
# 确认注册（node_type=docker, mode=bridge）
curl -s http://localhost:8031/api/v1/nodes
```

---

### Test 5 — Docker + 本地 + Bridge

```bash
BASE=$BASE_DOCKER

# 1. 确认 pool 是 bridge 模式
curl -s $BASE/api/v1/workers/chrome-pool

# 2. 创建数据源并触发
SOURCE_ID=$(curl -s -X POST $BASE/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Test5-Docker-Local-Bridge","channel_type":"opencli",
       "channel_config":{"site":"v2ex","command":"hot","args":{},"format":"json"},
       "enabled":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

TASK_ID=$(curl -s -X POST $BASE/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 20
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，`records_collected > 0`。

---

### Test 6 — Docker + 本地 + CDP

```bash
BASE=$BASE_DOCKER

# 1. 切换 pool 到 CDP 模式（不重启容器）
EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://agent-1:19823').decode())")
curl -s -X PATCH $BASE/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"cdp"}'

# 2. 复用 Test5 数据源，触发
TASK_ID=$(curl -s -X POST $BASE/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 20
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，agent-1 容器日志显示 `cdp | cmd=opencli v2ex hot -f json cdp=http://localhost:9222`。

---

### Test 7 — Docker + 边缘节点 + Bridge

```bash
# 1. 重启 API 为 agent 采集模式
export COLLECTION_MODE=agent
docker compose up -d --force-recreate api
sleep 8

# 2. 重启 agent-1 重新注册，切换 pool 到 bridge
docker compose restart agent-1
sleep 15

EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://agent-1:19823').decode())")
curl -s -X PATCH $BASE_DOCKER/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"bridge"}'

# 确认注册（mode=bridge）
curl -s $BASE_DOCKER/api/v1/nodes

# 3. 创建数据源并触发
SOURCE_ID=$(curl -s -X POST $BASE_DOCKER/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Test7-Docker-Edge-Bridge","channel_type":"opencli",
       "channel_config":{"site":"v2ex","command":"hot","args":{},"format":"json"},
       "enabled":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

TASK_ID=$(curl -s -X POST $BASE_DOCKER/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 20
curl -s $BASE_DOCKER/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，API 日志显示 `agent dispatch | url=http://agent-1:19823/collect`，`node_url=http://agent-1:19823`。

---

### Test 8 — Docker + 边缘节点 + CDP

```bash
BASE=$BASE_DOCKER

# 1. 切换 agent-1 到 CDP 模式（通过 API，不重启容器）
EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://agent-1:19823').decode())")
curl -s -X PATCH $BASE/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"cdp"}'

# 2. 复用 Test7 数据源，触发
TASK_ID=$(curl -s -X POST $BASE/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 20
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1
```

**预期**：`status=completed`，agent-1 容器日志显示 `cdp | cmd=opencli v2ex hot -f json cdp=http://localhost:9222`，`node_url=http://agent-1:19823`。

---

## Docker 内置 Chrome 测试（Tests 9–10）

这两个 test 验证 `-chrome` 镜像变体（含 Chromium + Xvfb + Bridge Daemon）是否真正自包含，**不依赖宿主机 Chrome**。

### 准备：切换 agent-1 为 -chrome 镜像

> ⚠️ **注意**：手动 `docker stop/rm` 旧容器后，旧节点的 endpoint 仍残留在 API 的 in-memory pool 中，会导致 dispatch 报错（"Name or service not known"）。必须先通过 API 删除旧节点，再启动新容器。

```bash
# 1. 先删除旧 agent-1 节点（清理 pool 和 DB）
NODE_ID=$(curl -s http://localhost:8031/api/v1/nodes | \
  python3 -c "import sys,json; nodes=json.load(sys.stdin)['data']; \
              [print(n['id']) for n in nodes if n['label']=='agent-1']")
curl -s -X DELETE http://localhost:8031/api/v1/nodes/$NODE_ID

# 2. 停止并删除旧容器
docker stop agent-1 && docker rm agent-1

# 3. 用 -chrome 镜像启动（AGENT_MODE=bridge，内置 Chromium 和 daemon）
docker run -d \
  --name agent-1-chrome \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -e CENTRAL_API_URL=http://host.docker.internal:8031 \
  -e AGENT_ADVERTISE_URL=http://host.docker.internal:19824 \
  -e AGENT_PORT=19824 \
  -e AGENT_MODE=bridge \
  -e AGENT_DEPLOY_TYPE=docker \
  -e AGENT_LABEL=agent-1-chrome \
  -e AGENT_REGISTER=http \
  -p 19824:19824 \
  xjh1994/opencli-admin-agent:0.3.2-chrome

sleep 20
# 确认节点注册（应有 label=agent-1-chrome, node_type=docker, mode=bridge）
curl -s http://localhost:8031/api/v1/nodes
```

---

### Test 9 — Docker + 内置Chrome + Bridge

```bash
BASE=$BASE_DOCKER

# 1. 确认 COLLECTION_MODE=agent，pool 里有 agent-1-chrome 且 mode=bridge
curl -s $BASE/api/v1/system/config
curl -s $BASE/api/v1/workers/chrome-pool

# 2. 创建数据源并触发
SOURCE_ID=$(curl -s -X POST $BASE/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Test9-Docker-ChromeBuiltin-Bridge","channel_type":"opencli",
       "channel_config":{"site":"v2ex","command":"hot","args":{},"format":"json"},
       "enabled":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")

TASK_ID=$(curl -s -X POST $BASE/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 25
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1 | python3 -m json.tool
```

**预期**：`status=completed`，`records_collected > 0`。容器日志显示内置 Chromium 启动，`bridge | daemon=127.0.0.1:19825`。宿主机可关闭 Chrome，采集仍成功。

```bash
docker logs agent-1-chrome --tail=30
```

---

### Test 10 — Docker + 内置Chrome + CDP

```bash
BASE=$BASE_DOCKER

# 1. 切换 agent-1-chrome 到 CDP 模式（不重启容器）
EP_B64=$(python3 -c "import base64; print(base64.urlsafe_b64encode(b'http://host.docker.internal:19824').decode())")
curl -s -X PATCH $BASE/api/v1/workers/chrome-pool/$EP_B64/mode \
  -H "Content-Type: application/json" -d '{"mode":"cdp"}'

# 2. 复用 Test9 数据源，触发
TASK_ID=$(curl -s -X POST $BASE/api/v1/tasks/trigger \
  -H "Content-Type: application/json" \
  -d "{\"source_id\":\"$SOURCE_ID\",\"trigger_type\":\"manual\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

sleep 25
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1 | python3 -m json.tool
```

**预期**：`status=completed`，容器日志显示 `cdp | cmd=opencli v2ex hot -f json cdp=http://localhost:9222`（连接容器内部 Chromium CDP）。

---

## 结果验证

每个 test 完成后，检查以下指标：

```bash
# 查看任务运行记录
curl -s $BASE/api/v1/tasks/$TASK_ID/runs?limit=1 | python3 -m json.tool
# 关注: status=completed, records_collected>0, error_message=null

# 查看 API 日志（Shell 模式）
# 关键日志行：
#   "opencli bridge | cmd=... daemon=..."  → 本地 bridge 采集
#   "opencli cdp | cmd=... cdp=..."        → 本地 CDP 采集
#   "agent dispatch | url=...  site=..."   → agent 模式分发
#   "agent done | ... items=N"             → agent 返回结果

# 查看 agent-1 容器日志（Docker 模式）
docker logs agent-1 --tail=20
# 关键日志行：
#   "bridge | cmd=opencli ... daemon=localhost:19825"  → bridge 模式执行
#   "cdp | cmd=opencli ... cdp=http://localhost:9222"  → CDP 模式执行
```

## 已知行为

- **重复数据返回 0 条**：同一来源的数据通过 `content_hash` 去重，重复触发同一数据源时 `records_collected=0` 但 `status=completed` 是正常现象。
- **`browser: false` 的站点（v2ex hot、HN 等）不区分 bridge/cdp**：这类站点直接调用公开 HTTP API，不使用浏览器，两种模式效果相同。需要验证 bridge 与 CDP 真实差异请使用需要浏览器的站点（如 linux-do、zhihu 等）。
- **COLLECTION_MODE 切换需重启 API**：这是系统级配置，对应用户修改 `.env` 后执行 `docker compose up -d api` 的正常运维操作。bridge/cdp 模式切换则无需重启，通过 `PATCH /mode` 接口实时生效。
- **Docker 测试前需在宿主机启动 Chrome**：agent 镜像默认使用无 Chrome 变体（约 400 MB），Tests 5-8 依赖宿主机 Chrome 通过 `host.docker.internal` 提供浏览器能力。如需完全自包含，在 `.env` 中设置 `INSTALL_CHROME=true` 和 `CHROME_SUFFIX=-chrome`，重启后会拉取 `-chrome` 变体（约 1.2 GB）。
- **切换 agent 容器后需清理旧节点**：手动 `docker stop/rm` 旧 agent 后，旧 endpoint 仍残留在 in-memory pool 中（节点 DB 也未清理）。切换前需通过 `DELETE /api/v1/nodes/{id}` 主动删除旧节点，或重启 API 让新 agent 重新注册后再清理。Tests 9-10 中切换到 `-chrome` 镜像时需先删除旧 `agent-1` 节点。

---

## Skill 执行回路（CDP 浏览器驱动）

Skill 执行回路（`backend/skills/page.py` + `backend/skills/perception.py`）通过 Playwright
**连接已在运行的 Chrome**（`connect_over_cdp`）来驱动页面 —— 复用 `browser_pool` 提供的、
与上面 Tests 1–10 相同的那个 Chrome（用 `--remote-debugging-port=9222` 启动）。它**不会**另起
一个浏览器，运行时只需要 Playwright **驱动**本身，不需要第二个 Chrome。

```bash
# 安装 Playwright 驱动（Windows / win32 开发或 CI 都需要执行一次）
playwright install chromium
```

- Playwright 是新的后端依赖（`pyproject.toml` 已加入 `playwright>=1.40.0`）。`playwright install
  chromium` 只装**驱动**；执行回路是 `connect_over_cdp` **挂接**到 `browser_pool` 里那个已经运行
  的 Chrome（沿用其已登录会话），所以运行时不需要额外的浏览器实例。
- 仅支持本地 + LAN 的 CDP endpoint（ADR-0003 D1）；通过 `agent_server` 驱动 NAT 边缘节点是 v2。
- 依赖真实浏览器的路径走既有的 `live` pytest marker；默认的 `pytest -m "not live"` **不需要浏览器**
  —— 感知快照的纯解析逻辑（`project_snapshot`）和页面包装器的 ref 解析都用 mock 覆盖（见
  `tests/skills/test_perception.py`）。

---

## 技能执行环路 e2e（live marker，Windows）

`tests/skills/test_execute_loop_live.py` 跑**真实本地 Chromium 内核浏览器**的端到端测试：它通过
CDP 把整条 `perceive → act → extract → done` 环路、以及 headless 写前确认闸门，对着一个**本机静态
页面**真跑一遍（issue 07）。`tests/skills/test_cookie_persistence_live.py` 补一条：证 cookie/登录态
在两次独立 `connect_over_cdp`（attach → detach → 重新 attach，等价生产里两次任务复用同一 endpoint）
之间真的没丢——不是靠"CDP 挂接不落地新 context"这条架构推理去信,是真设 cookie 断开重连读回来验证过。

两个文件都标了 `@pytest.mark.live`，所以默认的 `pytest -m "not live"`（带 `--cov-fail-under=80`）
**永远不需要浏览器**。浏览器是真的；只有 `test_execute_loop_live.py` 里便宜模型的**动作选择**被脚本
固定（patch `backend.channels.skill_channel._build_model_call`），避免模型抖动让 live 测试变 flaky。

**Chrome 和 Edge 都真跑过**（2026-07-02）：`connect_over_cdp` 走纯 CDP 协议，不认浏览器厂商——只要
给个 `--remote-debugging-port` endpoint 就行，不是 Chrome 专属。以下步骤两个浏览器通用，示例用 Chrome，
Edge 见下方"多浏览器"小节。

为新机器（win32）从零复现：

1. 安装 Playwright + 其 Chromium 驱动（每台机器一次性）：

   ```bash
   uv pip install playwright      # 或 pip install playwright（issue 01 起已是后端依赖）
   playwright install chromium
   ```

   说明（PRD §7）：执行环路是 `connect_over_cdp` **挂接**到一个已经在跑的 Chrome，所以这里装的
   Chromium 是给 Playwright **驱动**用的，不一定要再开第二个浏览器。

2. 用 CDP 调试端口启动一个本地 Chrome（Windows 路径）：

   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" `
     --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 `
     --no-first-run --no-default-browser-check
   ```

3. 指向它，跑 live 技能测试（连带 cookie 持久化那条）：

   ```powershell
   $env:SKILL_LIVE_CDP_ENDPOINT = "http://127.0.0.1:9222"
   uv run pytest -m live tests/skills/test_execute_loop_live.py tests/skills/test_cookie_persistence_live.py
   ```

   测试从 `SKILL_LIVE_CDP_ENDPOINT` 读 endpoint（回退到 `OPENCLI_CDP_ENDPOINT`）。**未设置时**它会
   `pytest.skip(...)` 并给出可操作的提示，而不是 fail。被测页面由测试内嵌的 `ThreadingHTTPServer`
   起在 `127.0.0.1:<随机端口>`，不依赖任何外部站点。

   **多浏览器（Edge / 其他 Chromium 内核）：**

   ```powershell
   & "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
     --remote-debugging-port=9223 --remote-debugging-address=127.0.0.1 `
     --no-first-run --no-default-browser-check `
     --user-data-dir="<某个持久目录>"
   ```

   ⚠️ **踩坑**：Edge 即使给了 `--remote-debugging-address=127.0.0.1`，实测仍只绑
   `::1`（IPv6 loopback），`http://127.0.0.1:9223/json/version` 会超时。用
   `http://localhost:9223`（Windows 上 `localhost` 优先解析到 `::1`）或直接
   `http://[::1]:9223`。Chrome 没这毛病，`127.0.0.1` 直接通。

4. 默认套件**不含**它（CI / 本地日常都走这条，不需要 Chrome）：

   ```powershell
   uv run pytest -m "not live"   # 带 --cov-fail-under=80；无需浏览器
   ```

   确认它确实被默认排除：

   ```powershell
   uv run pytest -m "not live" --collect-only -q | Select-String "test_execute_loop_live|test_cookie_persistence_live"   # 应无匹配
   ```

5. DB 说明：live 测试把一个**临时 SQLite**（默认内存库，`StaticPool` 单连接共享）绑进
   `backend.database.AsyncSessionLocal` 和 runner 的那份拷贝，这样环路通过 `events.emit` 写的
   `TaskRunEvent` 行对测试自己的查询可见（conftest 里那个 per-test 内存 `db_session` 是**另一个**库，
   环路不会写它）。需要事后翻库时，可改设 `DATABASE_URL` 指向一个一次性文件库。`playwright install
   chromium` 是每台机器一次性的准备步骤。

## Release Contract 本地复现

在仓库根目录使用 Python 3.13、uv 和原生 Bash（需可调用 `openssl`），执行与
`.github/workflows/release.yml` 相同的发布契约检查：

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest tests/unit/test_public_release_contract.py --noconftest --no-cov -q
```

依赖统一来自项目的锁文件及 `dev` extra，避免在发布工作流维护另一份版本固定列表。
`--noconftest` 隔离应用级 `tests/conftest.py`，不禁用 pytest 内置的 `tmp_path`
fixture 或参数化用例；`--no-cov` 仅为此定向发布门禁关闭无关的全应用覆盖率阈值。
应运行该文件的全部契约用例且无跳过，包括真实密码初始化成功和失败的路径。
测试替代了 Docker 调用，不代表已验证镜像构建、容器部署或标签发布。
