# ADR 0046：以显式授权接入飞书 Agent 回复与产物领取

- 状态：Accepted（分波次实施，尚未完成渠道功能）
- 日期：2026-09-06
- 关联：GitHub Issue #125，ADR 0045

## 背景

平台已有飞书自定义机器人通知、飞书多维表格交付、持久 Agent 会话和按
Workspace/Project 授权读取产物的能力，但这些能力不能直接组成可信的双向消息
通道：

- `FeishuNotifier` 只向自定义机器人 Webhook 发送消息，拿不到可用于关联用户
  回复的消息 ID；
- `DeliveryConnection` 保存的是多维表格应用凭据，没有 Workspace 所有权，也
  不是消息会话；
- `NotificationLog` 的 ACK 只有共享 HMAC，没有飞书用户、租户、项目、会话或
  消息绑定；
- Studio Agent 会话依据 ADR 0045 只允许本地或 bootstrap 平台管理员通过
  Studio bridge 访问。把飞书用户伪装成 `local-admin` 会绕过该边界；
- Fleet Auth 会拦截普通 `/api` 请求，而飞书事件服务器不能持有平台的 fleet
  token。

方案制定时，普通 workflow 产物也没有可信的原会话来源。`WorkflowRunStartRequest` 没有
conversation 字段；native executor 的 `run_context` 只有 run/workflow/trace/node；
`ArtifactProvenance` 禁止额外字段且只有 source/evidence/time，现有 producers 因而
不能写入 conversation。只有 Agent `send_message` 创建的 `ProposalProvenance` 已经
携带可信 conversation，但它没有贯通 workflow 启动链。隔离预览 seed 中展示的
conversation 关联只是演示数据，不能作为生产授权依据。

2026-09-06 独立审查接受本 ADR 的实施边界，并核对固定 v1.4.0 官方源码：同步
`EventDispatcherHandler.do(RawRequest)` 的 callback 异常返回 500，高层 Channel
handler 则不等待业务持久化。实施必须通过真实固定 wheel 的导入/签名分派测试、
thread/loop 时序测试和 timeout → late commit → retry 幂等测试。

#121 的服务端来源实现与研究续跑修复已进入整合分支 `a7d5f70b`、`dbdadcdb`，并通过
独立验收；artifact grant 本身仍须完成授权和交付验收。#125 第一波只实现安装、绑定、严格 SDK 入站和持久收件，未
实现的原 Agent 回复、出站和领取必须保持明确未就绪状态。

第一波实际 wheel 审查另确认：空密钥会跳过 SDK 的签名/token 检查，明文 URL
challenge 会在签名检查之前返回。因此本项目适配器必须先确认三项凭据均可解且
非空，并限制为严格加密 envelope 和完整签名头；验签、解密、token 检查仍由 SDK
完成。此为实施层必须补齐的合同要求，不允许用该 SDK 的降级行为降低本 ADR 边界。

飞书官方文档规定，消息事件需要在 3 秒内处理完成，否则会触发超时重推；接收
消息可能重复，消息场景应按 `message_id` 去重，不能依赖 `event_id`。事件同时
提供 `tenant_key`、发送者 `open_id`、`sender_type`、`message_id`、`chat_id`、
`parent_id`、`root_id` 和 `thread_id`。这些字段足以建立渠道身份和原消息的
绑定，但本身不授予平台权限。

## 决策

### 1. 第一阶段只支持飞书企业自建应用机器人的单聊

第一阶段使用飞书应用机器人接收 `im.message.receive_v1`。只有两类单聊消息可以
进入业务处理：一次性绑定 challenge，以及回复平台结果消息的 Agent/产物指令。
两类消息都必须满足：

1. SDK 已验证并解密回调；
2. `sender_type` 是 `user`；
3. `tenant_key` 和应用 ID 与 URL 选中的启用安装一致；
4. `chat_type` 是 `p2p`。

绑定 challenge 只能消费与 installation、Workspace、已登录平台用户绑定的未过期
一次性随机码；消费时才把已验证的 `open_id/chat_id` 写入 binding，并重检随机码
所属用户和 Workspace，不创建 Agent turn、不读取产物。其余消息的 `open_id/chat_id`
必须与现有 binding 一致，且必须通过 `parent_id`、`root_id` 或归一化后的 reply
target 指向平台先前发出的结果消息；对应 reply grant、用户、Workspace 成员关系
和对象归属在本次处理时仍须有效。

