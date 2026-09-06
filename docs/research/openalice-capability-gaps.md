# OpenAlice 可复用能力缺口核对

日期：2026-09-06。依据为[平台 PRD](../opencli-agent-data-operations-platform-PRD.md)、当前本地实现和 OpenAlice 固定源码快照 `52b51f29809178594b7b57bf666133829368b7b4`。这是源码盘点，不是新功能验收，也不代表已经检查上游后续全部提交。继续归属现有 [#116](https://github.com/2233admin/opencli-Razormind/issues/116)。

## 为什么此前拿得少

此前将范围收缩为保留壳层、借鉴小组件与来源导航，主要交付了设计适配，未执行完整的逐模块复用盘点。适配记录明确没有复制 OpenAlice 源码。技术栈、对象和许可存在真实差异，但这些差异应决定每个模块的接入方式，不能自动成为只做少量 UI 的理由。

OpenAlice 采用 AGPL-3.0，当前仓库采用 Apache-2.0；直接移植必须记录具体来源和对应许可，不能默认视为 Apache 源码。其第三方依赖和上游项目应分别核对。例如报告实现使用 DOMPurify、Marked、highlight.js，可优先评估原始库；不必自己再写 Markdown 解析、语法高亮或 HTML 清洗。许可差异本身也不等于禁止学习产品流程。

## 当前实现与验收边界（2026-09-06 用户纠正后复核）

用户再次指出参考项目的功能没有完成对齐。父目标仍是 PRD 规定的完整产品旅程，不能收缩成“单题采集成功”或“只剩配置模型”。下表核对当前整合提交 `4556c9df` 的代码和已有验收证据；不代表重新运行了历史测试。详细证据见[整合验收记录](../verification/openalice-adoption-integration.md)和[产品旅程记录](../verification/openalice-product-journey.md)。

| 能力 | 当前已有证据 | 尚未证明或仍缺失 |
|---|---|---|
| Alice 主对话与工具 | 实际 8047 页面已显示共享主对话、16 项服务器对话工具；工作流另有 28 项能力。 | 当前会话没有真实 LLM 回合；自然语言创建、修改、确认、执行并继续同一项目的旅程未验收。 |
| 文件与产物 | 项目产物面板、正文阅读器、来源和原会话入口已接入。 | 项目数据页的通用文件上传仍禁用，来源/批次列表不能算通用文件浏览与上传能力。 |
| 报告阅读与追问 | 已有 Markdown、隔离 HTML、代码、JSON、CSV/TSV 阅读器；确定性模型边界下的 HTTP/持久存储旅程验证了追问写回。 | 真实豆包单题仅生成普通 Record，IntelligenceArtifact 列表为空；没有真实模型读取该报告并继续工作的证据。 |
| 项目与运行归属 | Records 支持精确 Run 筛选；产物 API 校验项目、工作流、Run 范围，隔离旅程及生产来源专属测试已有记录。 | 不能把确定性夹具的产物和会话关联当作本次真实采集生成独立报告的证据。 |
| 工作标签与会话 | 标签按身份/Workspace 保存并可刷新恢复；项目会话库具备列表、搜索和关闭/恢复入口。 | 导航恢复不等于后台进程或所有 Runtime 的原 Run 恢复。 |
| 工作状态与配置 | 已接入 `agent-work-health.tsx` 和既有服务端健康投影，展示持久会话、Run、Automation 的状态、原因及授权操作。 | 不能继续声称“统一健康投影完全没有”；仍需按参考操作逐项验收真实阻塞原因和就地修复旅程。 |
| 豆包原现场恢复 | 用户手动验证后，实际 UI 显式恢复同一 Run/Job；完成 57 条事件、6 个节点、一条记录，前后保持同一页面和唯一一问一答。 | 此合同只证明已实现的豆包恢复路径，不能外推为所有 Runtime、任务类型或批量采集都可恢复。已完成 Run 不得为补证重复提问。 |
| 外部渠道 | 已有通知、Webhook、交付及 Connector 实现与 SDK/隔离验收记录。 | 真实外部平台双向回复、领取产物的产品验收未完成；不能将模拟或隔离验证算作真实平台交付。 |

后续对齐继续遵守 PRD 已确定的 Project 权威对象、Linear 风格全局壳层与项目导航、Agent/GUI 共享持久状态、受控副作用和人工恢复规则。这里记录事实与证据缺口，不新增实现方案；新的验收决策正在按用户指定的 grilling 流程澄清。

## 初次盘点的接入方向（历史基线）

以下表格保留初次调查时的实现状态与来源定位，部分缺口已在上述整合中补齐；不得将本表单独用作当前缺失功能清单。

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
