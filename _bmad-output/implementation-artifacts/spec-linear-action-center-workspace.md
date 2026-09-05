---
title: "线性 Action Center 稳定工作区"
type: refactor
created: "2026-08-29"
status: done
baseline_commit: 4f5bde63dd030c20342818812609ce63486161cb
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

### Problem
当前 `/inbox` 是独立的固定双栏工作台，`/tasks`、`/notifications` 与 `/control/actions` 各自复制页面标题、标签和容器，并受 pathname-keyed 页面转场影响。用户在待处理、历史、通知规则和控制证据之间切换时，视觉层级与空间上下文不连续；既有深链、数据操作、权限和审计边界必须保留。

### Approach
把 `/inbox?tab=pending|tasks|notifications|controls` 定义为唯一 canonical Action Center workspace。四个同级 pane 共享持久标题、标签、尺寸和滚动根：待处理队列、任务历史、通知规则、控制记录。`/tasks`、`/notifications`、`/control/actions` 保持有效兼容重定向；其中控制旧路径的 ledger query filters 必须原样转译到 `tab=controls`，`/tasks/[id]` 继续作为从 workspace 进入的 subordinate detail。仅在这些 sibling tab 状态间抑制页面级转场语义，不改变 API、认证、评估或 mutation 行为。

## Boundaries & Constraints

### Always
- 复用 `AppShell`、`RouteTabs`、既有设计令牌与 `/inbox` 的固定队列/详情结构。
- 新增第四个只读 `控制记录` pane，使用抽取后的 ControlActions evidence ledger 与 `useControlActions`，不改证据字段或展示权限。
- inbox 控制项指向 `/inbox?tab=controls`，并保留既有控制 ledger 查询过滤器。
- sidebar 的 Control href 指向 `/control/kill-switch`；`/control/kill-switch`、`/control/advisory-report`、`/control/odp-state` 留在 Control & Safety。
- 用 `router.replace(..., { scroll: false })` 或等价方式维持 tab、过滤器、深链与滚动上下文。

### Ask First
- 修改 Action Center 以外的导航分组、Control & Safety 信息架构或 `/tasks/[id]` 详情契约。
- 改变控制权限、认证、审计不可抵赖字段、审批决策、评估或 mutation 语义。
- 删除兼容重定向、移除支持的 control ledger query filters，或引入第二个 canonical workspace URL。

### Never
- 不替换 live API，不伪造、合并或重新解释后端信号。
- 不把只读控制证据变成可编辑工作项，不扁平化安全控制页面。
- 不以 pathname 重挂载、整页动画或自动滚顶制造 sibling tab 的视觉跳跃；不重建无关导航。

## I/O & Edge-Case Matrix

| 场景 | 输入/状态 | 期望输出 |
|---|---|---|
| 同级切换 | `/inbox?tab=pending|tasks|notifications|controls` | URL 保持 canonical；共享标题、标签、滚动根稳定；无页面级转场 |
| 兼容路径 | `/tasks`、`/notifications`、`/control/actions` | 重定向到对应 inbox tab；保留 query；控制 filters 原样保留 |
| 任务详情 | `/tasks/[id]` | 继续显示 subordinate detail；返回 workspace 对应 tasks tab |
| 控制证据 | `useControlActions` 返回记录或失败 | 只读 ledger 原样显示；沿用既有授权、错误和空态 |
| inbox 信号 | 筛选、搜索、j/k、Enter | queue/detail 原位更新；目标链接与 tab 状态不丢失 |
| 局部失败/窄屏 | 单源失败或小于双栏阈值 | 局部状态可重试；队列后详情可读；无横向页面跳动 |

</frozen-after-approval>

## Code Map

- `frontend/app/(app)/inbox/page.tsx::InboxContent`、`frontend/components/inbox/queue-detail.tsx::QueueDetail`：workspace、队列/详情与控制项入口。
- `frontend/components/shell/route-tabs.tsx::RouteTabs`、`ACTION_CENTER_TABS`：扩展四个 canonical tab 的导航契约。
- `frontend/components/motion/app-route-transition.tsx::AppRouteTransition`：仅对 `/inbox` tab 状态及兼容重定向抑制 pathname 页面转场语义。
- `frontend/components/shell/app-shell.tsx::AppShell`：持久 Sidebar/Header 与稳定 routed boundary。
- `frontend/app/(app)/tasks/page.tsx::TasksPage`、`frontend/app/(app)/notifications/page.tsx::NotificationsPage`：迁入 shared workspace 的既有 pane 内容。
- `frontend/app/(app)/tasks/[id]/page.tsx`：保留 subordinate detail。
- `frontend/app/(app)/control/actions/page.tsx`、`frontend/app/(app)/control/layout.tsx`、`frontend/lib/api/hooks.ts::useControlActions`：只读 evidence ledger、兼容重定向及安全边界。
- `frontend/app/(app)/control/kill-switch/page.tsx`、`advisory-report/page.tsx`、`odp-state/page.tsx`：留在 Control & Safety；sidebar 导航入口改为 kill-switch。

