# opencli-Razormind

[![GitHub Release](https://img.shields.io/github/v/release/2233admin/opencli-Razormind)](https://github.com/2233admin/opencli-Razormind/releases/latest)
[![CI](https://github.com/2233admin/opencli-Razormind/actions/workflows/ci.yml/badge.svg)](https://github.com/2233admin/opencli-Razormind/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/2233admin/opencli-Razormind)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-amd64%20%7C%20arm64-2496ED?logo=docker&logoColor=white)](https://github.com/2233admin/opencli-Razormind/pkgs/container/opencli-admin-api)

opencli-Razormind 是一个开源、自托管的研究与情报管线。它把浏览器 / OpenCLI / RSS / API 数据采集、可视化工作流、AI 处理、证据关系和结果交付，统一到一个可运行、可审计的系统中。

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Stars](https://img.shields.io/github/stars/2233admin/opencli-Razormind)
![Last Commit](https://img.shields.io/github/last-commit/2233admin/opencli-Razormind)
![Python](https://img.shields.io/badge/Python-3.13+-3776AB)

- **一句话**：开源自托管的研究情报管线，从采集到交付一个系统跑完。
- **适合谁**：做研究或情报收集，想把采集、工作流、AI 处理、证据和交付串起来的。
- **不适合谁**：只需要一个单点爬虫脚本、不想运维一套系统的。

## 为什么做这个

做研究或情报收集的时候，工具链总是碎的：浏览器登录态在本地、爬虫脚本在服务器、数据在 Excel、分析在另一个工具、交付靠手动复制粘贴。这个系统想把整条链路串起来——从登录采集到工作流编排，到 AI 处理，到证据关系，到定时交付，都在一个界面里完成。

<p align="center">
  <img src="docs/screenshots/project-overview.png" alt="opencli-Razormind 项目概览" width="100%" />
</p>

图中的网站变化监控项目已发布不可变 `v1`，并完成了基于该发布版本的真实运行与 Trace 记录。

当前公开版本 **v0.4.1** 已打通：

**登录采集账号 → 创建研究项目 → 编排工作流 → 执行与追踪 → 查看记录和证据 → 定时运行 / 对外交付**

## 安装版本边界

前置要求：Docker 与 Docker Compose。

当前公开版本 **v0.4.1 是不可变的旧版发布**。它早于本分支新增的随机本地管理员密码、持久化恢复基线、重启后认证校验和浏览器降级启动契约。不要把 `v0.4.1` 的安装器或镜像当作这些新行为已经公开发布的证据；需要旧版时请从 [v0.4.1 Release](https://github.com/2233admin/opencli-Razormind/releases/tag/v0.4.1) 获取并遵循该版本自己的说明。

这些新行为目前只存在于当前源码分支，**尚无可引用的 next-release 标签或一键安装 URL**。开发者可从源码构建当前分支：

~~~bash
git clone https://github.com/2233admin/opencli-Razormind.git
cd opencli-Razormind
cp .env.docker.example .env
# 为 .env 中的必填令牌和密钥设置非默认值后：
# 仅首次执行以下初始化步骤；请保存 .local-admin-password 中的随机密码。
local_admin_password_file=.local-admin-password
abort_local_admin_password() {
  echo "$1" >&2
  unset local_admin_password
  exit 1
}
validate_local_admin_password() {
  if ! printf '%s' "$local_admin_password" | grep -Eq '^[0-9A-Fa-f]{48}$'; then
    abort_local_admin_password "The local administrator password must be exactly 48 hexadecimal characters."
  fi
}
if ! IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml build api frontend agent-1; then
  abort_local_admin_password "Could not build the source images."
fi
if [ -s "$local_admin_password_file" ]; then
  if ! local_admin_password="$(cat "$local_admin_password_file")"; then
    abort_local_admin_password "Could not read $local_admin_password_file."
  fi
else
  umask 077
  if ! local_admin_password="$(openssl rand -hex 24)"; then
    abort_local_admin_password "Could not generate a local administrator password."
  fi
fi
validate_local_admin_password
if [ ! -s "$local_admin_password_file" ]; then
  if ! printf '%s\n' "$local_admin_password" > "$local_admin_password_file"; then
    abort_local_admin_password "Could not save $local_admin_password_file."
  fi
fi
if ! chmod 600 "$local_admin_password_file"; then
  abort_local_admin_password "Could not protect $local_admin_password_file."
fi
# initialize_password_hash 只写入一次 /data/local-admin-password.hash 及其
# /data/local-admin-password.hash.initialized marker；不要重复执行此初始化命令。
if ! printf '%s' "$local_admin_password" | IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml run --rm -T --no-deps api python -c \
    'import sys; from backend.security.local_auth import hash_password, initialize_password_hash; initialize_password_hash(hash_password(sys.stdin.read().strip()), "/data/local-admin-password.hash")'; then
  abort_local_admin_password "Could not initialize the local administrator password."
fi
unset local_admin_password
# 随后（以及今后的启动）：
IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --no-build --wait
~~~

下一版本获得维护者授权、创建标签并通过 candidate-image smoke 后，Release 页面才会给出与该标签绑定的 Linux/macOS 与 Windows 一键安装命令。不要自行把 `v0.4.1` URL 替换成一个尚未发布的版本号。

| 入口 | 地址 | 用途 |
| --- | --- | --- |
| 管理界面 | http://localhost:3010 | 项目、工作流、运行和数据 |
| API 文档 | http://localhost:8031/docs | REST API 与集成调试 |
| 内置浏览器 | http://localhost:6080 | 扫码或登录需要账号的平台 |

完成上述源码初始化后可使用本地管理员账号登录：

- 用户名：`admin`
- 密码：首次初始化生成并保存在 `.local-admin-password` 中的随机密码（请立即保存；不是 `admin`；`v0.4.1` 不具备此契约）
- 登录后可在「账户设置」修改密码

`API_AUTH_TOKEN` 仅由 Fleet、Agent、API 和 MCP 传输使用，自动保存在安装目录的 `.env`，不需要填入管理界面。不要公开 API 令牌、noVNC 或浏览器调试端口；远程部署建议使用 HTTPS、反向代理或 SSH 隧道。

## 浏览器引擎与 CloakBrowser

内置浏览器在未设置 `BROWSER_ENGINE` 时默认使用 `chromium`；显式空值或不支持的引擎值会报错退出，不会回退。Agent 保留对已安装 Chromium 的自动检测，也识别镜像内置运行时标记 `AGENT_HAS_CHROME=true`，不只依赖系统 `chromium` 命令来判断是否启动内置浏览器。需要切换到 CloakBrowser 时，先在未提交的 .env 中设置构建和运行配置；BROWSER_ENGINE 必须与镜像构建时的变体一致：

~~~bash
BROWSER_ENGINE=cloakbrowser
CLOAKBROWSER_VERSION=0.5.10
CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser/cache
CLOAKBROWSER_LICENSE_KEY=your-runtime-license-key

docker compose -f docker-compose.yml -f docker-compose.build.yml build agent-1
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --no-build agent-1
~~~

CLOAKBROWSER_VERSION 只作为构建参数使用，CLOAKBROWSER_LICENSE_KEY 只在运行时注入，不会写入镜像。不要把密钥写入仓库、Dockerfile、构建参数或日志。引擎启动失败会直接报告，不自动降级到 Chromium。

## Restart recovery and support

> **Release boundary:** the recovery behavior and verification gate described in this section are changes on the current source branch. They are not present in the immutable `v0.4.1` source archive or `v0.4.1` public images. They require the next authorized release before public-install users can rely on them; no next-release tag or installer URL exists yet.

The default `api`, `frontend`, and `agent-1` services use `restart: unless-stopped` and durable named volumes. On native Linux, the installer can verify only the narrow boot prerequisite described below; it never claims to have tested a host restart and never enables or changes a host service. An unverified result does not fail installation: follow the exact commands printed by the installer after the next host restart. Do not run `docker compose up`, `start`, or `restart` during that check; automatic recovery is the property being verified.

| Host | Install support | Host-restart recovery |
| --- | --- | --- |
| Native Linux with Docker Engine + systemd | Supported | The boot prerequisite is reported as verified only with the default Docker context, no `DOCKER_HOST`, and an enabled Docker systemd unit. This is not a claim that the installer tested a host reboot; the Linux CI gate separately restarts the daemon and verifies all three services plus database, authentication, and browser-profile persistence. |
| macOS with Docker Desktop | Supported | Unverified until Docker Desktop login startup is enabled and the printed post-restart checks pass. |
| Windows with Docker Desktop | Supported | Unverified even when the service is automatic, because full Desktop engine startup still requires the printed post-restart checks. |
| WSL | Development use only | Production host-restart recovery is not claimed because there is no WSL reboot gate. |

If `agent-1` is unhealthy, the API and frontend still start so the control plane and diagnostics remain reachable. Browser-dependent collection stays unavailable until the browser reports ready; the UI must not interpret control-plane health as browser readiness.

Persistent data lives in the Compose named volumes `db_data` and `agent_profile_1`. The next-release installer stores a non-secret pre-restart baseline in the install directory and prints a safely quoted verification command. After a restart, that check compares the database revision and volume sentinels, requires the current local-auth marker to have a strict supported value, requires the current durable password hash to load and validate structurally through `local_auth.load_password_hash`, and performs an authenticated identity request with the currently persisted container credentials without printing tokens or hashes. A legitimate password change is therefore allowed and is not compared with the installation-time hash. The check also restores the persisted `COMPOSE_PROJECT_NAME`, so it cannot silently inspect another Compose project. Do not use `docker compose down -v` during recovery because `-v` deletes those volumes. A plain container restart also does not reload host `.env` values; deployment-environment changes require an intentional container recreate followed by health and identity verification.

API restart behavior for work already in flight is unchanged: legacy collection tasks in `pending`, `running`, or `ai_processing` and Operations Agent runs in `queued` or `running` are marked failed with an interruption reason. Managed acquisition executions are requeued. The local scheduler does not replay schedules missed while the process was down; after startup it establishes a new watermark, and multiple fires observed within one later tick are coalesced into one dispatch.

## 正常的研究流程

1. 打开 `:6080`，在内置 Chromium 中扫码或登录目标平台。公开 RSS、API 和网页来源可跳过这一步。
2. 在「插件中心」确认 OpenCLI、RSS、API 或工具能力，在「项目」中从模板或空白项目开始。
3. 在 Dify 风格的画布中连接来源、处理、Agent、Gate 和交付节点；右侧参数面板配置当前节点实际声明的业务参数。
4. 保存、验证并发布工作流，手动执行已发布版本；Webhook 也可直接提交 `workflowProject` 触发运行。
5. 在运行记录中查看节点事件、Trace、错误、重试和输出；采集结果统一进入「成果与数据」。
6. 在项目内查看数据、逻辑与证据、证据关系和 Galaxy 视图。Galaxy 是证据关系的一种查看方式，不是独立的项目模块。
7. 配置 Webhook、飞书、钉钉、企业微信或 Email，将通过规则和质量门的数据交付出去。

## 产品界面

### 可视化工作流

来源、处理、校验和数据集节点在同一画布完成编排；草稿、验证、发布和运行使用同一项目上下文。

![opencli-Razormind 工作流画布与参数面板](docs/screenshots/workflow-inspector.png)

### 统一数据结果

采集结果保留原始数据、标准化字段和完整血缘，可搜索、查看详情，也可继续进入 AI、关系分析和交付节点。

![opencli-Razormind 数据结果与管线血缘](docs/screenshots/record-detail.png)

### 证据与关系

项目证据、实体关系和 Galaxy 共用同一份项目数据，支持搜索、图谱控制和运行证据回溯。

![opencli-Razormind 证据关系](docs/screenshots/evidence-relationships.png)

### 运行与治理

每次运行都能看到发布版本、状态、触发方式、节点事件、耗时和 Trace；下图第一条记录即为 `Published v1` 的成功运行。

![opencli-Razormind 运行记录](docs/screenshots/run-operations.png)

## 已支持的能力

| 层次 | 能力 |
| --- | --- |
| 项目与工作流 | 项目模板、可视化节点编排、草稿、验证、版本发布、运行记录 |
| 数据采集 | OpenCLI 浏览器适配、RSS、REST API、网页抓取、CLI / 工具节点 |
| 登录态 | Docker 内置 Chromium + noVNC；Bridge / CDP；浏览器 Profile 持久化 |
| 处理与分析 | 归一化、去重、AI 摘要 / 标签、关系与证据视图、Kats 时序工具（可选运行时） |
| 自动化 | 已发布版本手动执行、Webhook ingress、节点级事件、重试与可观测 Trace |
| 交付 | Webhook、飞书、钉钉、企业微信、Email，以及 API / MCP 消费 |
| 执行资源 | 单机内置浏览器执行资源；可选远程 Agent、WS 反向通道、HTTP 直连与按站点路由 |

OpenCLI 提供小红书、Bilibili、知乎、微博、X / Twitter、Reddit、YouTube、LinkedIn、Hacker News、财经和公开内容等适配能力。实际可用性取决于上游适配器、地区、登录态、站点风控和页面变更；请在自己的账号与网络环境中先运行连接测试。

## 默认、需配置与可选能力

| 开箱即用 | 配置后可用 | 可选部署 |
| --- | --- | --- |
| 项目 / 工作流 Studio | 需要登录的平台采集 | 远程多机 Agent |
| SQLite 数据库 | 模型提供方与 AI 处理 | PostgreSQL |
| 内置 Chromium / noVNC | 通知与交付渠道 | Redis + Celery |
| 记录、运行和证据界面 | OIDC、API / MCP 客户端 | Kats、Dify / Graphon、ODP / III 等隔离运行时 |

默认安装不会下载组织私有适配包，也不会伪造第三方凭证或交付成功。AI、通知和需要登录的平台必须由部署者显式配置。

## 分布式采集

需要把登录态留在本地电脑，或让多台机器分担不同站点时，可注册远程 Agent：

~~~text
opencli-Razormind (:3010 / :8031)
        │
        ├── WS 反向通道 ── Agent A ── 已登录的小红书 / Bilibili
        ├── WS 反向通道 ── Agent B ── 已登录的 X / LinkedIn
        └── HTTP 直连   ── Agent C ── RSS / 公开网页
~~~

进入「执行资源」→「新增节点」，系统会按当前部署生成安装命令。Agent 路由优先级：

1. 本次运行手动指定；
2. 自动化 / 计划绑定；
3. 站点绑定；
4. 自动选择可用实例。

远端机器默认使用 `19823` 端口。NAT 或跨网环境优先使用 WS 反向通道，无需在 Agent 侧开放入站端口。

## 架构

~~~mermaid
flowchart LR
    A["浏览器 / OpenCLI / RSS / API"] --> B["Project Workflow"]
    B --> C["Run + Node Events + Trace"]
    C --> D["Records + Evidence + Artifacts"]
    D --> E["AI / Rules / Relationships"]
    E --> F["Webhook / IM / Email / API / MCP"]
    G["Local or Remote Agent"] --> A
    H["Webhook / Human"] --> B
~~~

- 前端：Next.js 16、React 19、TypeScript、Tailwind CSS
- 后端：FastAPI、SQLAlchemy 2.0 async、Alembic
- 默认数据层：SQLite；可选 PostgreSQL
- 执行层：本地 asyncio；可选 Celery / Redis 与远程 Agent
- 浏览器：Chromium、noVNC、OpenCLI Bridge / CDP

更完整的对象模型和边界见 [CONTEXT.md](CONTEXT.md)，系统结构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 从源码开发

前置要求：Python 3.13+、Node.js 26.3.1（见 `.nvmrc`）、uv、pnpm。

~~~bash
git clone https://github.com/2233admin/opencli-Razormind.git
cd opencli-Razormind

uv sync
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8031
~~~

另开终端：

~~~bash
cd frontend
pnpm install
pnpm dev --hostname 127.0.0.1 --port 3010
~~~

常用验证：

~~~bash
npm run lint:frontend
npm run typecheck:frontend
npm run build:frontend
uv run pytest
~~~

从源码构建完整 Docker 栈：

~~~bash
cp .env.docker.example .env
# 设置 API_AUTH_TOKEN、BOOTSTRAP_ADMIN_TOKEN、SECRET_KEY、CREDENTIAL_ENCRYPTION_KEY
# 仅首次执行以下初始化步骤；请保存 .local-admin-password 中的随机密码。
local_admin_password_file=.local-admin-password
abort_local_admin_password() {
  echo "$1" >&2
  unset local_admin_password
  exit 1
}
validate_local_admin_password() {
  if ! printf '%s' "$local_admin_password" | grep -Eq '^[0-9A-Fa-f]{48}$'; then
    abort_local_admin_password "The local administrator password must be exactly 48 hexadecimal characters."
  fi
}
if ! IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml build api frontend agent-1; then
  abort_local_admin_password "Could not build the source images."
fi
if [ -s "$local_admin_password_file" ]; then
  if ! local_admin_password="$(cat "$local_admin_password_file")"; then
    abort_local_admin_password "Could not read $local_admin_password_file."
  fi
else
  umask 077
  if ! local_admin_password="$(openssl rand -hex 24)"; then
    abort_local_admin_password "Could not generate a local administrator password."
  fi
fi
validate_local_admin_password
if [ ! -s "$local_admin_password_file" ]; then
  if ! printf '%s\n' "$local_admin_password" > "$local_admin_password_file"; then
    abort_local_admin_password "Could not save $local_admin_password_file."
  fi
fi
if ! chmod 600 "$local_admin_password_file"; then
  abort_local_admin_password "Could not protect $local_admin_password_file."
fi
# initialize_password_hash 只写入一次 /data/local-admin-password.hash 及其
# /data/local-admin-password.hash.initialized marker；不要重复执行此初始化命令。
if ! printf '%s' "$local_admin_password" | IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml run --rm -T --no-deps api python -c \
    'import sys; from backend.security.local_auth import hash_password, initialize_password_hash; initialize_password_hash(hash_password(sys.stdin.read().strip()), "/data/local-admin-password.hash")'; then
  abort_local_admin_password "Could not initialize the local administrator password."
fi
unset local_admin_password
# 随后（以及今后的启动）：
IMAGE_TAG=source docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --no-build --wait
~~~

## 发布镜像

v0.4.1 同时发布 `linux/amd64` 和 `linux/arm64`：

- `ghcr.io/2233admin/opencli-admin-api:0.4.1`
- `ghcr.io/2233admin/opencli-admin-frontend:0.4.1`
- `ghcr.io/2233admin/opencli-admin-chrome:0.4.1`
- `ghcr.io/2233admin/opencli-admin-agent:0.4.1`
- `ghcr.io/2233admin/opencli-admin-agent:0.4.1-chrome`

查看 [最新 Release](https://github.com/2233admin/opencli-Razormind/releases/latest)。

## 文档与贡献

- [测试与验收](TESTING.md)
- [设计系统](docs/DESIGN_SYSTEM.md)
- [数据模型](docs/schema.md)
- [开发规范](docs/DEVELOPMENT_STANDARD.md)
- [架构决策记录](docs/adr)

Issue 与 PR 均在本仓库公开协作；开发计划和任务统一进入 GitHub Issues，长期架构决策进入 `docs/adr`。提交功能前请先运行与改动范围对应的最小测试，再运行前端 lint / typecheck / build 或后端测试。

## 同生态项目

- [OhMyOpenCLI](https://github.com/2233admin/OhMyOpenCLI) — OpenCLI 缺口站点 adapter 库
- [qmtcli](https://github.com/2233admin/qmtcli) — QMT 的本地 JSON CLI 桥
- [geo-xi](https://github.com/2233admin/geo-xi) — 本地内容与可见性系统

## License

[Apache License 2.0](LICENSE)
