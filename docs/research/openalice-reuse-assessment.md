# OpenAlice 产品流程与前端复用评估

> 范围纠正：此前把“保持旧壳层、只取小型 hook、Linear/SmoothUI 作为整个体验的固定边界”当成了实施前提。这与用户后来明确要求广泛内化 OpenAlice 操作逻辑不符。框架适配仍需保留权限与数据合同，但不能据此排除工作标签、项目工作区、会话/结果并览和直接追问。后续应按用户可完成的操作链拆分和验收。

本文保留最初调研时的状态；后续已按 #116–#119 实现共享创建、受治理的 Agent 草稿操作、项目级持久会话工作区、结果内联追问及来源导航。Studio 会话桥接的权限决策记录在 [ADR 0045](../adr/0045-bridge-studio-agent-session-context.md)。当前交付和验证见[整合验收记录](../verification/openalice-product-journey.md)及[使用说明](../usage/agent-project-workflow.md)。下文未实施的描述属于调研基线，不是当前功能清单。

核查对象是 TraderAlice/OpenAlice 的源码快照 `52b51f29809178594b7b57bf666133829368b7b4`（`git rev-parse HEAD`）。以下结论来自源码，不把 README、注释中的未来阶段或产品设想当成已交付能力。

## 关键发现

1. **全局壳层只负责一级区域，业务导航下沉到页面。** `AppShellContent` 持久挂载 `ActivityBar`、`TabHost` 和全局 toast；ActivityBar 只改变 `selectedSidebar` 并 `openOrFocus` 默认视图，页面自己的 navigator 由页面/Tab shell 持有。[App.tsx#L112-L161](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/App.tsx#L112-L161) [ActivityBar.tsx#L78-L90](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/ActivityBar.tsx#L78-L90) [registry.tsx#L57-L64](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/tabs/registry.tsx#L57-L64) 可借鉴全局壳层与页面内部导航的职责划分；我们已有稳定壳层，复用重点是上下文一致性。

2. **Tab 是可恢复的导航身份，Shell 可复用但资源默认释放。** `ViewSpec` 把 workspace/session/file-viewer 的参数编码进类型，Tab store 用 `openOrFocus` 去重并持久化到 `localStorage`；`TabHost` 默认只挂载活动视图，只有明确声明 `keep-mounted` 的视图才保留后台 DOM。[types.ts#L27-L77](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/tabs/types.ts#L27-L77) [store.ts#L17-L35](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/tabs/store.ts#L17-L35) [TabHost.tsx#L8-L18](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/TabHost.tsx#L8-L18) [TabHost.tsx#L44-L90](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/TabHost.tsx#L44-L90) `WorkspacePage` 将 sessionId 绑定到 Tab，关闭 Tab 不结束服务端 PTY；`WorkspaceView` 只为当前 Tab 挂一个 Terminal，避免同一 PTY 被多 WebSocket 抢占。[WorkspacePage.tsx#L1-L16](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/WorkspacePage.tsx#L1-L16) [WorkspaceView.tsx#L54-L73](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/WorkspaceView.tsx#L54-L73) 可移植的是“路由参数 + 资源生命周期 + 去重”契约；Next App Router 与 React Router 的导航/adoption 不能整段替换。

3. **Inbox、Session、文件在一个可回跳的上下文链中。** Inbox 采用 `PageSidebarLayout` 双栏：侧栏支持时间/Workspace 聚类、搜索、j/k 和逐条已读；详情按单条 push 展示，并根据 origin 的 `sessionId`/`resumeId` 继续原会话或打开对应 Workspace。[InboxPageShell.tsx#L10-L21](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/InboxPageShell.tsx#L10-L21) [InboxSidebar.tsx#L28-L40](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/InboxSidebar.tsx#L28-L40) [InboxPage.tsx#L271-L356](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/InboxPage.tsx#L271-L356) Workspace 内无 session 时是可搜索、按运行状态过滤的 Session library；有 session 才挂终端；右侧 Files 面板轮询目录，文件点击会创建带 `source`、`returnSessionId` 的 file-viewer Tab。[WorkspaceView.tsx#L75-L107](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/WorkspaceView.tsx#L75-L107) [WorkspaceView.tsx#L174-L215](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/WorkspaceView.tsx#L174-L215) [FilesPanel.tsx#L19-L73](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/FilesPanel.tsx#L19-L73) 这比把 Inbox、Agent Dock、文件预览做成互不相识的入口更连贯；应移植“来源/返回上下文”字段和交互，而不是整个 Terminal/Workspace 面板。

4. **创建路径被压成一个可复用的表单逻辑。** `CreateWorkspaceForm` 明确由侧栏、Chat、模板详情共同使用，模板、tag、source version、loading/error、成功回调都在一处；`useCreateWorkspace` 还负责 tag 规则、冲突后缀和提交状态。[CreateWorkspaceForm.tsx#L1-L15](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/CreateWorkspaceForm.tsx#L1-L15) [CreateWorkspaceForm.tsx#L51-L82](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/CreateWorkspaceForm.tsx#L51-L82) [useCreateWorkspace.ts#L7-L23](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/hooks/useCreateWorkspace.ts#L7-L23) [useCreateWorkspace.ts#L43-L87](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/hooks/useCreateWorkspace.ts#L43-L87) Quick Chat 则选择目标 Workspace、运行时/凭据后调用 `quickChat`，成功聚焦新 session；这是真实的“首句输入→开始工作”路径，但语义是创建 Workspace/Session，不是 DAG workflow builder。[ChatLandingPage.tsx#L80-L86](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/ChatLandingPage.tsx#L80-L86) [ChatLandingPage.tsx#L289-L337](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/ChatLandingPage.tsx#L289-L337) [ChatLandingPage.tsx#L372-L417](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/pages/ChatLandingPage.tsx#L372-L417)

5. **页面侧栏是可抽取的布局原语，但有较重的尺寸恢复逻辑。** `PageSidebarLayout` 统一桌面可调宽度、localStorage 恢复、窄屏 Sheet/drawer、主导航注入和最小宽度约束；它是 Inbox/Chat/Workspace 等邻近视图共享导航壳的直接来源。[PageSidebarLayout.tsx#L28-L81](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/PageSidebarLayout.tsx#L28-L81) [PageSidebarLayout.tsx#L101-L181](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/PageSidebarLayout.tsx#L101-L181) 移植时应先取接口形状和移动端焦点/关闭语义；`react-resizable-panels` 的 imperative repair 与 Next layout 生命周期需单独适配。

6. **动效是局部、低层的可复用 primitive，不存在已核实的通用路由动画或 DAG 编辑器。** OpenAlice 用 `--motion-fast/standard/slow` 和 reduced-motion CSS token 做控件、dialog、popover、graph 节点入场；`useReorderMotion` 是一个独立 FLIP hook，测量子项位置后用 WAAPI 过渡排序，且会取消旧 Animation。[index.css#L100-L111](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/index.css#L100-L111) [index.css#L448-L506](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/index.css#L448-L506) [useReorderMotion.ts#L18-L72](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/workspace/useReorderMotion.ts#L18-L72) 源码中可检索到的是 `TrackedGraphView` 的追踪图与 Office route pointer/trail；未验证到通用 DAG workflow editor，也未验证到跨页面 route transition。当前项目既有的全局 SSGOI 页面动效、SmoothUI 局部反馈和 Inbox Linear 结构应继续作为唯一体验基线，只考虑吸收 token/FLIP/按状态取消动画。

## 对当前仓库的落点（仅按文件名做对应，不据此断言实现等价）

- 壳层：`frontend/app/(app)/layout.tsx`、`frontend/components/shell/app-sidebar.tsx`、`frontend/components/motion/app-route-transition.tsx`。
- Inbox/来源回跳：`frontend/app/(app)/inbox/page.tsx`、`frontend/components/inbox/queue-detail.tsx`、`frontend/lib/inbox/workbench-state.ts`、`frontend/lib/api/agent-conversations.ts`。
- Agent/Session 连续性：`frontend/components/shell/global-agent-dock.tsx`、`frontend/lib/agent-dock-state.ts`，以及现有 `frontend/e2e/agent-conversation-session.spec.mjs`。
- 工作流创建与草稿：`frontend/app/(app)/studio/page.tsx`、`frontend/lib/workflow/draft-persistence.ts`、`frontend/lib/workflow/studio-templates.ts`。
- 可复用动效：`frontend/components/motion/route-transition.ts`、`frontend/components/flow/workflow-motion-runtime.tsx`、`frontend/components/smoothui/`。

## 集成约束与建议

OpenAlice UI 是 Vite + React Router 7 + Zustand 5 + Tailwind 4，依赖中为 React 19.1、Base UI 1.6；当前前端是 Next 16.3/React 19.2、Base UI 1.7、Zustand 5、Tailwind 4。可优先评估数据结构、状态机、焦点语义和小型 hook 的移植，避免引入第二套路由或整体壳层。最小收益顺序是：给当前 Inbox/Agent Dock 补齐 Workspace/Session/source/returnContext 的连续性；把 Studio 新建入口收敛到一个共享的 draft bootstrap/composer；再评估 FLIP 排序和文件预览返回条。源码未证明 OpenAlice 能直接解决“自然语言生成并编辑 DAG”的缺口，因此不应据此扩大成重写或全局面板迁移。

## 与 OpenCLI 当前实现的对照（主代理核查）

OpenAlice 的业务 Workspace 更接近我们围绕持续数据目标建立的 **Project**，不能直接替换 OpenCLI 作为权限、连接与资源边界的 Workspace。参考 [CONTEXT 的定义](../../CONTEXT.md)。以下均为源码检查，不是运行 OpenAlice 后得到的体验或性能结论。

| 我们的真实入口 | 已有实现 | 与用户场景之间的缺口 |
|---|---|---|
| 项目创建 | [Studio submitCreate](../../frontend/app/(app)/studio/page.tsx) 120–132 行调用事务式 bootstrap，保存 Project、Primary Workflow 和 Draft 后进入画布；[后端 bootstrap](../../backend/api/v1/studio_projects.py) 78–147 行提供现成落点 | 空白、模板、导入三个入口已存在；没有把“描述持续采集目标”接入同一创建路径。 |
| 顶部 Agent | [GlobalAgentDock](../../frontend/components/shell/global-agent-dock.tsx) 已调用持久会话 API，支持恢复消息与提案 | 84 行仅从 URL 取得 workspace；233–235 行在缺失时阻止发送。无 workspace 参数的全局页面会进入这个分支。这是静态路径核实，未在本轮浏览器重现。 |
| Agent 能做什么 | [chat.py TOOLS](../../backend/api/v1/chat.py) 109–204 行暴露 4 项查询与 4 项变更工具 | 该 Dock 的工具列表不包含创建项目、修改工作流草稿。不能仅添加一个“让 Agent 创建”按钮就宣称完成能力。 |
| 任务与通知 | [Inbox](../../frontend/app/(app)/inbox/page.tsx) 和 [QueueDetail](../../frontend/components/inbox/queue-detail.tsx) 已有 Linear 列表/详情与 SmoothUI 审批 | 任务跳工作项、通知跳规则、审批跳通用智能体页；这些分支还不是“报告 → 对应 Run → 原会话 → 继续修改”的完整入口。 |
| 项目内跳转 | [ProjectNavigation](../../frontend/components/studio/project-navigation.tsx) 和 [run-navigation](../../frontend/lib/studio/run-navigation.ts) 已保持同一范围的 workspace/project/workflow/run/trace | 在这套导航上补全上下文与来源链接，比再加一套独立窗口状态更适合当前实现。 |

这里不能说我们“没有持久化”或“没有项目上下文”。已有基础存在，待补的是创建能力、入口绑定与后续操作的连接。

## 可借用的后端规则，而非另建存储系统

1. **工作定义只有一个权威来源。** OpenAlice 的 `issueFirePrompt` 直接返回人看到的 `issue.what`；`isFireable` 由调度配置及非终态推导可触发性。适合借鉴为：人和 Agent 编辑同一个工作定义，手动运行和定时运行引用同一授权版本。我们仍需保留 Work Item、Automation、Workflow Version、Run 的现有职责，不能把它们全部折叠成一个 Markdown Issue。[工作定义与调度判定](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/workspaces/issues/declaration.ts#L298-L338)
2. **会话身份与单次运行身份分开。** OpenAlice 的 ProductSessionCoordinator 协调稳定 resumeId 与会话记录；PublicSession 再投影运行状态和不含密钥的模型配置。可借鉴“持续负责的 Agent 会话”和“这一次执行”分开显示，不能照抄原生 CLI/PTY 身份接线。[会话协调](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/workspaces/product-session-coordinator.ts#L47-L87)、[统一投影](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/workspaces/public-session.ts#L83)
3. **通知携带产出引用与来源。** InboxOrigin 关联 runId、issueId、resumeId；推送工具将服务端取得的 origin 和文件内容指纹写入 provenance。我们的通知应引用已有 Records/Evidence/Artifact 与 Run，不再复制一份脱离来源的结果。[通知契约](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/core/inbox-store.ts#L70-L103)、[推送来源记录](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/tool/inbox-push.ts#L79-L122)
4. **追问回到产出者。** Inbox inquiry 路由根据 origin.resumeId 指向原会话，否则按产出对象解析接续方式；不可继续时返回明确的 unavailable。我们可以把这个操作接入保留的 Linear 详情区。来源必须通过现有授权对象关系解析，来源字段本身不能当授权证明。[追问入口](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/webui/routes/inquiries.ts#L97-L120)
5. **手动执行与自动执行共享调度逻辑。** runIssueNow 重读 Issue，沿同一 dispatchIssue 路径执行；自动触发在 dispatch 成功之后记标记。可借鉴行为一致性，不能据此声称崩溃窗口下的端到端 exactly-once，也不据此替换我们的数据库调度与幂等控制。[手动路径](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/workspaces/schedule/scanner.ts#L168-L207)、[自动路径](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/src/workspaces/schedule/scanner.ts#L329-L368)

## 建议首先验收的用户场景

“每天采集指定来源，去重整理，保存为表格，并在任务与通知里给我结果。”

理想操作顺序如下，属于建议的验收目标，**本轮没有实现或宣称已跑通**：

```mermaid
flowchart LR
  A["描述目标"] --> B["保存项目草稿"]
  B --> C["补齐来源、连接与处理规则"]
  C --> D["验证并试运行"]
  D --> E["查看表格、报告与运行证据"]
  E --> F["确认启用定时执行"]
  F --> G["任务与通知"]
  G --> H["追问原会话或调整同一项目"]
  H --> C
```

- 关闭再打开，仍能找到同一项目与会话。
- 从通知打开结果、Run 和会话，项目归属保持一致；不存在的来源显示明确不可用状态。
- 不会把“草稿已保存”“验证通过”“单次成功”“已启用调度”显示成同一种成功。
- 用户看到的工作定义与执行版本有清楚关系；发布、启用和有外部效果的操作仍走现有提案与权限流程。
- 继续使用 Linear 的任务列表与详情结构、SmoothUI 的局部异步反馈，以及现有统一路由动效。

## 核查边界与协作记录

本轮浅克隆并检查固定提交的源码，没有安装或执行 OpenAlice 的依赖和脚本，没有启动其服务，没有修改应用代码、数据库或正在运行的前端。前端组件是否可直接迁移仍需对所选组件做 Next 客户端边界、样式令牌、键盘操作和 reduced-motion 的适配验证；没有给出跨项目的编译速度推断。

路由：Luna High Explore 负责 OpenAlice 前端源码与组件复用评估；主代理负责持久化/调度/来源链路和当前 OpenCLI 实现对照，并核对最终说明的源码链接。交付为研究说明，不是开发完成报告；无模型升级；主代理修正了初稿中的 7 个截短提交链接和 1 个不存在的本地路径。
