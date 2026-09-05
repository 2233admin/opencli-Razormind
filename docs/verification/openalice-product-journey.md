# OpenAlice 适配整合验收

## 2026-09-06 增量：可恢复工作标签

按平台 PRD 继续复用 OpenAlice 操作语义，新增全局工作标签，覆盖已有项目、编排、运行、数据和证据页面；没有复制上游源码或增加依赖。
标签按登录身份与 Workspace 隔离，保留来源标识，刷新恢复、重复打开聚焦、关闭当前项转向相邻项，关闭不修改服务端对象。
当前 URL 是活动视图的权威来源；恢复历史不会自动开始运行。未知路由和非白名单参数不会被恢复。

独立 Sol High 审查发现并修复两个生命周期问题：认证完成前消费 Agent 深链接；关闭活动标签使用 push 导致立即后退又重开已关闭项。分别增加延迟认证和关闭后后退的浏览器断言。

本轮新鲜验证：

- `node --test scripts/check-work-tabs.mjs scripts/check-run-navigation-behavior.mjs`：8/8。
- 8030 开发前端上，`openalice-work-tabs`、`openalice-agent-continuity`、`openalice-inbox-journey`：5/5；最后关闭历史修正后单独重跑工作标签：2/2。
- `tsc --noEmit`、本轮三个 TS/TSX 文件的 ESLint 通过。
- 浏览器 API 使用隔离夹具；没有验证真实模型执行、文件上传或外部投递。

本轮仅新增 `work-tabs.ts`、`work-tabs.tsx`、对应 Node/browser 测试，并在已有 `app-shell.tsx` 上接入和修复认证时序。开始前目录已有大量未提交改动，未重置或提交它们。
复用出处及剩余文件查看器、按运行筛选结果等真实缺口见[复用记录](../research/openalice-ui-adoption.md)。#116 的全量目标仍未完成，不能用本轮标签验收替代。

> 用户复核纠正（2026-09-05）：本文仅证明第一批基础接线和局部适配通过，不能作为“OpenAlice 整套操作体验已复用”的验收。总任务 #116 重新打开。用户要求的可恢复工作标签、项目内会话/运行库、结果与产物并览、消息详情直接追问等尚未交付；现有测试没有覆盖这些缺失能力。

