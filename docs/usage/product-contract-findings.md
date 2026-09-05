# 产品契约核对记录：为什么完整说明书还写不通

日期：2026-09-05。源码基线：`ae02a27ddd8fd1855d298e8ac023fcb66b21b857`。

本记录支撑[当前源码使用说明](product-manual.md)。它记录本次证据与尚未成立的产品承诺，不替代 GitHub Issues 中的后续实施任务，也不把一次审查当作整套产品已验收。

## 结论

已有的采集、工作流、Agent、数据和治理能力形成了多条可工作的局部路径。它们对“运行”“持久化”“调度”“结果”“交付”的定义和接入方式尚未完全统一。文档需要不断跨路径解释特殊规则，因此无法提供一份省略这些边界仍然真实的全流程教程。

同时，部分早期调查已过时，部分 ADR 是设计目标，部分 README 表述比本次能证明的实施范围更强。应逐条校准承诺及其验收证据。

## 证据级别

| 级别 | 含义 | 本次不据此推导的结论 |
| --- | --- | --- |
| 现场观察 | 本机浏览器、健康响应、运行容器或部署 OpenAPI 已读取 | 不等于每个写操作及外部服务可用 |
| 测试通过 | 本次实际运行的测试通过 | 不等于使用真实账号、真实来源和接收方完成全链路 |
| 源码核对 | 读到实现、模型、路由和调用关系 | 不等于部署实例与源码一致，或所有边界均被覆盖 |
| 设计约定 | CONTEXT / ADR 中的目标 | 不作为已交付功能证明 |

## 1. “持续运行这个工作流”没有统一的调度对象

**源码事实：** `Schedule` 以 `source_id` 关联数据源，scheduler 调用 `dispatch_scheduled_collection`。另一套 `Automation` 关联 Operations Agent，调度服务读取 Agent 的发布定义。没有在这些标准调度路径中找到持久绑定 Studio 工作流发布版本并周期触发的实现。

