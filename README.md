# OpenCLI Admin

[![GitHub Release](https://img.shields.io/github/v/release/2233admin/opencli-admin)](https://github.com/2233admin/opencli-admin/releases)
[![CI](https://github.com/2233admin/opencli-admin/actions/workflows/ci.yml/badge.svg)](https://github.com/2233admin/opencli-admin/actions/workflows/ci.yml)

**现代化的数据采集系统** — 可视化管理多渠道数据采集，接入 [opencli](https://github.com/jackwener/opencli) 驱动国内外主流平台，支持 AI 处理、多节点分布式调度与实时通知推送。

## 当前仓库边界

- 本仓库是 OpenCLI Admin 单一主仓库，包含前端、后端、Agent、Chrome extension、III/ODP 与编排能力。
- 新前端位于 `frontend/`，以前端 Canvas/HDA WorkflowProject 为编排主线。
- 后端位于 `backend/`，前端通过 `/api/v1/*` 与 `/health` 代理对接后端。
- 扩展仍在 `chrome/extension-src/` 下独立构建。

**OpenCLI WebUI** OpenCLI 可视化界面 [opencli-webui](https://github.com/2233admin/opencli-webui)

**仪表盘**
<img width="1600" height="900" alt="dashboard" src="https://raw.githubusercontent.com/2233admin/opencli-admin/main/docs/dashboard.png" />

**Agent 节点自动路由**
<img width="949" height="726" alt="clipboard-image-1774003758" src="https://github.com/user-attachments/assets/2838af3b-2ecb-4d3b-8d8e-21c69db174fc" />

**AI 智能体自动打标签**
<img width="839" height="221" alt="566120897-994046c5-88ae-436f-8108-3327108cb2cc" src="https://github.com/user-attachments/assets/f0c59128-f74e-4cb7-84ee-87818743a4b6" />

## 功能

- **数据看板** — 7 天趋势折线图、采集量柱状图、今日/昨日对比、最近执行一览
- **数据源管理** — opencli、RSS、API、Web 爬虫、CLI 五种渠道；卡片布局，搜索 + 类型筛选，一键测试连通性
- **定时计划** — 结构化 cron（每 N 分钟/小时/天/周/月），支持时区与一次性执行，可绑定专用 Agent 实例
- **采集任务** — 实时任务状态、逐步执行链路追踪、错误详情；手动触发可临时指定节点
- **采集记录** — 全文搜索（normalized_data / raw_data），行展开查看格式化 JSON + AI 分析，多选批量删除
- **节点管理** — 采集模式卡片（本地模式 / Agent 模式）一键切换；本地模式展示本地 Chrome 端点及 Bridge/CDP 切换，Agent 模式展示远端节点列表及新增节点向导；在线状态、注册时间线、按站点绑定路由
- **AI 智能体** — 采集后自动分析、摘要、打标签；支持 Claude、OpenAI、DeepSeek、Kimi、GLM、MiniMax、Ollama
- **通知推送** — 新记录入库 / AI 完成 / 任务失败触发；Webhook、飞书、钉钉、企业微信、Email

### 支持平台（opencli 渠道）

| 分类 | 平台 |
|------|------|
| 🇨🇳 国内 | 小红书、Bilibili、知乎、微博、V2EX、雪球、什么值得买、Boss直聘、携程、小宇宙、新浪财经 7x24 |
| 🌐 Public | Hacker News、BBC、Reuters、Bloomberg、arXiv、Wikipedia |
| 🌍 Global | Twitter/X、Reddit、YouTube、LinkedIn、Yahoo Finance、Barchart |

## 典型场景

### 多台 Mac Mini 组成采集集群

家里几台闲置 Mac Mini，各自登录不同账号，中心统一调度：

```
Mac Mini A（主控）              Mac Mini B                 Mac Mini C
┌──────────────────┐    WS     ┌─────────────────┐   WS  ┌─────────────────┐
│ opencli-admin    │◄──────────│ opencli-agent   │       │ opencli-agent   │
│ 管理界面 :3010   │◄──────────│ 登录：B站/小红书 │       │ 登录：Twitter/X │
│ API :8031        │           │ 采集国内平台     │       │ 采集海外平台     │
└──────────────────┘           └─────────────────┘       └─────────────────┘
```

B / C 两台用 Shell 脚本一键安装 Agent，WS 反向通道注册，穿透 NAT。在「节点管理」按站点绑定，任务自动路由。

---

### 云服务器 + 本地 Mac 混合采集

本地 Mac 持有登录 Cookie，云端负责公开数据高并发抓取：

```
本地 Mac（家庭网络）                云服务器（公网）
┌──────────────────┐              ┌──────────────────────────┐
│ opencli-agent    │──── WS ─────►│ opencli-admin（中心）     │
│ 登录：微博/知乎   │              │                          │
│       小红书/B站  │              │  内置 Agent               │
└──────────────────┘              │  HackerNews / RSS / BBC   │
                                  └──────────────────────────┘
```

敏感 Cookie 留在本地，公开数据走云端，不出内网。

---

### 单机全能采集（最简部署）

```bash
curl -fsSL https://raw.githubusercontent.com/2233admin/opencli-admin/v0.4.0/scripts/install.sh | sh
```

安装完成后打开 `http://localhost:3010`，再打开 `http://localhost:6080` 进入内置浏览器扫码或登录平台账号。

---

### 定时抓取 + AI 摘要 + 推送工作流

无需写代码，全程可视化配置：

1. **数据源** — 添加知乎热榜、HN、Twitter 列表
2. **定时计划** — 每小时执行，绑定对应 Agent
3. **AI 智能体** — 采集完成后自动调用 DeepSeek 提取摘要、打标签
4. **通知推送** — AI 完成后推送到飞书 / 企业微信

每天早晨打开飞书，信息流已处理好等你阅读。

---

### 舆情监控实战闭环

当前实战链路已经投到真实运行面，而不是只停留在配置说明：

1. **多账号 / 多节点采集** — 通过「节点管理」和站点绑定，把 `opencli` 采集路由到指定 WS agent；验收脚本会证明 `chrome_endpoint` 和 `node_url` 都落在绑定节点。
2. **AI 摘要与打标** — `collect → normalize → store → ai → notify` 流水线会把模型输出写入 `ai_enrichment`，监控台读取真实记录展示摘要、标签和情绪分布。
3. **飞书推送** — 飞书模板可以直接引用 `{{summary}}`、`{{tags}}`、`{{sentiment}}`，把 AI 处理后的内容推到群机器人。
4. **可视化验收** — 「监控台」的舆情监控卡片读取 `/api/v1/dashboard/opinion-monitor`，展示最近热点、AI 处理量、Feishu sent/failed 证据和来源贡献。

一键生成实战配置：

```bash
curl -X POST http://localhost:8000/api/v1/presets/opinion-monitor/apply \
  -H "Content-Type: application/json" \
  -d '{
    "source_prefix": "实战舆情",
    "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  }'
```

这会创建两条默认 `aibase news` 多账号采集源、对应定时计划，以及一个飞书规则。
如果暂时不填 `feishu_webhook_url`，飞书规则会以 disabled 状态创建，不会伪造推送成功。

关键验收命令：

```powershell
scripts\acceptance\fleet-acceptance.ps1 `
  -Site aibase `
  -Command news `
  -Limit 1 `
  -CenterPort 8032 `
  -AgentPort 19824 `
  -FreshDb
```

如果本机已有旧 API/agent 进程占用端口，可以换一组固定端口，例如
`-CenterPort 8035 -AgentPort 19828`。

最终应输出：

```text
ACCEPTANCE: PASS
```

---

## 快速开始

### 方式零：前后端本地开发

新前端在仓库内 `frontend/` 下开发和构建：

```bash
cd frontend
pnpm install
pnpm dev      # Next.js dev server, proxies /api/v1/* to backend
pnpm lint
pnpm exec tsc --noEmit
```

也可以从仓库根目录调用：

```bash
npm run dev:frontend
npm run lint:frontend
npm run typecheck:frontend
```

### 方式一：原生 Shell

直接复用本地 opencli 和 Chrome，适合开发和个人使用。

**前置要求**：Python 3.11+、Node.js 18+、[opencli Browser Bridge 扩展](https://github.com/jackwener/opencli/blob/main/README.zh-CN.md#playwright-mcp-bridge-%E6%89%A9%E5%B1%95%E9%85%8D%E7%BD%AE)

```bash
cp .env.example .env
./start.sh
```

| 服务 | 地址 |
|------|------|
| API 文档 | http://localhost:8031/docs |

`start.sh` 启动后端与 Chrome；前端另开终端运行 `npm run dev:frontend`。

```bash
./start.sh --no-chrome      # 跳过 Chrome（RSS/API 渠道不需要）
./start.sh --cdp-port 9223  # 自定义 Chrome CDP 端口
```

**停止**：`Ctrl+C`，自动关闭所有进程。

---

### 方式二：Docker

**前置要求**：Docker & Docker Compose

Linux / macOS：

```bash
curl -fsSL https://raw.githubusercontent.com/2233admin/opencli-admin/v0.4.0/scripts/install.sh | sh
```

Windows PowerShell：

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/2233admin/opencli-admin/v0.4.0/scripts/install.ps1 -OutFile install.ps1
.\install.ps1
```

安装器会生成安全密钥、拉取公开 GHCR 镜像并等待健康检查通过。终端会打印首次登录用的 `BOOTSTRAP_ADMIN_TOKEN` 和边缘节点用的 `API_AUTH_TOKEN`。

从源码构建：

```bash
cp .env.docker.example .env
# 填写 API_AUTH_TOKEN、BOOTSTRAP_ADMIN_TOKEN、SECRET_KEY、CREDENTIAL_ENCRYPTION_KEY
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

| 服务 | 地址 |
|------|------|
| 管理界面 | http://localhost:3010 |
| API 文档 | http://localhost:8031/docs |
| 内置浏览器 / 扫码登录 | http://localhost:6080 |

**停止**：`docker compose down`

公开镜像只包含 OpenCLI 核心能力，不会隐式下载组织私有适配包；需要额外适配包时必须显式提供经过审计的仓库。

首次打开管理界面时，在“管理员身份令牌”中输入 `BOOTSTRAP_ADMIN_TOKEN`；“Fleet API 令牌”中输入 `API_AUTH_TOKEN`，用于调用受保护的采集与节点接口。

---

### 登录采集账号

opencli 渠道依赖浏览器登录态，首次使用需手动登录各平台账号，之后持久保存。

| 启动方式 | 操作 |
|----------|------|
| 原生 Shell | 脚本启动后在 Chrome 窗口中登录。登录态保存在 `~/.opencli-admin/chrome-profile/` |
| Docker 单实例 | 打开 http://localhost:6080，在内置 Chromium 中扫码或登录 |
| 远程服务器 | 先执行 `ssh -L 6080:127.0.0.1:6080 user@server`，再访问本机 `http://localhost:6080` |

> 需要登录：小红书、Bilibili、知乎、微博、Twitter/X、LinkedIn、YouTube。Hacker News、BBC、RSS 等公开内容无需登录。

## 边缘节点

将任意远端机器注册为采集节点，形成分布式集群。

### 注册模式

| 模式 | 原理 | 适用场景 |
|------|------|----------|
| **WS 反向通道** | Agent 主动连接中心，中心通过长连接推送任务 | NAT / 跨网，Agent 无需开放入站端口 |
| **HTTP 直连** | Agent 启动后注册到中心，中心直接请求 Agent | 局域网，中心可直达 Agent |

### 安装

进入管理界面 → **节点管理** → **新增节点**，选择安装方式，复制命令在目标机器执行：

**Docker 安装**

```bash
# WS 模式（NAT / 跨网）
docker run -d --name opencli-agent --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -e CENTRAL_API_URL=http://<center-ip>:8031 \
  -e AGENT_REGISTER=ws -e AGENT_MODE=bridge \
  -e AGENT_API_TOKEN=<API_AUTH_TOKEN> \
  -p 19823:19823 \
  ghcr.io/2233admin/opencli-admin-agent:0.4.0

# HTTP 模式（局域网）
docker run -d --name opencli-agent --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -e CENTRAL_API_URL=http://<center-ip>:8031 \
  -e AGENT_REGISTER=http -e AGENT_MODE=bridge \
  -e AGENT_API_TOKEN=<API_AUTH_TOKEN> \
  -p 19823:19823 \
  ghcr.io/2233admin/opencli-admin-agent:0.4.0
```

**一键脚本安装**

```bash
# Docker 安装（无 Chrome 镜像）
curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center>:8031/api/v1/nodes/install/agent.sh | bash

# Docker 安装，含 Chrome（无需宿主机 Chrome）
curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center>:8031/api/v1/nodes/install/agent.sh | bash -s -- docker --install-chrome

# Shell 安装（无 Docker 环境）
curl -fsSL -H "Authorization: Bearer $API_AUTH_TOKEN" \
  http://<center>:8031/api/v1/nodes/install/agent.sh | \
  AGENT_REGISTER=ws AGENT_MODE=bridge bash -s -- python
```

### Bridge 与 CDP 两种连接模式

| 模式 | 原理 | 适用场景 |
|------|------|----------|
| **Bridge** | opencli + daemon.js + Browser Bridge 扩展 | 需要登录账号的平台（B站、小红书、微博等） |
| **CDP** | opencli + Chrome DevTools Protocol 直连 | 无需登录的公开页面，链路更简单 |

本地模式下，每个 Chrome 端点卡片上可切换 Bridge/CDP；Agent 模式下，通过「新增节点」向导（InstallWizardModal）选择连接模式。

### 常用环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CENTRAL_API_URL` | 中心 API 地址（必填） | — |
| `AGENT_REGISTER` | 注册模式：`ws` \| `http` | `ws` |
| `AGENT_MODE` | Chrome 连接模式：`bridge` \| `cdp` | `bridge` |
| `AGENT_PORT` | Agent 监听端口 | `19823` |
| `AGENT_ADVERTISE_URL` | 中心回连 Agent 的可达 URL；LAN/NetBird/WireGuard/SSH/custom 都只需要保证它可达 | 自动 |
| `FLEET_NETWORK_PROVIDER` | 采集层 reachability provider：`lan` \| `netbird` \| `wireguard` \| `ssh` \| `custom` | `lan` |
| `AGENT_LABEL` | 可读标签 | 主机名 |

### 采集模式

| 模式 | 说明 |
|------|------|
| **本地模式** | 中心直连本地 Chrome（shell 部署），不经过 Agent |
| **Agent 模式** | 通过 Agent 节点采集，支持本地 Docker 容器或远端多机分布式部署 |

```bash
COLLECTION_MODE=local   # 默认
COLLECTION_MODE=agent
```

### Agent 路由优先级

| 优先级 | 入口 | 作用 |
|--------|------|------|
| 1（最高） | 触发时手动指定 | 仅本次覆盖 |
| 2 | 定时计划绑定 | 该计划每次固定使用 |
| 3 | 节点管理 → 站点绑定 | 按站点自动路由 |
| 4（兜底） | — | 自动分配空闲实例 |

## 配置

编辑 `.env`：

```bash
SECRET_KEY=your-random-secret-key          # 生产环境必须修改
PUBLIC_URL=http://192.168.1.1:8031         # 远端 Agent 注册使用，留空自动推断
COLLECTION_MODE=local                      # local | agent
TASK_EXECUTOR=local                        # local | celery（分布式）
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

完整配置项参见 [.env.example](.env.example)。

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          用户浏览器                                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP
┌──────────────────────────────▼──────────────────────────────────────┐
│                        Frontend（React）                             │
│         Dashboard · 数据源 · 任务 · 记录 · 计划 · 节点 · 通知        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST API
┌──────────────────────────────▼──────────────────────────────────────┐
│                       Backend API（FastAPI）                          │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ Scheduler│  │ Pipeline │  │  AI 处理  │  │  通知分发          │  │
│  │ (定时/   │  │ collect  │  │ Claude/  │  │  Webhook/飞书/     │  │
│  │  webhook)│  │ →store   │  │ OpenAI/  │  │  钉钉/企微/Email   │  │
│  └──────────┘  └────┬─────┘  │ Ollama…  │  └────────────────────┘  │
│                     │        └──────────┘                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     渠道插件（Channels）                       │   │
│  │  opencli · RSS · REST API · Web 爬虫 · 通用 CLI              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────┬────────────────────┬───────────────────────────────────┬─────┘
       │                    │                                   │
       │ CDP/Bridge         │ WS 反向通道 / HTTP 直连            │ SQLite /
       ▼                    ▼                                   ▼ PostgreSQL
┌─────────────┐   ┌──────────────────────┐             ┌───────────────┐
│  内置 Agent  │   │     远端边缘节点       │             │    数据库      │
│  (Docker)   │   │  (Docker / Shell)    │             │               │
│             │   │                      │             └───────────────┘
│ ┌─────────┐ │   │ ┌──────────────────┐ │
│ │ Chrome  │ │   │ │ agent_server.py  │ │
│ │Bridge / │ │   │ │  WS ←─── 中心    │ │
│ │  CDP    │ │   │ │  HTTP ──→ 中心   │ │
│ └────┬────┘ │   │ └───────┬──────────┘ │
└──────┼──────┘   └─────────┼────────────┘
       │                    │
       ▼                    ▼
  opencli CLI          opencli CLI
  目标网站              目标网站
```

## 技术栈

| 层次 | 技术 |
|------|------|
| 前端 | Next.js + React 19 + TypeScript + Tailwind CSS |
| 后端 | FastAPI + SQLAlchemy 2.0 (async) |
| 数据库 | SQLite（默认）/ PostgreSQL |
| 任务队列 | asyncio（单机）/ Celery + Redis（分布式） |
| Agent | Chromium + noVNC + opencli（Bridge/CDP，WS/HTTP 双注册） |
| 部署 | 原生 Shell / Docker Compose |

## 采集流水线

```
触发（手动 / 定时 cron / Webhook）
  ↓
Agent 路由（手动指定 > 计划绑定 > 站点绑定 > 自动分配）
  ↓
渠道采集
  ├─ opencli  → Bridge / CDP → 目标平台
  ├─ RSS      → feedparser
  ├─ API      → REST / GraphQL 直连
  ├─ Web 爬虫 → httpx + BeautifulSoup
  └─ CLI      → 通用命令行包装
  ↓
归一化（title / url / content / author / published_at / extra_*）
  ↓
去重存储（SHA-256 内容哈希）
  ↓
AI 处理（可选）— Claude · OpenAI · DeepSeek · Kimi · GLM · Ollama
  ↓
通知推送（可选）— Webhook · 飞书 · 钉钉 · 企微 · Email
```

## 项目结构

```
├── backend/
│   ├── api/v1/          # FastAPI 路由
│   ├── agent_server.py  # 边缘 Agent 服务（WS/HTTP 双注册）
│   ├── channels/        # 渠道插件（opencli / rss / api / web_scraper / cli）
│   ├── pipeline/        # 采集流水线（collect → normalize → store → ai → notify）
│   ├── scheduler.py     # 本地异步调度器
│   └── worker/          # Celery 任务定义
├── frontend/             # Next.js Canvas/HDA 前端
├── chrome/extension-src/ # Browser Bridge / extension
├── iii/                  # III workers and schedules
├── odp-rs/               # ODP Rust data plane
├── scripts/
│   └── install-agent.sh # 一键安装 Agent
├── docker-compose.yml
├── start.sh             # 原生 Shell 启动脚本
└── .env.example
```

<img width="3360" height="1958" alt="565679904-db302792-5593-4199-bcad-0982e860ec41" src="https://github.com/user-attachments/assets/ea9c3b6f-e3f6-4792-a388-9e470bf3de7e" />
<img width="3360" height="1864" alt="565680032-ce644c00-c8dd-492b-97f1-172516e3d78d" src="https://github.com/user-attachments/assets/c9694c60-90b2-4241-9d8b-5753a7066f8c" />
<img width="3360" height="1856" alt="566009767-1236b60d-b682-4327-bbfd-4e6da1f857cf" src="https://github.com/user-attachments/assets/05712eda-5f7d-4cb8-b772-2b481b2f9f51" />
<img width="3360" height="2128" alt="566026018-c2860e1c-23f0-4ce5-94de-599f4b9de062" src="https://github.com/user-attachments/assets/3b965d29-8783-49fc-a58f-30ef88213899" />
<img width="3358" height="884" alt="566120897-994046c5-88ae-436f-8108-3327108cb2cc" src="https://github.com/user-attachments/assets/e8975aa7-206f-4880-9072-e93d913803d5" />

## 集成测试

详见 [TESTING.md](TESTING.md)。

## 数据模型

`collected_records` 表结构、`normalized_data` JSON 字段语义、status 状态机、AI enrichment 形状以及下游查询的正确 SQL 姿势详见 [docs/schema.md](docs/schema.md)。下游消费者（自定义查询、外部 AI worker、数据导出）务必先看这份。

## 发布镜像

推送 `v*` 标签后，GitHub Actions 自动构建 amd64 / arm64 镜像并创建 GitHub Release：

- `ghcr.io/2233admin/opencli-admin-api:0.4.0`
- `ghcr.io/2233admin/opencli-admin-frontend:0.4.0`
- `ghcr.io/2233admin/opencli-admin-chrome:0.4.0`
- `ghcr.io/2233admin/opencli-admin-agent:0.4.0`
- `ghcr.io/2233admin/opencli-admin-agent:0.4.0-chrome`

## License

[Apache License 2.0](LICENSE)

---

## 接盘侠指南

新手？从这里开始：

```bash
# 1. 克隆项目
git clone https://github.com/2233admin/opencli-admin.git
cd opencli-admin

# 2. 快速了解项目
cat README_HANDOVER.md      # 接盘完整指南
cat PONYTAIL.md             # 开发规范摘要

# 3. 启动开发环境
docker compose --profile nas up -d
```

详细文档见：
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - 架构设计
- [ptt-acceptance.md](docs/ptt-acceptance.md) - 集群联机 PTT 验收门
- [SURVEY_superset.md](docs/SURVEY_superset.md) - 技术选型
- [PROJECT_MANAGEMENT.md](docs/PROJECT_MANAGEMENT.md) - 任务看板
- [DEVELOPMENT_STANDARD.md](docs/DEVELOPMENT_STANDARD.md) - 开发规范
