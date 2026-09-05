# OpenAlice 基础模块整合验收

日期：2026-09-06。任务事实源为 [父任务 #116](https://github.com/2233admin/opencli-Razormind/issues/116)。最新状态：#121 读取与完整生产来源、#122 阅读器、#123 工作状态与 #124 项目/Inbox 接线已通过各自验收。#125 基础已整合测试，但独立审查发现 SDK 降级路径，正在修正；连接设置前端并行实施。整批任务和主分支合并尚未完成。

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
