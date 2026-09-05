# 0045：桥接 Studio Workspace 与持久 Agent 会话上下文

- 状态：Accepted
- 日期：2026-09-06

## 背景

Studio 使用独立的 `studio_workspaces` 表，而持久 Agent 会话通过外键和
Workspace RBAC 存储在 governed `workspaces` 中。项目页需要按 Studio 项目
恢复会话；如果直接把 Studio workspace 当成 governed workspace，会绕过成员
授权；如果只按 governed workspace 全量查询，又会把不同项目的会话混在一起。

## 决策

Agent 会话请求保留 governed Workspace 的存储和 RBAC 边界。仅当请求来自本地
或 bootstrap 平台管理员、带有明确的 project/workflow/run 锚点，并且对象确实
属于请求的 Studio Workspace 时，服务端才把请求桥接到该身份唯一可访问的
governed Workspace。会话 `context_binding` 写入不可变的
`studio_workspace_id`；列表接口按项目、工作流和运行锚点过滤；读取、发送和
关闭会话都从已存储的标记重新授权；普通 governed Workspace 列表排除带该标记
的会话，避免会话元数据泄露。Studio 会话不能切换到另一个项目，也不能
用 source_id 伪造归属。无锚点和 OIDC 管理员请求继续拒绝。

前端项目工作区把项目筛选下沉到 `/chat/sessions` 查询参数，并将会话恢复、
新建和持久工作标签统一指向全局 Agent Dock。Inbox 追问复用同一会话和来源
上下文，不创建第二条不可追溯的对话。

## 结果

- Studio 项目可以使用持久 Agent 会话，同时保留 governed Workspace 的外键和
  权限模型。
- 列表、详情和追问不会跨项目串线；现有 governed Workspace 调用保持原行为。
- Studio Workspace 的访问仍依赖本地/受信管理员语义，未来若引入 Studio 成员
  模型，应替换桥接条件而不是放宽到任意已登录用户。
