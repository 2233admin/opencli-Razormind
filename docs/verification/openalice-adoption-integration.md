# OpenAlice 基础模块整合验收

日期：2026-09-06。任务事实源为 [父任务 #116](https://github.com/2233admin/opencli-Razormind/issues/116)。最新状态：#121 读取与生产来源、#122 阅读器、#123 工作状态与 #124 项目/Inbox 接线已验收。#125 的严格接入、配置界面、原会话回复与报告领取已实现；权限修正通过独立复审和 root 82 项整合回归，完整实际浏览器旅程再次通过。SDK 日志隔离已接入，最后的日志捕获测试和父目标最终审查尚待完成。隔离预览已更新；没有合并主分支或修改业务数据。

下文保留分阶段验证记录，早期的待完成描述只代表当时状态，当前进度以本段和最新 Issue 评论为准。

## 整合范围

隔离分支 `codex/openalice-adoption-integration-20260906` 以本地源快照 `08f0c9fa` 为基线，接入报告阅读器提交 `269e0fcb`、`e5bde339` 与工作状态提交 `ea5f04dd`、`9005681d`。主目录、主分支与业务数据库未随此整合更新。

报告解析复用 Marked、DOMPurify、highlight.js 及原有 XLSX 依赖。协调者修正 CSV 表头丢失、含制表符的 CSV 字段误判、数字字符串格式丢失，并补充 Markdown 自动资源限制和可重复的真实组件浏览器测试。测试应用位于 `frontend/e2e/report-content-harness/`，不属于产品路由。

工作状态复用原有运行、自动化与会话存储。Studio 页面请求通过既有受限管理员桥接解析到 governed Workspace；读取会话继续执行原有权限检查；变更操作与配置链接使用 governed Workspace，项目导航保留 Studio Workspace。配置页只接受已授权列表中的目标。

## 新鲜验证证据

- `python -m pytest tests/unit/test_agent_work_health.py tests/integration/test_studio_agent_session_access.py --no-cov -q`：12 项通过。
- 前端 `node --test scripts/check-report-content.mjs scripts/check-agent-work-health.mjs scripts/operations-agent-deep-link.test.mjs`：9 项通过。源码合同检查仅作为辅助证据。
- 前端 `pnpm test:report-content`：3 项真实组件浏览器测试通过，覆盖 SSR 页面响应、恶意 Markdown、引用链接、剪贴板正文、HTML sandbox/CSP、CSV/TSV/对象列对齐、375px 窄屏及回退状态。
- 前端完整 `tsc --noEmit` 通过；本次变更的 ESLint 无错误，默认 Playwright 配置中有一个原有未使用变量警告；`git diff --check` 通过。
- 独立只读审查分别接受工作状态修正与报告阅读器模块范围。父目标的最终独立验收尚未执行。

Chromium 会为被 CSP 阻止的 CSS `@import` 和背景图发出 `request` 事件。测试同时断言这些尝试以 `csp` 失败、未进入网络拦截器，避免把“产生事件”误判成“成功联网”。恶意内容未获得父页面权限。

## 环境与限制

验收使用已安装依赖的只读 `node_modules` junction 和 webpack 测试应用；没有通过共享 junction 安装依赖。干净安装被基线已存在的锁文件 `hono` override 与配置不一致问题阻止，这并非本次新增变更。没有改动该既有依赖策略，也没有执行生产数据库迁移或真实外部通知。

#121 的普通运行产物与来源修正已完成并挂载到实际 API，协调者复测产物与 Records 专属测试 9 项通过，独立审查接受。#124 的产品页面接线已进入可见预览；#125 仍在渠道预检，整批任务尚未完成，不能据此认定可以合入主项目。

路由记录：Luna 负责产物/阅读器实现，Sol 负责运行状态及权限修正；协调者负责整合、阅读器收尾和复测，独立审查者负责只读验收。首轮发现真实接入遗漏后退回修正；本轮还通过浏览器证据区分了 CSP 阻断与实际网络访问。

## 产品预览与当前接线

用户要求先查看前端后，整合分支接入 #124 的 `84a55104`、`5f30f8e9`，对应整合提交 `021a6049`、`aba9d28d`。项目数据页新增产物列表、共享阅读器、来源和原会话入口；项目概览嵌入工作状态；Inbox 提案和审批详情复用同一产物面板。记录列表、分析和导出共同使用工作流与精确运行范围。

本机预览前端为 `http://127.0.0.1:8047`，连接 `http://127.0.0.1:8046` 上的实际整合后端。预览使用独立 SQLite 示例库，所有展示记录均为模拟；后端关闭 lifespan，未执行业务数据迁移、调度恢复、真实模型调用或外部消息发送。主目录和原业务服务保持原状。

协调者通过正常登录流程实际检查了项目概览、产物列表、Markdown 正文、来源详情、已保存会话展示，以及“打开原会话”携带项目/工作流/运行范围恢复相同 conversation。完整 TypeScript 检查与现有会话/工作区回归 8 项通过。直接调用已安装的 Node 工具完成检查，避免 pnpm 的自动安装行为触及外部依赖 junction。

实际刷新暴露了父视图未恢复和列表未加载即清理 artifact 参数的问题。Luna 在 `4fa5fc1f` 修正，整合提交为 `a9194116`；同时修正侧栏宽度与切换运行时重置页码。独立审查接受修正。

协调者在实际前后端上另起隔离浏览器上下文复测通过：正常登录、首次访问报告链接、刷新恢复、已保存会话正文、切回数据集、无效 ID 清理、桌面报告宽度 672px、375px 手机报告满宽且页面无横向溢出；全程无浏览器页面异常。截图保存在本机预览辅助目录。修正后完整 TypeScript 和两处文件的 ESLint 再次通过。

#124 的跨项目/跨运行及回复写回等完整端到端验收仍需补齐；#125 预检发现现有飞书渠道只有出站能力，需明确官方 SDK、持久身份/事件映射和 Connector 授权合同后才能实现。本记录接受当前可见前端预览，不宣称所有功能已交付或可以合入主分支。

## 首页恢复与生产来源复核

整合提交 `77be81c9` 复用 AppShell 工作标签，修正裸 `/studio` 缺少 Workspace URL、标签丢弃 artifact/view 的问题。4 项工作标签回归、完整 TypeScript 与定向 ESLint 通过；独立审查接受。实际前后端浏览器新增验证通过：报告打开原会话，回到裸首页并恢复同一会话；再次回首页并恢复同一报告正文。页面在 375px 下继续无横向溢出，没有 pageerror。Records 参数类型在 `2fd07d91` 统一复用，消除临时类型断言，类型检查通过；共享 hooks 中保留一个与本次无关的既有 unused-options lint 警告。

提前父目标复核确认并览界面与首页恢复已具备，发现生产来源尚未贯通：当前 Agent 只创建/修改草稿，用户后续手动运行时没有传递该会话；native writer 也未保存可信 conversation。预览示例和消费端测试不能证明该生产链路。已按 #121 新派发合同交 Sol 在独立 provenance worktree 实施，要求真实身份/范围复验、服务端运行来源、防输入伪造、幂等与恢复约束。#124 的真实 HTTP/隔离库 E2E 由 Luna 并行补齐，测试不使用用户预览数据库。

#125 的 Proposed ADR 0046 已整合为 `5615922a`，尚未开放实施。官方 SDK 高层入口的提前 ACK/去重时序不满足持久接收要求，草案改用需要独立审查的同步验证入口与数据库收件边界。该设计交付不代表外部回复或领取功能已经实现。

## 项目旅程验收与来源退回

Luna 的端到端提交 `041ee94f` 已整合为 `3f915c57`，Sol 的生产来源提交 `bf6dee84` 已整合为 `a7d5f70b`。协调者在整合树独立复跑：

- `pytest tests/integration/test_workflow_conversation_origin.py tests/unit/test_native_artifact_conversation_origin.py tests/integration/test_studio_lifecycle_api.py tests/integration/test_project_artifacts.py --no-cov -q`：36 项通过。
- `pytest tests/integration/test_openalice_workspace_journey.py tests/unit/test_native_intelligence_contracts.py tests/unit/test_intelligence_store_dialects.py tests/integration/test_workflow_native_intelligence_lifecycle.py --no-cov -q`：69 项通过。
- `OPENALICE_PYTHON=<本机隔离 Python> OPENCLI_NEXT_DIST_DIR=.next-openalice-e2e-8049 node node_modules/@playwright/test/cli.js test --config playwright.openalice-workspace.config.mjs`：1 条完整浏览器旅程通过，使用临时测试库与 8048/8049，结束后两端口已释放，8046/8047 预览继续运行。
- `tsc --noEmit --incremental false`、来源/工作标签/Playwright 配置的 9 项 Node 检查通过；修改范围 ESLint 无错误，默认配置保留一个已有 unused-variable 警告。协调者另在 `4e9465e2` 隔离默认浏览器套件，移除验收 runner 的机器专属 Python 路径，并忽略其临时库。

上述 105 项后端测试是定向验证，不是全仓覆盖率认证。第一次调用遗漏 `--no-cov`，13 项断言通过但命中全仓 80% 覆盖率门槛，命令失败；随后按定向模式完整复跑并通过，没有改低仓库覆盖率要求。

浏览器旅程使用真实前端、HTTP API 和 SQLite，只有现有 `chat.run_chat_request` 边界被替换为确定性响应：两项目/三运行产物与 Records 隔离、报告正文、原会话追问实际写回、关闭/缺失会话状态、Inbox 提案并览、当前运行 CSV 导出均通过。它的产物来源是 fixture 中显式保存的关联，因此不作为 native 生产来源证据。结合此前正常登录、刷新/首页恢复、375px 与安全阅读器验证，#124 接受其完整合同范围。

独立审查在生产来源实现中发现一项阻断：`research_continuation._load_run` 丢弃服务端来源，子 run 的 native writer 因而失去 conversation。该问题已按 [#121 退回合同](https://github.com/2233admin/opencli-Razormind/issues/121#issuecomment-5555099605) 交 Luna 在原 provenance 工作树修正，原 reviewer 随后复审。身份/owner/RBAC/精确范围、普通输入防伪、HDA continue/replay 与 writer 注入的其余重点已被审查接受。生产来源整体仍未验收，不开放 connector artifact grant。

#125 ADR 0046 的官方同步 dispatcher 与持久 ACK 边界已通过独立审查，状态改为 Accepted。[第一波实施合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555078767) 已发布并远端回读：Sol 仅负责安装、绑定、严格 SDK 入站和持久收件基础；原 Agent 回复、出站、产物领取和共享注册仍属于后续接线。SDK 的真实 wheel/线程/超时后迟到提交测试是验收门槛，尚未完成这项实现。

本轮路由：Luna 交付项目 E2E 并接手明确的研究续跑修复；Sol 交付来源链并转入连接器安全边界；协调者完成集成和上述复跑；独立 reviewer 找到未覆盖的研究续跑路径后退回。业务数据、实际外部消息和主目录未改动，父目标最终独立验收仍待全部任务完成。

## 来源完整验收与连接器严格入口修正

研究续跑修复 `366ad171` 已整合为 `dbdadcdb`，原独立 reviewer 接受。测试真实 published HTTP 父 run → continuation service → child native writer，来源保持同一 conversation；没有来源的父 run 仍无来源，客户端不能重绑，旧产物 hash 保持不变。generic continuation HTTP 原有的 Studio 拒绝不放宽，不能把 service 验证表述为该 HTTP 入口已经对 Studio 开放。[#121 验收评论](https://github.com/2233admin/opencli-Razormind/issues/121#issuecomment-5555356259) 已发布并远端回读。

连接器基础 `8e37e909` 整合为 `28267872`；协调者在 `336370d0` 注册模型和生产 router，Fleet Auth 只豁免精确 callback 的 POST，并标注独立 SDK 认证的 OpenAPI 合同。新的真实 create_app + SQLite + 固定 SDK 测试证明正确加密签名事件/URL challenge、重推一个 receipt、错误签名无写入，以及相邻方法/路径继续401。基础与来源34项、接线/Fleet/研究既有回归54项通过；OpenAPI 补充断言后单项复跑通过，Ruff与uv lock check通过。未运行全仓覆盖率认证。

SDK 要求 websockets>=11,<16，锁文件因此从16.1.1调整到15.0.1。Luna 单独核查项目实际调用和该版本签名，6项既有Agent/userscript测试与本地loopback握手/收发通过，未发现兼容阻断。

基础独立审查结论仍为 REVISE：固定SDK对空密钥配置的明文无签名事件可返回200并执行callback；明文URL challenge也可能先于签名检查返回200。新[修复合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555371293)要求完整可解凭据、严格加密envelope/必要头、SDK继续负责全部验签解密，同时补reply target边界、日志脱敏和disabled/revoked已验证重推的稳定receipt。共享接线本身已被独立审查接受，基础整体尚未验收或开放实际外部使用。

Sol 在connector工作树修正上述边界，并增加仅当前用户的my-binding读取；Luna在独立UI工作树按[前端合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555391202)复用/providers/catalog实现安装、本人绑定和健康状态，不把callback readiness当作已绑定。完整原Agent回复/出站/产物grant留待下一波，不展示虚假的发送能力。

后续会话grant需明确处理conversation.revision随正常turn递增的事实：不能让grant因自身成功回复而失效，也不能跳过关闭、授权context变更、撤销和降权重检。此为第二波待审合同要求，尚无对应实现。本轮仍未重启或变更8046/8047预览、原有业务服务或业务数据库。

## 严格接入与配置界面验收

此前的 SDK 降级问题已由 `a4dfce5b` 修复并整合为 `fc341ca5`，原独立 reviewer 接受。协调者在整合树使用真实固定 SDK wheel 和临时 SQLite 重跑 migration、runtime、receipt、installation 及实际 create_app callback 测试，49 项通过。完整凭据门禁、唯一加密 envelope/签名头、SDK 验签解密、reply target 上限、错误脱敏、停用/撤销后的已验签持久拒绝与 ACK、本人绑定读取均在此次范围内。[后端验收记录](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555574416) 已发布并远端回读。

连接设置页面复用 `/providers/catalog`，`b3dccbf3` 和 `2a88fcdf` 分别整合为 `c4fa638e`、`828e37df`。界面读取真实 governed Workspace 成员角色，仅管理员/维护者显示安装和编辑；本人绑定来自独立 my-binding API，未把配置就绪误标为本人已绑定。新建表单不提供后端不支持的启停选项，编辑时秘密留空保留现值。独立 UI 审查接受授权与秘密边界。

协调者在 `85d9ac81` 补齐可重复的隔离浏览器验收，复用现有 OpenAlice runner，新增局部虚构工作区 fixture。测试使用真实 Next 页面、HTTP、SQLite 和固定 SDK；只在本机使用虚构密钥构造加密签名事件，没有响应拦截或真实飞书请求。专用配置隔离于默认 smoke 套件。

- `node node_modules/@playwright/test/cli.js test --config playwright.feishu-connectors.config.mjs`：2 条完整浏览器测试通过。包含安装、API 不返回秘密、健康状态、生成绑定指令、真实加密 callback 及重推、刷新已绑定、UI 撤销、编辑秘密空白、停用后禁用绑定、375px 无溢出、跨 Workspace 拒绝，以及 Viewer 可以管理本人绑定但不能编辑安装。
- 全量 TypeScript、面板/API/测试配置定向 ESLint、fixture/runner Ruff 和 `git diff --check` 通过。
- 临时测试端口 8048/8051 已释放；用户预览保持 8047，8046 只重启隔离后端加载新代码。预览 SQLite 备份后仅新增四张 connector 表和一条明确标注模拟、已停用的连接，不执行其他表迁移。
- 另以正常本地登录实际检查 8047 配置页：真实角色、模拟连接、未绑定状态、停用限制、秘密不回显、375px 无溢出、无 pageerror，桌面与手机截图已检查。

本轮路由：Sol 负责 P1 安全边界修复，Luna 负责配置 UI，协调者补真实 SDK 浏览器闭环、整合和预览，独立 reviewer 分别复审后端和 UI。当前 P2 执行合同在独立安全审阅；健康页面明确提示回复与产物领取尚不可用。业务数据、真实模型/飞书消息和远程主分支未改动，父目标最终独立验收仍未执行。

## P2 正式派发

[P2 正式合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555789755)已由原独立 reviewer 接受；root 发布后回读 30,771 字符全文，与本地合同规范化换行后完全一致。此前要求的创建幂等、会话短事务 CAS、运行时 readiness、单密钥失败、分阶段 SDK 超时，以及 claim receipt 脱敏、generation 字段和 artifact failed 终态均已写成实施及测试约束。官方 Markdown 文档经直接 HTTP 再核对 UUID 一小时降重限制，故结果不确定的 send 禁止自动重发。

Sol 已在干净的 `codex/openalice-replies-20260906`（基线 `4befc988`）开始后端实现，当前尚未交付或验收。root 在整合树 `92302911` 增加默认关闭的两个 rollout 配置，Ruff 与 diff check 通过；后续 main/model registry 接线由 root 独占。Luna 完成只读前端预检，明确复用受授权会话读取获得 governed Workspace，并补齐所需的本人 grant 状态读取/撤销合同；前端实施等待实际 API 交付。

本记录只确认设计审查、正式派发和配置准备，不确认 P2 回复/产物领取已可用。预览 8046/8047 仍运行，原目录改动与业务服务/数据不变。

## P2 接线与浏览器验收准备

真实 worker 接口交付后，root 在 `94338d92` 接入生产 connector 生命周期：默认关闭时跳过导入和启动；开启时依次 start、recover，并在启动、恢复或服务异常后执行 stop。实际 `create_app(app_settings=...)` 的 6 项 worker 边界测试与 Ruff 通过，独立 reviewer 接受这部分接线。测试替换了 worker 边界，因此尚不证明实际 P2 worker 可用。

[报告授权界面合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555932351)全文远端回读匹配后，Luna 在独立 `codex/openalice-replies-ui-20260906` 开始实施授权、投递状态刷新、撤销与专属浏览器旅程。Sol 继续负责 P2 后端；root 独占共享接线和测试环境，不交叉修改同一文件。

root 的 `6de34f57` 准备了隔离 P2 浏览器环境。它故意使用不同的 governed 与 Studio Workspace ID，通过真实生产 connector lifespan、HTTP、SQLite 和固定 SDK 入站检查范围映射；仅现有 chat 执行边界和 SDK 外发网络使用本地替身。外发观察端点仅由测试 runner 注册，限制本机与测试 token，记录仅存于临时进程内，不进入产品路由。共享 fixture 原有 HTTP 完整旅程 1 项回归、Ruff、Node 语法及定向 ESLint 通过；默认 Playwright 配置保留一个原有警告。

实际 P2 前后端尚待整合，新的回复/领取浏览器旅程未运行，不能将测试环境准备视为功能验收。此时 8046 健康接口和 8047 `/studio` 均返回 200，现有用户预览继续可用。

## P2 第一固定版本复核

后端 `69eb63dd` 整合为 `97bb7fbd`，root 在 `6cc06639` 注册模型；前端 `fe38389a` 整合为 `8c05f305`。root 独立复跑 P2/迁移/生命周期 26 项，以及原会话/P1/实际应用 callback 回归 60 项，全部通过；完整 TypeScript 和前端修改范围 ESLint 通过。SQLite 清理测试对新表的循环外键发出排序警告，独立迁移 upgrade/downgrade 断言通过。

真实临时 HTTP 服务使用生产 connector 生命周期，实际 readiness 和本人 grant 列表读取通过。root 在 `f0fdee1d` 修复仅测试 observer 被最后 MCP root mount 遮蔽的顺序问题；实测测试 token 返回 200，匿名返回 403。独立 reviewer 接受 fixture 隔离；固定 adapter 在调用时导入可替换 SDK 类，没有提前缓存第二条发送路径。

[固定版本审查记录](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5556053023)为 REVISE：后端须修正旧 worker 无 fence 失败写入和撤销后 artifact receipt 永久 processing；前端须修正领取 detail 撤销缓存、最终交付轮询和 failed 新授权，并补实际 redeem/revoke 浏览器闭环。原执行者按原 ownership 修复，独立 reviewer 随后复审。测试通过尚不等于本版本可验收。

为使现有预览读取新接口，8046 在 connector 开关均显式关闭、lifespan off 下重启。仅预览 SQLite 应用了专属 P2 connector schema：事前使用 SQLite backup 保留副本，前后所有非 connector 模拟记录的 dump hash 相同；原业务数据库及服务未动。临时 worker 检查端口 8054 已停止，8047 用户预览继续运行。回复和领取执行仍待修正及完整验收，不启用真实外发。

## SQLite 原子领取与报告交付闭环

root 在 `65fb9055` 增加独立 SQLite 并发回归，复现两个 worker 同时领取同一 delivery 且 generation 均为 1。后端 `c31425b5`（整合 `e9acbe7e`）改用条件 UPDATE/RETURNING 争唯一胜者，最终写入双 fence，并让 connect 后复授权事务先获得 SQLite 写锁；恢复扫描以游标避免持续重试的队头饿死后续任务。真实 native Markdown 的 `content` 文本字段也已适配。原 reviewer 接受这些固定修复范围。

UI `b93594db`、`cf34fc6d` 整合为 `c9543450`、`bf18d5b8`，修正撤销后的 detail 缓存、最终交付轮询、failed 新授权、真实 EXPORT 角色和可信 draft run。claim 保持仅组件内存；`retryable_failed` 继续轮询并明确等待后台重试。独立 UI 复审接受。

root 独立 SQLite race/P2/migration/原会话 service **38 项通过**。完整 `playwright.connector-artifacts.config.mjs` 浏览器旅程 **1 项通过**：真实绑定、激活、回复原会话、offer、撤销与旧 claim 拒绝、新 request ID 重新授权、领取报告正文及 redeemed/sent、刷新不回显 claim、scope/Viewer 403 与 375px。测试运行真实前后端、SQLite、生产 connector worker 和 SDK 入站，仅原 chat seam/SDK 外发网络为本地替身。Next 字体下载超时使用回退字体，不影响测试通过。

8048/8053 临时测试服务已释放，8046/8047 预览继续 HTTP 200，app 内已有已认证报告详情可直接查看。完整 P2 矩阵中的媒体边界、敏感信息扫描、异常结果分类和部分恢复证据仍在补齐，最终父目标独立审查尚未执行；本节不宣称 #125 或整批工作已完成。

## P2 异常边界测试补齐

Sol 的 tests-only `d9958c55` 整合为 `52d17146`，只修改 P2 专属测试。root 在整合树独立复跑 SQLite 并发回归、P2 与迁移三个文件，**41 项通过**（164.19 秒）；没有因 tests-only 提交重复运行此前已通过的完整浏览器旅程。新增证据覆盖 JSON 内存序列化、未知及非文本媒体、UTF-8/1 MiB 边界、failed/proposal 已提交状态恢复不重跑模型、SDK 成功无 message ID 和失败结果分类、SDK 异常日志及真实 ASGI 读取/错误响应敏感值扫描。执行者另报 P1/来源四文件 50 项通过，按执行者证据记录。

父目标只读预审进行中。部分跨用户/跨范围创建、缺失密钥和 claim 失效组合仍需按具体测试核对，不能以 root 此前 38 项或单条浏览器旅程概称全部组合已测。使用说明新增飞书原会话授权、领取与撤销的实际操作路径；本地预览继续使用模拟数据和已停用连接，业务数据库及真实平台未改。

## 管理身份与日志边界修正

权限补测 `d293008c`、`d94a373b` 分别整合为 `58457db3`、`4ff82030`，root 独立复现普通 OIDC 身份可创建 Studio 原会话授权的失败。Sol 的 `ec3c1578`（整合 `ad9c2f5a`）在两类 grant 的人类创建、重放、读取、列表和撤销入口复用原有 Studio resolver；sealed connector 运行身份继续为非管理员。原 reviewer 接受修复。`4a511dd4`（整合 `9dd0c137`）另补不同 request ID 同 active slot 并发、固定 canonical 向量、缺失/空密钥终态与新授权、simulated 冻结和安全文件名。

root 在 `ad9c2f5a` 独立运行 P2、SQLite 并发、迁移与原会话 service 四文件，**82 项通过**（328.39 秒）。包含此前失败的 OIDC 拒绝和上述补充边界。执行者另报 74 项专属回归及 50 项 P1/来源回归通过；不混同为 root 本轮复跑。

root 的 `b4d129e7` 用生产数据库配置、DEBUG=true 和临时 SQLite 复现 SQL 参数与异常文本含虚构敏感标记。`e6a5a4a5` 仅增加标准 `hide_parameters=True`，独立日志回归及既有事务回调 **7 项通过**，原 reviewer 接受。SDK 自带 `Lark` stdout handler 的独立风险由 Luna helper `f4c5f238`（整合 `d9fc48fd`）与 root 两入口接线 `3eabf340` 隔离；原 reviewer 接受源码时机，实际捕获测试仍待最终复跑。

在 `3eabf340` 上，完整 P2 真实浏览器旅程再次 **1 项通过**（用例 24.5 秒，含启动总计 57 秒）。隔离预览后端随后加载本轮权限与日志修正，原有正常登录、报告/会话/首页恢复、375px 和无页面异常 **11 项通过**；8046/8047 正常，模拟连接及外部执行仍关闭。没有执行新迁移、真实平台请求或业务数据写入。