不关联平台结果消息的普通私聊、群聊、机器人消息、转发文本和只包含猜测 ID 的
消息均不进入 Agent 会话。群聊、多机器人协作、任意主动私聊、媒体输入和用户
访问令牌不属于第一阶段。

这使授权对象是“这个已验证用户在这个单聊中回复这条平台结果消息”，而不是
“任何能联系机器人的人都可以操作 Workspace”。

### 2. 固定官方 SDK 及安全模式

实现固定使用 `lark-channel-sdk[fastapi]==1.4.0`，锁文件必须固定该版本及解析后
依赖。该版本要求 Python 3.8 以上、FastAPI 0.100 以上，与本项目 Python 3.13
和 FastAPI 0.115 以上的约束兼容。发行包许可证为 `MIT AND BSD-3-Clause`；
仓库代码是 MIT，vendored 第三方代码及告知义务以该版本
`THIRD_PARTY_NOTICES.md` 为准。仓库不复制 SDK 源码。

入站和出站使用同一固定发行包，但入口不同：

- 入站使用该版本随包发布的
  `lark_channel.event.dispatcher_handler.EventDispatcherHandler` 和
  `lark_channel.core.model.raw_request.RawRequest`。dispatcher 以
  `SecurityConfig(mode="strict", allow_unsigned_encrypted_webhook=False,
  strict_error_response=True, strict_content_text=True)` 构建，只注册
  `register_p2_im_message_receive_v1`；
- FastAPI 路由在 worker thread 中调用同步 `dispatcher.do(raw_request)`。注册的同步
  message callback 把持久化 coroutine 通过 `asyncio.run_coroutine_threadsafe` 提交
  给路由启动时捕获的 application loop，并在 2 秒以内等待数据库 commit；commit
  失败或超时就抛出异常，让 dispatcher 返回 500。不得在 event-loop thread 上
  阻塞等待自身；
- URL challenge 由 dispatcher 在验签/解密和 verification token 校验后直接返回，
  不进入 message callback；普通消息只有 callback 成功提交后才能得到 200；
- 入站第一阶段只接受官方事件 schema 的 text message。callback 从已验证的 typed
  event 读取 `header.app_id/tenant_key/event_id`、sender open ID/type 和
  `message_id/chat_id/chat_type/parent_id/root_id/content`；解析 content JSON 中唯一的
  text 字符串，用标准库 `html.escape(text, quote=True)` 生成与该 SDK v1.4.0
  `_safe_content_text` 相同的受限文本，不读取未文档化对象属性；
- 出站另建 `FeishuChannel(..., transport="webhook")`，使用
  `await channel.connect_until_ready()` 和 `await channel.disconnect()` 管理生命
  周期；worker 没有可重建的 `InboundMessage`，因此使用文档化的
  `channel.send(chat_id, message, {"reply_to": provider_message_id, ...})`，保存
  `SendResult.message_id`，并提供固定 `uuid` 作为出站幂等键。

不能把 `FeishuChannel.handle_webhook_request` 当作 durable ACK 边界。v1.4.0 官方
源码中该方法只等待同步 dispatcher；其 message processor 随后调用 `schedule(...)`
把 `_handle_message_event` 投到 SDK 后台 loop，HTTP response 不等待用户 handler。
pipeline deduper 又会在用户 handler 前执行 `check_and_mark`，safety seen cache 还会在
handler 抛错后的 `finally` 中标记消息。若数据库 commit 失败，默认 Channel message
pipeline 仍可能 ACK 并吞掉后续重推。因此本 ADR 的入站不得注册
`FeishuChannel.on("message", ...)`，也不启用这两层 SDK 去重；dispatcher 只负责验签、
解密、schema 分派和“同步 callback 成败决定 HTTP 状态”，数据库 receipt 唯一约束
负责全部业务幂等。这个约束需要针对固定 v1.4.0 源码时序写回归测试；升级 SDK 前
必须重新审计。

安装必须同时配置非空的 `app_id`、`app_secret`、`encrypt_key` 和
`verification_token`。缺少任一项时安装状态为 `blocked`，回调不得降级为明文、
未签名或 compatibility 模式。SDK 文档明确说明未配置 `encrypt_key` 时不会验证
即使存在的签名头，因此平台不允许这种配置进入 enabled 状态。

