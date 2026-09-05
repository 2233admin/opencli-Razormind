# OpenAlice 可复用能力缺口核对

日期：2026-09-06。依据为[平台 PRD](../opencli-agent-data-operations-platform-PRD.md)、当前本地实现和 OpenAlice 固定源码快照 `52b51f29809178594b7b57bf666133829368b7b4`。这是源码盘点，不是新功能验收，也不代表已经检查上游后续全部提交。继续归属现有 [#116](https://github.com/2233admin/opencli-Razormind/issues/116)。

## 为什么此前拿得少

此前将范围收缩为保留壳层、借鉴小组件与来源导航，主要交付了设计适配，未执行完整的逐模块复用盘点。适配记录明确没有复制 OpenAlice 源码。技术栈、对象和许可存在真实差异，但这些差异应决定每个模块的接入方式，不能自动成为只做少量 UI 的理由。

OpenAlice 采用 AGPL-3.0，当前仓库采用 Apache-2.0；直接移植必须记录具体来源和对应许可，不能默认视为 Apache 源码。其第三方依赖和上游项目应分别核对。例如报告实现使用 DOMPurify、Marked、highlight.js，可优先评估原始库；不必自己再写 Markdown 解析、语法高亮或 HTML 清洗。许可差异本身也不等于禁止学习产品流程。

## 优先复用的用户能力

| 能力 | 当前实现与缺口 | 上游具体参考 | 接入方向 |
|---|---|---|---|
| 项目文件及产物浏览 | `frontend/app/(app)/studio/projects/[projectId]/data/page.tsx:709` 的 `ProjectInputsView` 是来源分组，上传按钮禁用。已有情报 artifact 模型，但没有形成此页面的通用文件查看链路。 | `ui/src/components/workspace/FilesPanel.tsx`、`ui/src/pages/FileViewerPage.tsx` | 接入现有 Project/Run/Artifact 授权对象，提供列表、读取、不可用状态和返回产出会话；不另建目录作为权限权威。 |
| Markdown/HTML 报告与代码展示 | `frontend/components/inbox/inbox-conversation-thread.tsx:85` 将回复作为文本段落显示；未获得上游统一报告阅读器的能力。 | `ui/src/components/FileContentView.tsx`、`MarkdownContent.tsx`、`HtmlReportView.tsx` | 优先采用现成解析和清洗库，统一报告组件；沿用隔离 HTML 展示、禁止脚本和网络加载的边界。 |
| 报告与原会话并览、继续追问 | 当前 Inbox 已有原会话追问，项目已有会话库；这些不能再列为全无。缺少正文/文件与会话共同呈现的完整工作面。 | `ui/src/pages/InboxPage.tsx` 的文档区和 inquiry；`WorkspaceView.tsx` 的会话/文件组合 | 复用已有会话 API 与提案流程，补共享产物面板和来源关联。 |
| 精确结果归属 | `run-context-banner.tsx` 明示数据按项目展示；数据页 `useRecords` 仅带项目，`backend/api/v1/records.py:32` 的列表参数也没有运行过滤。 | `src/core/inbox-store.ts` 的 origin、`src/tool/inbox-push.ts` 的内容指纹与来源 | 将引用真正用于授权查询和结果筛选，保留运行、会话和内容版本；不能只在 URL 上带 run。 |
| 可恢复的 Agent 工作状态 | `project-agent-workspace.tsx` 当前为服务端会话列表并打开 Dock；会话模型有 active/closed。原生运行时适配已经存在，缺口是统一呈现及恢复操作。 | `ui/src/components/workspace/ResumeCta.tsx`、`src/workspaces/public-session.ts`、`product-session-coordinator.ts` | 在既有运行时支持范围内显示运行、暂停、需处理、结束及可用操作；不得把简单再次发消息称为原生进程恢复。 |
| 配置就绪与就地修复 | `backend/agent_runtimes/base.py:30` 已有 RuntimeReadiness；不能声称我们完全没有探测。需要继续对齐用户可理解的原因和配置入口。 | `src/workspaces/agent-runtime-readiness.ts`、`agent-credential-readiness.ts`、`ui/src/components/HarnessSetupPage.tsx` | 学习安装、认证、模型缺失、超时及修复目标的分类，汇合到 PRD 的 Setup Center；避免另写健康检查系统。 |
| 自动化健康与阻塞解释 | 已有 `backend/api/v1/automations.py:147` 手动触发和持久调度，不能说没有自动化；尚需对齐面向工作的统一健康投影。 | `src/workspaces/issues/automation-health.ts` 将工作配置、运行、负责人会话和 runtime blocker 合成健康状态 | 从现有 Automation、Run、Readiness 推导为何未开始、到期、受阻或中断，并提供同一工作的下一步操作；不替换现有去重调度器。 |
| 外部渠道双向交互 | 已有通知分发、Webhook 和飞书表格交付；不能说没有渠道能力。未核实到与上游相当的渠道回复原会话、按需领取产物的一体化产品链。 | `services/connector/src/core/adapter.ts:19` 的 owner chat、artifact delivery、start/stop/health；`packages/connector-protocol/src/types.ts` | 按渠道插件接入已有 Agent Control 与授权规则，让结果接收者回复并继续处理；不把外部消息直接执行为命令。 |

以上前三项可共用一个产物读取与展示模块，属于同一条操作链。文件/产物授权读取接口是必须接上的实际工作，不是搁置复用的理由。

## 不应重复建设的已有基础

- 已有 Codex、Claude Code、Pi 等运行时适配：`backend/agent_runtimes/`。
- 已有调度、自动化和恢复：`backend/scheduler.py`、`automation_schedule.py`、`services/scheduled_run_recovery.py`。
- 已有模板、项目 bootstrap、草稿修订和 Agent 提案：`frontend/lib/workflow/studio-templates.ts`、`backend/services/agent_project_service.py`、`backend/api/v1/chat.py`。
- 已有情报产物与引用：`backend/models/intelligence.py`；应先检查是否可扩展为共享能力。
- 已有统一运行事件中的 artifact/evidence：`backend/agent_runtimes/base.py`。

上游金融数据层 `packages/opentypebb` 可继续按采集/分析插件评估；其 package.json 明确为内部私有包和 AGPL-3.0，不能当作已经能直接 npm 安装的通用包。证券交易界面、券商执行语义、桌面 PTY 壳层和模板自动订阅升级不能仅因上游存在就默认进入当前 PRD；其中模板在 PRD 中明确是一次性蓝图。

核对分工：Luna Explore 检查后端；主代理检查文件/报告、现有 UI、PRD 与来源，并复核后端结论。未采纳“完全缺少 Inbox 追问”和“必须搬模板自动升级”两个初始判断：前者与当前实现不符，后者与 PRD 的一次性模板约定不符。本轮仅写研究记录，无应用代码变更或运行验收声明。

## 来源

- [OpenAlice 固定快照](https://github.com/TraderAlice/OpenAlice/tree/52b51f29809178594b7b57bf666133829368b7b4)
- [上游报告阅读器](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/FileContentView.tsx)
- [上游 HTML 隔离实现](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/HtmlReportView.tsx)
- [上游第三方声明](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/THIRD_PARTY_NOTICES.md)
- [已有适配与来源记录](openalice-ui-adoption.md)