依据：[来源调度模型](../../backend/models/schedule.py#L13)、[来源调度器](../../backend/scheduler.py#L117)、[Automation 模型](../../backend/models/automation.py#L7)、[Agent 调度服务](../../backend/services/automation_schedule_service.py#L182)。设计目标见 [Automation 定义](../../CONTEXT.md#L354)。

**对说明书的影响：** “保存并发布，然后设置每小时执行”缺少可以填写的统一步骤。两个页面都出现“自动化”，仍不足以说明重复执行的是同一对象。

**需要落实的产品合同：** 重复执行的归属、目标版本、输入、启停、错过调度及失败恢复必须可说明。外部定时器可被评估，但不能隐去版本选择和恢复责任。

**验收证据：** 同一项目、同一确定版本至少执行两次；修改并发布新版后，能证明后续计划采用的版本规则及历史记录不变。

## 2. Agent 生成图与保存、发布之间有断点

**源码事实：** MCP 的使用指引明确将 drafting/compile 定义为不保存、不执行。工具集有能力发现、需求生成、patch 预览、编译、已发布工作流运行和运行查询；没有覆盖 bootstrap、草稿持久化、持久化验证、版本发布的全套工具。MCP 当前直接调用 REST，ADR-0042 里的统一命令接口不能当作现成接入合同。

依据：[MCP 指引和请求包装](../../backend/mcp_server.py#L85)、[MCP 项目工具](../../backend/mcp_server.py#L325)、[图预览路由](../../backend/api/v1/workflows.py#L277)、[统一命令接口设计](../adr/0042-expose-capabilities-through-an-api-first-agent-loop.md)。

**对说明书的影响：** “让外部 Agent 帮我长期采集”需要从 MCP 切到额外的 REST 调用或人工操作。生成成功不能写成创建持久化项目成功。

**需要落实的产品合同：** 人、内置助手和外部 Agent 的创建/修改入口应说明同一对象如何保存、如何处理修订冲突以及如何得到可运行版本。先复用现有持久化事实，再判断哪些适配入口需要补齐。

**验收证据：** Agent 创建或修改后，人在界面中打开同一个项目；重新进入仍可见；双方修改不会静默覆盖；发布与运行可追溯到实际修订和版本。

## 3. 发布版本的执行规则随入口变化

**源码及本次测试事实：** Studio 项目运行接口要求已有发布版本，并把实际版本写入运行；幂等请求、未发布拒绝和版本快照有本次通过的测试。通用 `/api/v1/workflows/runs` 接受传入的 `project` 图；Webhook ingress 接受 `workflowProject` 并编译执行。这两类入口没有同样的发布版本查找过程。

依据：[Studio 执行入口](../../backend/api/v1/studio_workflows.py#L416)、[通用与 Webhook 执行入口](../../backend/api/v1/workflows.py#L313)、[生命周期测试](../../tests/integration/test_studio_lifecycle_api.py#L271)。

**对说明书的影响：** “所有运行都执行不可变发布版本”当前不能作为无条件规则。页面有工作流 ID，也不等于运行来自指定版本。Studio 的正常接口选择当前发布版本，因此 API 调用、调度固定某一旧版本和运行记录保留版本，是不同能力。

**需要落实的产品合同：** 区分正式项目执行与直接传图执行的用途、权限和结果归属，并明确哪些入口构成推荐的生产路径。不能只在文档隐藏另一条实现路径。

**验收证据：** 从各正式入口启动相同工作，版本、身份、范围和结果归属可以对齐；例外入口有明确合同与检查。

## 4. 保存运行证据与保存全部业务数据不是同一步

**源码事实：** `workflow_runs` 和 `workflow_run_events` 持久化运行及事件；runtime 还有内存中的 `outputs_by_node`，部分 trace 只保留样本。业务 `CollectedRecord` 的明确写入边界在 record/inbox 存储绑定等具体路径。不能推导出任何来源节点的所有输出都会自动进入“成果与数据”。

依据：[运行模型](../../backend/models/workflow_run.py)、[运行事件持久化](../../backend/workflow/opencli_hda_tracer.py#L2614)、[节点输出与存储分支](../../backend/workflow/opencli_hda_tracer.py#L4900)、[记录写入分支](../../backend/workflow/opencli_hda_tracer.py#L5104)。

**对说明书的影响：** README 的“采集结果统一进入成果与数据”必须说明生效条件。用户看到成功、数量或一段输出，仍不能确认完整内容已保存且可由消费者读取。

**需要落实的产品合同：** 清楚定义哪些节点输出是中间值，哪些成为业务记录，哪些成为证据或其他产物；消费入口需要返回与具体运行一致的数据和来源。

**验收证据：** 一批已知输入经过采集/处理/保存，重新读取后数量、内容与血缘可以核对；界面与 API 读取的结果一致。

## 5. 交付节点名称不能证明外部发送已经发生

**源码事实：** `workflow.notify.send` 分支生成通知相关元数据和上游项的投影；Webhook 分支有实际出站请求。独立的受治理交付路径又有授权、执行、重试和未知结果核对。未在已核对的普通工作流完成路径中找到自动串接全部授权/执行事实的证据。

依据：[通知和 Webhook 分支](../../backend/workflow/opencli_hda_tracer.py#L5166)、[交付授权接口](../../backend/api/v1/delivery_authorization_routes.py#L183)、[交付执行接口](../../backend/api/v1/delivery_execution_routes.py#L32)、[交付执行实现](../../backend/workflow/delivery_execution.py#L418)。

**对说明书的影响：** 不能把“通知元数据已生成”“HTTP 请求已提交”“对方已接收”“业务处理已完成”写成同一种成功。多种通知渠道存在，也不足以支撑所有工作流都能沿同一教程交付。

**需要落实的产品合同：** 对正式支持的每一种交付途径明确授权、实际发送、回执、重试和重复投递规则，说明由哪个入口触发。

**验收证据：** 经授权的测试接收方实际收到内容，保留来源运行和交付标识；失败与未知结果能够查询和核对，不靠页面投影认定成功。

## 6. 恢复规则随执行类型分裂

**源码事实：** 来源调度跳过停机期间错过的执行；Operations Agent 对排队与运行中任务有不同启动恢复规则；主服务启动还处理 managed acquisition 和特定业务任务。部分 Workflow 有外部输出续接或专用恢复路径，不能据此承诺任意中断 Workflow 自动恢复。

依据：[来源调度水位](../../backend/scheduler.py#L94)、[Agent 计划任务恢复](../../backend/services/scheduled_run_recovery.py#L24)、[主服务启动恢复](../../backend/main.py#L147)、[工作流续接](../../backend/workflow/opencli_hda_tracer.py#L2796)。

**对说明书的影响：** “重启后继续运行”需要明确是哪一类工作、从哪里继续、可能重复哪些外部动作。

**需要落实的产品合同：** 对持久定义、运行恢复、结果保存、外部副作用分别承诺。恢复指引需要以运行类型和状态为条件。

**验收证据：** 在授权测试环境中断一次运行，恢复后能解释已完成部分、待处理部分、重复风险和最终结果。

## 7. 人的操作入口没有完整承载已有生命周期

**调查时的现场与源码事实：** 项目创建菜单提供空白、模板、DSL；专门的 Agent 创建页没有从该菜单链接。工作流页的验证和发布位于右下角折叠的“状态”内；主画面突出“运行”。项目标签不携带完整 run/trace 参数，部分目的页明确提示当前未按 Run 筛选。

**2026-09-05 前端修复：** 验证与发布改为画布上方常驻操作；保存按钮、快捷键和命令面板接入项目草稿保存；运行入口先展示输入面板，再由用户启动。面包屑显示实际项目页面并提供返回链接，同一项目/工作流的标签保留 run/trace。创建入口可发现性与数据按 Run 过滤仍未解决，本条不能据此整体关闭。

依据：[创建菜单](<../../frontend/app/(app)/studio/page.tsx#L210>)、[Agent 创建页面](<../../frontend/app/(app)/studio/new/page.tsx#L455>)、[生命周期与保存操作](../../frontend/components/flow/workflow-editor-session.tsx)、[项目导航](../../frontend/components/studio/project-navigation.tsx)、[运行上下文提示](../../frontend/components/studio/run-context-banner.tsx)。

**对说明书的影响：** 用户需要额外知道隐藏入口、不同数据范围和状态含义，才能尝试照步骤完成任务。多个页面视觉相似，仍可能呈现不同范围的事实。

**需要落实的产品合同：** 从同一目标到创建、发布、执行、结果和修改的操作应连续，项目/工作流/运行/时间范围可辨认、可保留。现有 [Project Navigation 设计](../../CONTEXT.md#L370) 与八个实际标签也需要校准，不能直接把改标签数量当作问题已解决。

**验收证据：** 新使用者按照说明独立完成任务；切换页面后能准确回答“现在看的是哪次执行的哪些数据”。

## 8. 历史调查和当前实现缺少清晰的时间边界

**源码事实：** 早期 [golden-path 调查](../wayfinder/golden-path/assets/audit-existing-golden-path-seams.md) 写着没有 Project、Draft、Version 数据库事实。当前已有 [Studio 模型](../../backend/models/studio.py)、[事务性 bootstrap](../../backend/api/v1/studio_projects.py#L73) 和 [验证/发布实现](../../backend/api/v1/studio_lifecycle.py#L223)。本次生命周期测试也通过。

**对说明书的影响：** 从旧调查开始会错误地重新规划已实现能力；把 ADR 当操作说明又会跳过尚未接线的能力。文档数量增加不能自动提供可信入口。

**本轮已做的校准：** 给旧调查增加历史说明并指向本记录；提供新的当前源码使用说明入口。保留原调查作为历史证据，不改写其原始发现。其他文档的能力宣称仍需逐项以行为证据校准。

**后续验收证据：** 每个主要产品承诺能定位到对应版本的操作步骤、实现和行为检查；新入口不再把历史缺口当作当前事实。

## 9. 部署实例与源码基线的对应关系尚未证明

**现场事实：** 本机前端 `8030` 与后端 `8031` 的 `/health` 返回相同实例标识。后端 OpenAPI 返回 `0.4.1` 和 271 个路径。Docker API、浏览器和另一套前端的镜像标签均为 `0.4.1`，但标签不是源码提交证明。

**对说明书的影响：** 本地 main 上存在某接口或某项修复，不能直接当作运行服务已经部署它。源码测试和现场页面观察必须分别标注。

**需要落实的产品合同：** 安装版本、源码构建、正在运行的前后端版本和可用能力要有可核对的关联。默认地址与本机覆盖端口也应区分。

**验收证据：** 使用同一确定版本从安装开始完成说明书步骤，能够记录和核对前后端实际构建身份。

## 本次验证记录

### 实际运行的检查

- 前端 `http://127.0.0.1:8030/health`：200。
- 后端 `http://127.0.0.1:8031/health`：200，与前端代理返回相同实例标识。
- 后端 `/openapi.json`：200，271 个路径，包含 Studio bootstrap、草稿、验证、发布、运行及证据路径。路由出现仅证明公开合同存在。
- Docker 只读清单：API、浏览器、前端、协作服务健康；独立 Docker 前端映射 `3000 → 3010`。
- 浏览器读取了已登录的概览页；前一轮同源码基线的工作流页观察用于核对导航、原始技术字段及“状态”入口。本轮后台新标签访问项目运行页转入登录，没有复制登录凭证或继续执行写操作。
- 9 项后端/MCP 检查通过，97 条现有弃用警告。测试数据库为内存 SQLite，MCP 映射测试模拟 REST。没有访问生产采集目标或发送外部通知。

复现测试集合（在已安装项目测试依赖的环境）：

```text
python -m pytest -q --no-cov -p no:cacheprovider
  tests/unit/test_mcp_server.py
  tests/integration/test_studio_lifecycle_api.py::test_studio_workflow_draft_validation_run_is_persisted
  tests/integration/test_studio_lifecycle_api.py::test_studio_workflow_current_validated_revision_can_be_published
  tests/integration/test_studio_lifecycle_api.py::test_studio_api_run_requires_a_published_version
  tests/integration/test_studio_lifecycle_api.py::test_studio_api_run_is_version_bound_idempotent_and_visible_in_logs
  tests/integration/test_studio_lifecycle_api.py::test_studio_workflow_rejects_validation_from_an_older_draft_revision
  tests/integration/test_studio_lifecycle_api.py::test_studio_workflow_versions_keep_immutable_graph_snapshots
```

上面为换行展示的单条命令参数，执行时需合并为一行或使用当前 shell 的续行方式。`--no-cov` 表示本次没有声称达到全仓库覆盖率门槛。

### 没有完成的验证

没有重建或升级运行中的镜像，没有在生产数据上创建测试项目，没有完成“真实采集 → 两次计划执行 → 完整记录读取 → 外部接收 → 中断恢复”的端到端演练。本文列出的实现缺口不能通过九项局部测试消除。

### 路由与复核

本任务属于跨模块产品契约核对：主 Agent 负责文档与现场观察，Sol High 核对后端生命周期，Luna High 核对 Agent/开发者入口，另由 Sol High 独立复核核心缺口和两份草稿。文档由主 Agent 统一编写。Docker 清单和测试初始化因沙箱权限限制经审批后重跑；没有改变服务或认证配置。

独立复核确认标准调度绑定、MCP 持久化断点、记录/通知节点行为及两类执行入口的结论，未发现两份草稿的材料性错误。复核另运行了 9 项定向测试：MCP 3 项、来源调度 CRUD、Automation 重复扫描、runtime 存储绑定、Webhook ingress、Studio 版本绑定运行、Fleet 缺失令牌拒绝。该批次与主检查有 4 项重叠，不能把两批简单相加称为 18 个不同用例。全部通过；仍未执行真实外部采集和交付。
