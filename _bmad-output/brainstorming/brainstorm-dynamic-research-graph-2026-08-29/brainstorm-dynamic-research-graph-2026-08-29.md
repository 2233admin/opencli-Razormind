# Dynamic Research Graph Open Space 纪要

## 事实底图
- 权威运行状态：`backend/models/workflow_run.py::WorkflowRun` 与 `WorkflowRunEvent`；事件追加在 `backend/workflow/workflow_run_events.py::append_workflow_run_events`。
- 事件契约：`backend/schemas/workflow.py:691-702`；投影：`WorkflowRunProjection:851-862`、节点状态 `WorkflowRunNodeState:825-848`。
- 研究算子：`backend/workflow/research_operators.py:19-86` 的六个确定性、证据关联算子。
- 前端取数：`frontend/lib/workflow/backend-runs.ts`；展示：`frontend/components/flow/run-trace-panel.tsx`；画布转换：`frontend/lib/workflow/to-react-flow.ts`。
- 外部许可事实：STORM、DeepSeek Harness、dsh-mission 官方仓库标 MIT；dsh-mindmap README 声称 MIT，但仓库元数据显示 Other，须以 LICENSE 原文/作者确认作为 gate；mindmap bundle 内嵌 MindElixir，需保留其版权/许可。

## 能力归属矩阵
|能力|现有归属|ResearchGraph采用边界|
|---|---|---|
|检索与问题生成|外部 STORM 机制；现有 source adapters|Borrow 机制，Own 事件契约|
|研究算子|`research_operators.py`|Own，输出可审计事件|
|运行与重放|WorkflowRun/Event spine|Own 权威源|
|证据批次与血缘|workflow evidence projection/native research|Adapt 为图节点来源|
|图谱产物|Native `GraphArtifact`|Adapt 成事件折叠读模型|
|协作画布|React Flow + project UI|Adapt 只读/提案双模式|
|长期任务|dsh-mission 的 verify/lease/DAG|Borrow 机制，隔离实现|

## Borrow / Adapt / Own / Reject
- **Borrow**：STORM 的 perspective-guided questions、模拟对话、检索模块化；mission 的 report≠verify、CAS、lease、replan；mindmap 的 projection+RPC 双向同步。
- **Adapt**：将 claim/evidence/反证/coverage 映射到现有 node event；将 GraphArtifact 变成版本化 projection；将许可证清单纳入 compile/release gate。
- **Own**：事件类型扩展策略、ResearchGraph schema、权限与证据可见性、跨 run lineage、画布查询 API。
- **Reject**：直接复制外部 bundle/核心实现、把模型输出视作权威、建立绕过 WorkflowRun 的第二权威数据库。

## 领域语言与事件
实体：ResearchRun、Revision、Claim、Evidence、Source、Question、Relation、Proposal、Verification、Projection、Retraction。
事件（目标候选）：`research/created`、`question/proposed`、`evidence/batch-ready`、`claim/projected`、`relation/proposed`、`relation/verified`、`coverage/assessed`、`revision/branched`、`source/retracted`、`projection/rebuilt`、`publication/gated`。现有 WorkflowRun 事件仍是运行层事实，图语义事件需保持可关联的 run/trace/node IDs。

## 目标架构（事实基础上的收敛方向）
事件脊柱作为唯一写入事实；研究事件折叠出 ResearchGraph read model。Workflow projection 负责运行节点状态，ResearchGraph projection 负责 claim/evidence/relation/version 查询；二者以 run、trace、revision、lineage 关联。前端画布默认读取 project+图读模型，模型/人工修改先形成 proposal，经验证事件后进入权威投影。

## 采用原则
1. 先定义可回放事件与幂等键，再定义 UI。
2. 结论必须可追溯到证据批次与来源版本。
3. 模型建议、人工提案、环境验证三者分层。
4. 失效采用撤回/降权事件，不物理删除。
5. 大图按局部子图查询与增量投影。
6. 外部项目只经公开 API/协议接入，clean-room 优先。
7. 每个可复制文件锁定 commit 并建立 SPDX SBOM。

## 许可证 gates
- 复制 MIT 代码：保留原 LICENSE、版权/许可 headers，分发物附 notice，并标注修改。
- dsh-mindmap：先解决仓库 metadata Other 与 README MIT 冲突；内嵌 MindElixir 版权 notice 不得遗漏。
- DeepSeek Harness：README 声明 THIRD_PARTY_NOTICES，但当前 main URL 取证为 404；发布前重新核验并逐项清点依赖。
- 所有依赖（直接/传递）不得继承上游 MIT 推断；需逐项核许可证与兼容性。
- 更安全路径：行为契约记录→未阅读实现者独立实现→仅接入公开接口；若直接采用则保留完整 notice 与 provenance。

## 分阶段路线
- P0：冻结术语、事件 envelope、来源清单、许可证 gate 与回放不变量。
- P1：把 claim/evidence/coverage 输出接入事件并生成最小图读模型；提供按 run/revision 查询。
- P2：局部子图画布、事件时间轴、证据路径、提案/验证交互。
- P3：跨 run lineage、过期证据、冲突/撤回、权限过滤与增量投影。
- P4：接入长期 mission 编排与多端协作；以兼容性和 SBOM 作为发布门槛。

## 风险 / 开放问题
- 图语义事件与现有 node event 的边界及版本迁移。
- GraphArtifact 当前是否已持久化、如何与 WorkflowRun 统一 idempotency。
- 大规模图的查询、分页、权限过滤性能。
- 来源撤回、证据敏感字段与导出合规。
- 外部依赖许可证、bundle 生成物和维护活跃度的持续核验。
- 多人同时提案的冲突判定与人工仲裁责任。

## 首批决策
1. WorkflowRun/Event spine 保持唯一权威写入源。
2. ResearchGraph 首先作为可重建 read model，不另建不可回放权威存储。
3. 首批只实现 evidence→claim→relation 的最小闭环与 verification gate。
4. 外部项目只借鉴机制；任何源码复制必须经过许可证与 notice gate。
5. 前端先做投影阅读与提案，不把画布直接当权威编辑数据库。

_本纪要由 active memlog 的 100 条 ideas、technique 记录、decision 与 insight 收敛而来。_