官方 SDK 说明多个 worker 间的 cache 去重不是原子一致性边界；本入站路径直接
使用 dispatcher，完全不依赖 Channel pipeline/seen cache。数据库收件记录是唯一
业务幂等边界。

### 3. 回调是独立认证边界，不接收 fleet token

新增且只新增一个公网回调形状：

```text
POST /api/v1/connectors/feishu/installations/{installation_public_id}/events
```

Fleet Auth 只豁免这个精确路径模板的 POST 请求。其他 connector、管理、状态、
绑定和授权 API 继续要求正常平台身份及 fleet/本地认证。不得把整个
`/connectors` 前缀加入公开路径，也不得把 controlled-receiver 的豁免前缀借给
飞书。

回调路由先执行固定 body 大小限制，再调用 SDK。URL 中的安装 ID 只负责选择该
安装的 SDK 验证上下文，不是凭据；使用另一安装的签名、租户或应用 ID必须失败。
路由不得记录原始 body、解密内容、签名、verification token 或 encrypt key。
TLS 终止、请求速率限制和异常计数属于网关/HTTP 层；SDK 官方文档明确把这些
职责留给应用。

飞书 URL challenge 只由 SDK 完成验证和响应，不创建业务记录。普通消息只有在
SDK 验证、解密和事件类型分派成功后才能进入收件服务。

### 4. 新增显式身份绑定，不从 `open_id` 推断平台身份

新增下列持久对象：

#### `ConnectorInstallation`

- `provider="feishu"`；
- governed `workspace_id`；
- `app_id`、`tenant_key`、公开随机 `installation_public_id`；
- 加密保存 `app_secret`、`encrypt_key`、`verification_token`，读取时复用
  `backend.auth.crypto`；
- `status` 为 `blocked | active | disabled | revoked`；
- `config_revision`、`last_ready_at`、`last_error_code`、`revoked_at`；
- 唯一键为 `(workspace_id, provider, tenant_key, app_id)` 和
  `installation_public_id`。

API 永远只返回 `has_app_secret`、`has_encrypt_key`、
`has_verification_token`，不返回密文或明文。

#### `ConnectorBindingChallenge`

已登录用户在目标 Workspace 中为自己创建一次性绑定 challenge。记录包含
`installation_id`、`workspace_id`、`user_id`、随机码的 HMAC/哈希、过期时间、
尝试次数和消费时间；只返回一次明文随机码。用户在飞书机器人单聊中回复该码，
SDK 验证的 `tenant_key/open_id/chat_id` 消费 challenge。

#### `ConnectorPrincipalBinding`

绑定包含 `installation_id`、`tenant_key`、`open_id`、`p2p_chat_id`、平台
`user_id`、状态、版本和撤销信息。唯一键至少覆盖
`(installation_id, tenant_key, open_id)`；同一安装下一个平台用户不能静默绑定
多个飞书主体。绑定只证明渠道主体对应平台用户，不自动授予 Workspace 权限。

绑定前和每次使用时都读取现有 `User`、`WorkspaceMembership` 和 Workspace
状态。禁用用户、失效 Workspace 或缺失成员关系立即拒绝。禁止构造
`RequestIdentity(auth_method="local")`，也禁止把 `open_id` 拼成一个新的 OIDC
subject 后自动创建成员关系。

### 5. Studio 访问使用窄授权，不放宽 ADR 0045

新增 `ConnectorReplyGrant`，由已通过正常平台认证的用户显式创建。Grant 必须
冻结：

- installation、principal binding 和 `bound_user_id`；
- governed `workspace_id`；
- `studio_workspace_id`、`project_id`、可选 `workflow_id/run_id`；
- `conversation_id` 及创建授权时的会话 revision/context hash；
- 目标 `p2p_chat_id`；
- 平台出站 operation ID、SDK `uuid` 和发送成功后的飞书
  `provider_message_id`；
- `active | revoked | expired` 状态、版本、有效期和撤销审计字段。

第一阶段要求 `grant.bound_user_id == binding.user_id ==
conversation.created_by_user_id == granting_actor.user_id`。授权人必须同时拥有当前
Workspace 的 `workspace.read` 和 `analysis.export`，且 Studio project/workflow/run
必须与会话已存的 `context_binding` 完全一致。这样可以支持当前本地管理员把自己
的 Studio 会话显式带到自己已验证的飞书单聊，而不会给任意 OIDC/飞书用户开放
Studio bridge。