## Tasks & Acceptance

### Execution checklist
- [x] 建立 `/inbox` canonical tab 状态与共享 shell，四 tab 均可深链。
- [x] 迁入 tasks、notifications pane，兼容路径重定向并保留 `/tasks/[id]`。
- [x] 提取只读 ControlActions evidence ledger，接入 `useControlActions` 与 controls pane；保留 filters。
- [x] 将 inbox 控制项改指向 canonical controls tab，移除 `CONTROL_TABS` 中的 action history。
- [x] 将 sidebar Control href 指向 `/control/kill-switch`，保留三项 Control & Safety 路由。
- [x] 仅关闭 sibling tab 的页面转场语义，并补齐定向回归与 UI smoke。

### Given/When/Then AC
- **Given** 用户在任一四 tab，**When** 点击另一 tab，**Then** URL 变为 `/inbox?tab=...`，共享壳与滚动上下文不跳变。
- **Given** 用户打开旧 `/control/actions?outcome=pending`，**When** 重定向完成，**Then** 到达 `/inbox?tab=controls&outcome=pending` 且 ledger 仍只读、权限不变。
- **Given** 用户从 inbox 控制项进入，**When** 点击入口，**Then** 进入 canonical controls pane 而非旧独立页面。
- **Given** 用户打开 `/tasks/[id]`，**When** 使用返回或 workspace 标签，**Then** detail 深链有效且回到 tasks tab。
- **Given** 非 Action Center 控制页面，**When** 访问 kill-switch、advisory-report 或 odp-state，**Then** 仍在 Control & Safety 上下文，sidebar Control 指向 kill-switch。

## Spec Change Log

- 2026-08-29：权威修订；明确 `/inbox?tab=` 为唯一 canonical workspace，加入 controls pane、兼容重定向及 Control & Safety 导航边界。

## Design Notes

共享 shell 只负责标题、四个同级 tab、稳定高度与滚动根；pane 保留各自数据查询、空态、错误态和操作。`pending` 继续承载 inbox master-detail，`tasks` 与 `notifications` 保留原表格/创建/删除行为，`controls` 复用只读 evidence ledger。兼容重定向是 URL 层适配，不复制页面实现；`/tasks/[id]` 不被改写成 tab。Control & Safety 的三项页面不纳入 Action Center，避免把安全语义伪装成普通历史记录。

## Verification

- 定向回归：`cd frontend && node --test scripts/check-navigation-transition-regressions.mjs scripts/check-inbox-regressions.mjs`，补充 canonical tab、兼容重定向、filters、CONTROL_TABS/sidebar 断言。
- UI smoke：启动 `pnpm build && pnpm start --hostname 127.0.0.1 --port 3000`，运行 `pnpm exec playwright test e2e/p0-regressions.spec.mjs`，覆盖四 tab、旧路径跳转、`inbox-workbench`、队列/详情滚动与无跳顶。
- 手工确认：控制 ledger 只读且权限边界不变；Control & Safety 三页仍独立；窄屏无页面级横跳。

## Suggested Review Order

**工作区入口与请求隔离**

- 先核对共享壳、tab 状态和 pending 隔离。
  [`page.tsx:341`](../../frontend/app/(app)/inbox/page.tsx#L341)

- 查询仅在 pending 激活，避免隐藏请求。
  [`hooks.ts:36`](../../frontend/lib/api/hooks.ts#L36)

**Canonical 导航与兼容入口**

- 链接先生成完整目标，兼顾新标签和右键。
  [`route-tabs.tsx:21`](../../frontend/components/shell/route-tabs.tsx#L21)

- 旧地址重定向时保留全部查询过滤器。
  [`page.tsx:7`](../../frontend/app/(app)/control/actions/page.tsx#L7)

**Pane 状态与证据边界**

- 任务筛选由 URL 驱动并保存滚动位置。
  [`tasks-pane.tsx:31`](../../frontend/components/action-center/tasks-pane.tsx#L31)

- 控制账本保持只读并精确转发过滤器。
  [`control-actions-ledger.tsx:22`](../../frontend/components/control/control-actions-ledger.tsx#L22)

**运行时回归证明**

- 浏览器覆盖深链、过滤、快捷键和滚动往返。
  [`p0-regressions.spec.mjs:112`](../../frontend/e2e/p0-regressions.spec.mjs#L112)

- 文本回归锁定 pending 边界与兼容契约。
  [`check-inbox-regressions.mjs:7`](../../frontend/scripts/check-inbox-regressions.mjs#L7)