日期：2026-09-05。任务：[总交付 #116](https://github.com/2233admin/opencli-Razormind/issues/116)，实施拆为 #117 后端、#118 创建与会话、#119 Inbox 与结果。

## 交付范围

三个实施 worktree 分别提交，根任务在 `codex/openalice-ux-integration-20260905` 合并。基线 `bb014c79` 保存原有 UX 工作；没有切换、重置或暂存并行账户任务所在的主目录。主目录同步前逐文件比较基线与上一次已同步的哈希，发现其他修改会中止同步。

- 共享空白、模板及需求创建的提交逻辑，持久保存项目和主工作流草稿。
- Agent 读取项目、工作流与草稿，创建及修改通过现有提案、授权和修订检查。
- Agent 工具图结构直接使用权威 WorkflowProject schema；模型生成的结构错误在提案落库前被拦截，仍允许配置未补齐的合法草稿。
- 确认结果携带真实 ID；确认与会话 turn 完成在同一事务内保存，刷新后不再恢复为待确认提案。
- 顶部会话保留确认后的项目上下文；确切旧会话链接会验证工作区和请求范围。
- Inbox 区分普通审批与 Agent 提案，后者返回原会话确认；已完成提案进入项目动态。
- 来源导航保留项目、工作流及运行；没有原会话来源时不虚构会话链接。
- 窄屏创建页面保留输入，初次显示需求区域；画布显现时重新适配视口，自动布局的节点改为纵向，手工位置保留。
- Tailwind 仅扫描 app/components/features/hooks/lib，避免并行 Next 构建目录的二进制缓存被误识别为 CSS 类名。配置采用 [Tailwind 官方显式来源机制](https://tailwindcss.com/docs/detecting-classes-in-source-files#disabling-automatic-detection)。

OpenAlice 固定参考版本、适配文件及语义见[复用记录](../research/openalice-ui-adoption.md)。未增加前端依赖，保留 Next、Base UI、现有动效时长及发布模型。

## 可重复验证

前端命令从 `frontend/` 运行。独立 worktree 通过依赖目录 junction 复用安装；因此直接调用 Node CLI。隔离测试前端使用 8040，真实开发前端 8030 保持 Turbopack。

```powershell
$env:PLAYWRIGHT_SMOKE_PORT='8040'
$env:PLAYWRIGHT_REUSE_EXISTING_SERVER='1'
node node_modules/@playwright/test/cli.js test e2e/agent-conversation-session.spec.mjs e2e/openalice-agent-continuity.spec.mjs e2e/openalice-inbox-journey.spec.mjs e2e/openalice-project-creation.spec.mjs --workers=1
node node_modules/@playwright/test/cli.js test e2e/route-motion.spec.mjs e2e/smoothui-feedback.spec.mjs --workers=1
node node_modules/typescript/bin/tsc --noEmit
node --test scripts/check-inbox-origin-navigation.mjs scripts/check-inbox-regressions.mjs scripts/check-run-navigation-behavior.mjs scripts/check-agent-dock-regressions.mjs
```

结果：产品路径 **9/9**、原有路由和异步反馈 **8/8**、定向 Node 检查 **12/12**、TypeScript 与变更文件 ESLint 通过。最后在主目录 8030 重跑创建、续聊和 Inbox **5/5**，并重跑路由与反馈 **8/8**，验证实际 Turbopack 与新样式扫描配置。

浏览器测试使用隔离 API 夹具，断言页面错误、请求路径及状态；创建失败保留名称，确认后下一条消息使用服务端绑定，未调用错误的审批接口。移动端额外检查节点宽度及左右边界、隐藏 minimap、保留输入，并留截图供人工检查。

后端从仓库根运行：

```powershell
python -m pytest tests/integration/test_agent_project_control.py tests/integration/test_openalice_product_journey.py tests/integration/test_studio_bootstrap_api.py tests/unit/test_agent_conversation_api.py tests/unit/control/test_agent_control.py --no-cov -q
```

根任务最终整合检查 **19/19**；后端工作树更广的权限、Studio、Chat 回归 **81/81**。HTTP journey 使用真实内存数据库、REST 路由、提案确认、Studio 和 Inbox，只替换模型选择工具的回复。Ruff 与变更核心模块的隔离 mypy 通过。全依赖 mypy 仍有既有错误；没有把它报告为通过。目标 pytest 使用 `--no-cov`，避免把定向测试集的全仓覆盖率阈值失败误报为功能失败。

## 审查与返工

Sol 处理后端治理；Terra 分别处理创建/会话和 Inbox。根任务负责整合、浏览器测试、主目录同步和文档。实现者交叉审查自己未编写的模块，根任务另审创建/会话及最终业务路径。

首轮审查和浏览器截图发现并修复：跨范围会话恢复、旧异步回复进入新范围、工作区切换保留旧草稿、通知过滤 change_proposal、已确认提案刷新复活、续聊覆盖已创建项目绑定、移动端隐藏画布初始化失真。部分最初浏览器夹具错误也已修正，最终结果以成功重跑为准。

## 本地运行交付

本轮差异已与主目录逐文件核对后同步，业务数据库未作为测试数据修改。8031 开发 API 更新六个 Agent/Studio 模块，原文件备份保存在本地 `.git/openalice-runtime-update/before/`；最后的结构校验更新另存于 `.git/openalice-runtime-update-final/before/`。旧运行容器中独立的 Workflow Run 路由保持原样，只替换本轮草稿保存函数与依赖。

API 导入检查通过，重启后 8031 与前端同源 `/health` 均返回 200，五个新增 Agent 工具已在运行进程加载。容器本轮使用开发热更新，重新创建旧镜像会丢失该热更新；源码在整合分支中，应从整合源码构建后续镜像。

完整使用方法见[从目标创建项目并继续处理结果](../usage/agent-project-workflow.md)。本轮不把夹具验收称为真实模型、真实网站采集、定时调度或外部投递的生产验收。