`ConnectorReplyGrant` 是独立 capability，不转换成 local/bootstrap identity。
`agent_conversation_service` 新增内部专用入口，例如
`send_connector_message(access: ConnectorConversationAccess, ...)`。该入口只能接收
由 connector authorization service 生成的不可伪造 capability 对象，并在同一
事务边界重新加载 grant、binding、用户、成员关系、会话和 Studio 对象。它复用
现有 turn 分配、上下文连续性、历史、runner、错误脱敏和 proposal 持久化逻辑；
普通 `send_message(RequestIdentity, ...)` 与
`resolve_stored_agent_session_workspace` 保持不变。

Connector 不提供 proposal approve/execute API。Agent 返回高风险 proposal 时，
飞书只收到摘要和回到平台的提示；批准仍必须通过现有受认证的平台操作完成。

### 6. 数据库是入站与出站幂等权威

新增 `ConnectorInboundReceipt`：

- 唯一键 `(installation_id, provider_message_id)`；飞书消息事件使用官方要求的
  `message_id`，不使用 `event_id` 作为幂等键；
- 保存 bounded 的 `tenant_key/open_id/chat_id/reply_to_message_id`、
  `safe_content_text`、`intent=binding|reply|artifact`、规范化请求 hash，以及可空的
  grant/conversation ID；
- 状态为 `received | processing | completed | proposal | rejected |
  retryable_failed | permanent_failed`；
- 保存 lease owner/expiry、attempt、turn ID、出站 delivery ID、稳定错误码和时间；
- 不保存原始回调、签名、token 或未裁剪的消息体。

`safe_content_text` 是 worker 恢复执行所需的权威正文，不能为空，最大 20,000
字符，并在转义后检查上限；超限消息整体拒绝，不能截断后执行。receipt commit
必须同时原子保存正文、hash、grant/conversation 及状态，不能先存 hash 再异步补
正文。worker 只从 receipt 读取这段正文，不重新读取 provider 消息，也不从日志、
原始 callback 或 hash 猜测输入。

规范化 hash 只覆盖验证后的不可变安全字段和完整 `safe_content_text`。同一
`provider_message_id` 的同一 hash 返回已有状态且不新增 Agent turn；若重复事件
仍到达应用并且 hash 不同，则标记安全冲突，不处理新内容。SDK 在进程内提前
丢弃重复消息的行为在本入站路径被禁用；所有重推都到数据库唯一约束。

Agent turn 的 `request_id` 由 installation ID 与飞书 `message_id` 的稳定哈希派生
并限制为 64 字符，从而复用 `(conversation_id, request_id)` 唯一约束。数据库
receipt 负责跨进程/重启幂等和冲突证据，Agent turn 唯一键是最后一道防重复写入。

新增 `ConnectorOutboundDelivery`，唯一绑定 grant/receipt 与 operation ID，保存
SDK 幂等 `uuid`、请求 hash、状态、provider message ID、尝试次数和稳定错误码。
状态至少区分 `pending | sent | failed | indeterminate`；超时后不能假设未发送，
必须以相同 UUID 重试或进入人工可见的 indeterminate 状态。

### 7. 回调先持久化，Agent 工作在后台完成

dispatcher 的同步 message callback 只完成以下工作，并在 2 秒内部预算内返回，
给 HTTP/SDK 留出飞书 3 秒总预算：

1. 提取和界定 SDK 已验证字段；
2. 验证安装/租户/发送者类型；
3. 按 provider message ID 原子 claim/create `ConnectorInboundReceipt`；
4. 若是绑定 challenge，在同一事务消费随机码、建立/确认唯一 binding，并把 receipt
   标为 completed；
5. 其余消息通过 provider reply target 查找 grant，绑定 receipt 的 grant/context；
6. 提交后通知 connector worker。

它不调用模型、不读取产物、不发送飞书消息。只有 receipt/binding transaction 已
commit 才返回成功；commit 失败或超时必须返回 500，让飞书重推。成功只表示事件
已持久接收，不表示 Agent 已完成。

`ConnectorReplyWorker` 是连接器传输 worker，不是新的 Agent 执行系统。它从
receipt 获取 lease，调用现有 Agent 会话服务；进程启动时恢复过期 processing
lease 和未完成 receipt。多个 API 进程通过数据库 claim 竞争，同一 receipt 只有
一个 owner。worker 生命周期接入现有 FastAPI lifespan，安装不可用时公开健康
状态为 `blocked/unavailable`，不触发模型或外部发送。

### 8. 产物领取是授权读取，不是公共下载链接

产物领取功能有一个必须先完成的来源前置条件：在已认证、已授权的 workflow 启动
边界接收可选 `conversation_id`，校验该会话与 governed Workspace、Studio
workspace/project/workflow 及发起用户完全一致，然后仅由服务端把该绑定沿
`WorkflowRunStartRequest`、持久 run request 和 native executor `run_context` 传给
`IntelligenceStore._append_artifacts`，写入扩展后的 `ArtifactProvenance`。外部触发、
定时触发以及未提供 conversation 的运行继续产生“无会话来源”产物，不能事后按
project/run 猜测 conversation。该链路完成并有回归测试以前，connector 不得为
普通 workflow 产物创建领取 grant；只能处理现有 artifact service 已验证为唯一
可信 conversation 的产物。

新增 `ConnectorArtifactGrant`，每行绑定一个 `ConnectorReplyGrant` 与精确的
artifact public ID、session ID、artifact ID、content hash、project/workflow/run
scope、状态和有效期。Grant 创建时先调用现有 project artifact service 验证
来源；领取时再次：

1. 验证 installation、binding、reply grant 和 artifact grant 有效；
2. 重检用户/Workspace 成员关系及 `workspace.read`、`analysis.export`；
3. 重检 Studio project/workflow/run/conversation 绑定；
4. 通过现有 `project_artifact_service.resolve_scope/get_project_artifact` 读取；
5. 比对 artifact ID 和 content hash，漂移时拒绝；
6. 把内容发送回同一 `p2p_chat_id`，并回复在触发领取的消息线程中。

第一阶段的固定领取语法是平台结果消息中展示的
`领取 <opaque_artifact_claim>`。opaque claim 是随机、限时、单 grant 的引用，不
包含 Workspace/Project/Artifact 原始 ID；它只触发只读领取，不能映射为工具
调用或 proposal 批准。任意其他文字按 Agent 会话消息处理。

文本/Markdown 只读取 `ProjectArtifactDetail.content` 中服务端已知的 bounded
字符串 body；JSON 或超出消息安全大小的内容以 SDK 支持的内存 bytes 文件发送。
不接受 artifact payload 提供的本地路径或 URL，不让 SDK 代下载不受信资源。
消息必须保留 title、media type、content hash 和 `simulated` 标识。未支持的媒体
类型返回稳定 `artifact_media_unsupported` 状态，不返回内部路径或原始错误。

### 9. 撤销和权限重检

所有能力在每次处理前重检，不能仅相信创建 grant 时的快照：

- installation disabled/revoked：已验签事件可安全 ACK，但 receipt 标记
  `installation_disabled/revoked`，不执行 Agent 或出站；
- binding revoked、用户 disabled、成员关系删除：立即拒绝所有新处理；
- 用户失去 `analysis.export`：已有 artifact grant 和新的外部回复均暂停，防止
  降权后继续向外发送结果；
- reply/artifact grant revoked 或 expired：拒绝且不自动续期；
- conversation closed、context revision/hash 漂移或 Studio 对象归属失效：拒绝；
- 飞书目标撤销、限流、权限不足、超时和 SDK `not_connected`：映射为稳定状态，
  保留 receipt/delivery，按类别重试或进入 permanent/indeterminate；
- 安装撤销后保留验证材料到审计保留期结束，以便验签并安全 ACK 残余重推；停止
  provider 订阅后才能销毁材料。销毁后回调失败关闭并报告 installation unavailable。

在耗时 Agent 调用前重检一次；在把 Agent 结果或产物发送到飞书前再次重检，避免
处理中途撤销后继续外发。已经持久化的 proposal 不会因外发失败而执行。

## API 合同

以下管理 API 都走现有平台身份、Fleet Auth 和 Workspace RBAC：

```text
POST   /api/v1/workspaces/{workspace_id}/connector-installations
PATCH  /api/v1/workspaces/{workspace_id}/connector-installations/{id}
GET    /api/v1/workspaces/{workspace_id}/connector-installations/{id}/health
POST   /api/v1/workspaces/{workspace_id}/connector-installations/{id}/binding-challenges
DELETE /api/v1/workspaces/{workspace_id}/connector-bindings/{binding_id}
POST   /api/v1/workspaces/{workspace_id}/projects/{project_id}/connector-reply-grants
GET    /api/v1/workspaces/{workspace_id}/projects/{project_id}/connector-reply-grants/{id}
DELETE /api/v1/workspaces/{workspace_id}/projects/{project_id}/connector-reply-grants/{id}
POST   /api/v1/workspaces/{workspace_id}/projects/{project_id}/connector-reply-grants/{id}/artifact-grants
```

权限复用现有角色：安装管理和他人绑定撤销要求
`configuration.manage`；用户为自己创建绑定 challenge 要求 `workspace.read`；创建
或撤销自己的 reply/artifact grant 要求 `workspace.read` 和 `analysis.export`。
无需新增泛化的 “connector admin” 或 Studio member 权限。

回调 API 只返回 SDK 要求的 challenge/ACK 或通用失败，不返回 Workspace、用户、
会话、产物、receipt 状态或错误细节。状态查询只能通过上述受认证 API 完成。

交付顺序分两步。第一步实现 installation、binding、显式 reply grant、持久 receipt
和回原 Agent 会话；artifact-grant API 在可信 workflow 来源链落地前返回稳定的
`artifact_conversation_provenance_unavailable`。第二步只在来源链测试通过后开启产物
grant/领取。这个门禁不能用预览 seed 或 project/run 相同来替代。

## 必须新增与可复用

### 必须新增

- connector installation、binding challenge、principal binding、reply grant、
  artifact grant、inbound receipt、outbound delivery 模型及迁移；
- 飞书 SDK runtime、严格回调适配器、收件 service 和 connector worker；
- 精确 Fleet Auth callback 豁免和共享 router/lifespan 注册；
- Agent conversation 的内部 connector capability 入口；
- 管理 schema/API、稳定错误码、健康状态和隔离 mock 测试；
- `lark-channel-sdk[fastapi]==1.4.0` 依赖、锁文件和第三方告知。

产物领取还依赖一个由 workflow 运行链负责的先行变更：扩展
`WorkflowRunStartRequest` 和 `ArtifactProvenance`，在已授权的 workflow 启动 API
验证 conversation，并经持久 run request、native executor `run_context` 到
`IntelligenceStore._append_artifacts` 注入服务端 provenance。所有 producers 仍只
提供 source/evidence/time，不能自行声明 conversation。该变更应由运行链 owner
单独交付和验证；它不是 connector callback 对任意产物补来源的许可。

### 直接复用

- `backend.auth.crypto` 的凭据加密；
- `User`、`WorkspaceMembership`、`get_workspace_access`、
  `WorkspacePermission.READ`、`EXPORT_ANALYSIS`；
- `agent_conversation_service` 的 context validation、turn/历史/runner、错误脱敏和
  proposal 语义，以及 Agent turn request ID 唯一约束；
- `studio_agent_session_access` 已有 Studio object ownership 规则，但不复用其
  local/bootstrap 身份假设；
- `project_artifact_service` 的 scope、可信 conversation provenance 和 content
  size 限制；
- controlled receiver 的数据库 claim、nonce/hash、indeterminate/retry 测试思想，
  但不复用其私有 MAC header 作为飞书验签；
- `NotificationSendResult`/notification status 的可观测性模式，但不把旧
  `NotificationLog` 当作 connector 权限权威。

## 最小 ownership 扩展

Issue #125 原 owned paths 不足以实现上述边界。实现任务至少需要增加：

- `pyproject.toml`、`uv.lock`、第三方告知文件；
- `backend/models/connector_reply.py` 和一条 Alembic migration；
- `backend/security/fleet_auth.py` 的精确回调豁免；
- `backend/services/agent_conversation_service.py` 的内部 capability 入口；
- `backend/api/v1/__init__.py` 的 router 注册；
- `backend/main.py` 的 connector runtime lifespan；
- `backend/worker/tasks.py` 仅当协调者决定复用 Celery，而不是进程内数据库 lease
  worker；两者只能选择一种；
- 对应单元、集成和迁移约束测试。

在开放 artifact grant 前，ownership 还需要由 workflow 运行链 owner 明确扩展到
`backend/schemas/workflow.py`、workflow 启动/持久化入口、
`backend/workflow/native_intelligence_executor.py`、
`backend/workflow/native_intelligence_contracts.py` 和
`backend/workflow/intelligence_store.py`，仅用于上述服务端来源贯通。connector
实现不得自行绕过这项依赖或修改 producer payload 来伪造 conversation。

不要修改 `resolve_stored_agent_session_workspace` 以接受任意 connector/OIDC 身份，
不要给旧 `NotificationRule` 或 `DeliveryConnection` 补充隐式 Studio 权限，也不要
把回调凭据写入工作流图或前端。

## 验证矩阵

所有 SDK 和网络调用使用本地 callback fixture/SDK mock，不向真实群或用户发送。

| 场景 | 预期 |
| --- | --- |
| SDK challenge、有效加密签名 | 返回 SDK challenge/ACK，不建业务 turn |
| 缺签名、错签名、错 encrypt key/token | strict 模式拒绝，业务表无变化 |
| receipt commit 失败或超过 2 秒 | callback 返回 500；同一 message_id 重推后可再次提交 |
| receipt 已 commit，worker 尚未执行 | callback 返回 200；重启后 worker 从已存正文恢复 |
| handler 抛错后的同消息重推 | 不受 SDK seen/dedup 吞噬，由数据库唯一键决定 |
| 错 installation/app/tenant | 拒绝，不能回退到其他安装 |
| 未绑定 open_id、bot/system sender | 拒绝，无 Agent turn |
| 绑定 challenge 过期、重放、跨用户消费 | 拒绝；一次性约束保持 |
| 已绑定用户但无/已删除 Workspace membership | 拒绝并保留稳定状态 |
| Studio OIDC/外部身份直接访问 | 继续 403；只有有效 capability grant 可进入内部入口 |
| reply target 不属于 grant 或来自其他 chat | 拒绝，证明原会话隔离 |
| Project/workflow/run/conversation 任一跨 scope | 拒绝，无标题、状态或产物泄漏 |
| 同一 message_id 同一内容并发/重推/重启 | 一个 receipt、一个 turn、一个出站 operation |
| 同一 message_id 不同规范化内容到达应用 | 安全冲突，无第二 turn |
| Agent 返回 proposal | turn 状态 proposal；飞书无 approve/execute 能力 |
| Grant 在模型运行中撤销 | 可保存审计结果，但不向飞书外发 |
| Artifact grant 正确且 hash 未变 | 从现有 API service 读取并回同一单聊 |
| 普通 workflow run 未携带已验证 conversation | 产物保持无会话来源，禁止创建 artifact grant |
| workflow 启动传入跨用户/跨 scope conversation | 启动请求拒绝，不创建 run 或带来源产物 |
| 定时/外部 workflow run | 可继续运行，但产物无 conversation，不能通过 connector 领取 |
| Artifact ID、hash、project 或用户伪造 | 拒绝，不泄漏内容或存在性 |
| 失去 analysis.export | 回复/领取暂停，不继续外发 |
| SDK not_connected、429、权限不足、超时 | 状态明确；仅可重试类别重试，超时为 indeterminate |
| Worker 崩溃/lease 过期 | 启动恢复；数据库 claim 防重复 |
| Callback body 超限、日志扫描 | 413/通用失败；日志无正文、签名、token、secret |
| Fleet token 已开启 | 精确 callback 可由 SDK 独立认证；其他 connector API 仍需 token |

## 取舍与结果

- 选择官方 Channel SDK 而不是继续扩展自定义机器人 Webhook，因为只有应用机器人
  能提供受验证的发送者、会话和回复消息上下文。
- 选择单聊和“回复平台结果消息”约束，牺牲首版群聊便利，换取确定的用户、chat、
  会话和授权对应关系。
- 选择显式、可撤销 capability grant，而不是放宽 Studio bridge；因此 ADR 0045
  的普通访问行为完全不变。
- 选择先 durable ACK 再后台处理，以满足飞书 3 秒处理/重推合同；代价是用户需要
  看到 pending/failed 状态，而不是把 callback 200 当成 Agent 完成。
- 选择数据库作为幂等权威，SDK cache 只减载；增加了几张窄表，但能覆盖并发、
  重启、冲突、撤销和审计。
- 选择固定版本的同步 `EventDispatcherHandler` 作为入站适配层，因为该 SDK 的
  高层 `handle_webhook_request` 在 commit 前 ACK；代价是升级时必须复核随包模块
  路径和同步异常语义。
- 产物由机器人在已授权会话内交付，不创建公开下载链接或长期 bearer token。

## P2 接入边界补充决策

P1 严格入站与配置界面已通过独立验收。P2 的[正式执行合同](https://github.com/2233admin/opencli-Razormind/issues/125#issuecomment-5555789755)经原 reviewer 复审接受，实施尚在进行。本节补充并收紧前文的恢复和交付边界，不表述为功能已上线。

- grant 创建使用当前创建者和精确范围内的 request ID、规范化请求 hash 及数据库唯一约束；并发失败者 rollback 后重读胜者。每个会话只允许一个有效 reply grant，重复请求不生成多条激活消息或重复产物 offer。
- 复用已有会话 turn 与 runner。普通 send 行为保持；connector 的模型前 running turn 可先持久化，最终 response/status、conversation revision 与本 grant cursor 在重新验证的同一短事务中提交。模型等待不持有会话锁；无法证明最终提交的过期 running turn 进入不确定状态，不自动重跑模型。
- receipt 与 grant lease 明确 owner、generation 和过期时间。接管原子递增 generation，续租、写回和释放均以同一 owner/generation 校验，迟到 worker 不能覆盖新状态。
- 领取命令的 receipt 仅保存固定占位和已验证服务端关联；有效、过期、无效和格式错误的领取命令均不得留下 claim 明文。claim 密文复用现有单 Fernet key；失钥使对应 artifact grant/delivery 原子失败，恢复 key 不复活旧授权。
- SDK 的 connect、send 与 disconnect 各有独立超时和清理规则。持久化 sending 后的未知结果不自动重发。飞书[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create.md)和[回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply.md)仅提供 UUID 一小时内的降重保证，因此固定 UUID 不能证明任意重启后的恰好一次发送。
- 只读和撤销接口面向当前授权用户、精确父 grant/会话/项目，供前端刷新后恢复实际投递状态；不返回 claim、provider 身份或产物正文。前端通过既有受授权会话读取获得 governed Workspace，不能用 Studio artifact.workspace_id 猜测权限映射。
- 显式 `connector_reply_enabled` 和 `connector_artifact_delivery_enabled` 默认 false，并与实际 worker/outbound 生命周期共同决定 readiness。CI 测试结果不作为运行时状态，安装或绑定本身也不代表具备投递能力。

## 官方来源

- 飞书开放平台，[Python SDK 处理事件](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/handle-events)：HTTP/WebSocket 事件方式、3 秒处理与重推、`EventDispatcherHandler` 加密参数。
- 飞书开放平台，[接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)：`im.message.receive_v1` 权限、sender/tenant/chat/thread 字段及按 `message_id` 去重要求。
- 飞书开放平台，[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)：应用机器人发送权限、返回 message ID 与消息限制。
- Lark Technologies，[Channel SDK v1.4.0 Webhook adapter](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/docs/webhook-server.md)：`handle_webhook_request`、FastAPI 生命周期、验签/解密与 HTTP 层职责。
- Lark Technologies，[Channel SDK v1.4.0 reference](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/docs/reference.md)：`InboundMessage`、`SendResult`、reply target、UUID 幂等和 error kinds。
- Lark Technologies，[Channel SDK v1.4.0 security](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/docs/security.md)：strict 模式、签名前解密限制及 compatibility 开关。
- Lark Technologies，[Channel SDK v1.4.0 dedup architecture](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/docs/dedup-architecture.md)：两层 SDK 去重及多 worker 非原子限制。
- Lark Technologies，[Channel SDK v1.4.0 `channel.py`](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/lark_channel/channel/channel.py)：`handle_webhook_request` 只等待同步 dispatcher、message processor 通过 `schedule` 投递后台 coroutine 的实际时序。
- Lark Technologies，[Channel SDK v1.4.0 dispatcher](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/lark_channel/event/dispatcher_handler.py)：验签、解密、token/challenge 处理、同步 callback 异常映射为 500 的固定实现。
- Lark Technologies，[Channel SDK v1.4.0 normalize/safety pipelines](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/lark_channel/channel/normalize/pipeline.py) 与 [safety pipeline](https://github.com/larksuite/channel-sdk-python/blob/v1.4.0/lark_channel/channel/safety/pipeline.py)：handler 前 `check_and_mark`、异常后 seen 标记及 `safe_content_text` 的标准库转义语义。
- PyPI，[lark-channel-sdk 1.4.0](https://pypi.org/project/lark-channel-sdk/1.4.0/) 与 [官方仓库许可证/告知](https://github.com/larksuite/channel-sdk-python/tree/v1.4.0)：Python/FastAPI 兼容范围及 `MIT AND BSD-3-Clause` 发行许可。
