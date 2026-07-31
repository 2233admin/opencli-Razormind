# 功能接线欠债账本 (designed-but-unwired) — 2026-07-19

两路只读审计 (代码层孤儿 + ADR 落地) 综合。主题: **建好了却没接通 / 空转的功能**。区别于采集 bug (C1-C25) 和工程化 gap (E1-E7)。

核心洞察: 债分三种, 别混。**接线欠债** (做了差一根线, 高 ROI) vs **愿景欠债** (accepted ADR 根本没做, 大工程) vs **文档 stale** (代码跑前面, 文档落后)。用户痛点是第一种。

主导模式 (审计 1): 前端 client `frontend/lib/api/endpoints.ts` **建在 UI 之前 —— 89 个 wrapper 里 46 个从没被任何页面 import**。后端+client 就绪, 差最后一公里 UI。

---

## A. 后端接线欠债 (我们能立即修, 高 ROI)

| ID | 建好的 | 缺的线 | 修法 | 证据 |
|---|---|---|---|---|
| **W1** | control 层 SCHEMA_DRIFT 全链 (error_kinds→evaluator→policies→actuator), `cycle_task` 每 60s **活跑** | 4 个 parse 失败点 3 个没传 `error_type` → recorder `elif error_type is not None` guard 跳过 → 永不 schema_drift。仅 crawl4ai 对 | rss_channel collect()+fetch() (bozo 分支) + cli_channel:106 三处加 `error_type=type(exc).__name__` (或 effective_error_type)。**一根线点亮已建好每 60s 空转的安全链 = 全账本最高 ROI** | rss_channel.py 两 bozo 点无 error_type; cli_channel.py:106-107; crawl4ai_channel.py:139-142 (正确对照); control/recorder.py guard |
| **W2** | `NotificationRule.trigger_event` 自由 str 字段 | dispatch 只用默认 `"on_new_record"` 调, 任何别的值的规则**静默永久失效** (无 producer, 同 SCHEMA_DRIFT 形状) | 收紧 Literal["on_new_record"] (或补其他 trigger 的 dispatch 调用点) | models/notification.py:20; notifier_dispatch.py:95 单值 |

## B. 前端接线欠债 (归 codex/5080 前端线; 后端+client 已就绪, 纯前端活)

| ID | 建好的 (后端+client) | 缺的线 (前端) | ROI |
|---|---|---|---|
| **W3** | control-plane 全 API + client wrapper: kill-switch (读写), advisory-report ("翻 automatic 的证据面"), odp-state | `control/` 目录只有只读 actions 表; kill/advisory/odp **零 UI** | **最高安全回报** — 一个下午 3 个面板读已建端点 |
| **W4** | skill correction 人审闭环: propose (已接 skill_channel 实跑) → getSkill/dismissCorrection/rollbackSkill (client 就绪) | skills/ 只有列表页, 无详情/dismiss/rollback 按钮; 文档说的 AgentDock 前端根本不存在 | 高 — 最后一公里 |
| **W5** | 46 个 CRUD/admin wrapper (createAgent/deletePlan/createSchedule/storeSourceCredential/batchDeleteRecords/addChromeInstance/updateSystemConfig...) | 各资源只接了 list GET, create/edit/delete 表单按钮从没建 | 中 — 逐资源 triage: 接按钮 or 删 wrapper (若已弃) |

> 待验证 (审计 1 未定论): tasks/workflows 的 SSE `/events/stream` 端点 —— apiClient 扫描测不到 EventSource, 先 grep `new EventSource` 再判死活, 别误杀。

## C. 愿景欠债 (accepted ADR 根本没做, 大工程 — 规划非接线)

ADR 0010-0025 (企业工作流平台愿景) 大片 UNIMPLEMENTED, 这些是**没开工**不是"做了没用":
- ADR-0012 Agent Control API (AgentOperationProposal 模型不存在; mcp_server 仍 184 行 pre-decision 基线) — **阻塞 0022/0025**
- ADR-0020 Project + durable draft (无 Project/ProjectDraft model, 现以 Plan/Source 为顶层)
- ADR-0022 Ephemeral Execution Grants (无 Connection/Binding/Grant 链, 现是扁平 per-source 凭证)
- ADR-0021 Delivery 业务结果 vs 提交 (无 BusinessOutcome, webhook_delivery 只到传输层, 200 ≠ 业务成功)
- ADR-0024 Recovery Cases 统一 inbox (无 RecoveryCase model; /inbox 页存在但活查三端点, 没串成案子) — **半接线, 比其他愿景更接近**
- ADR-0014/0015/0019 plugin 系统 (DataFoundry/声明式 plugin UI/locked node def 均无)
- ADR-0016 Setup Center (无 readiness 聚合面, 相对便宜)
- ADR-0023 幂等 Operation ID (仅 acquisition 有 idempotency_key, 未泛化成跨 plugin 契约)
- ADR-0025 乐观锁 guard (模式已在 source_cursor 证明, 但目标对象 Project/Proposal 未存在, 无处可挂)

## D. 文档 stale (低成本清理, 防误导)

- `CONTROL_THEORY_ARCHITECTURE.md` **低估**: 写"拟新增 control 层", 实际 19 文件已建好在跑
- 旧 Vite+React18 阶段计划已删除，当前实现为 Next.js 16+React19
- ADR-0001 (FlowGram) / 0002 (Radix) 已被 xyflow / base-ui **superseded**, ADR 文件无标注

---

## 优先级 (ROI 排序)

1. **W1 SCHEMA_DRIFT error_type** (后端, 三行, 点亮已建好每 60s 空转的安全链) — 立即, 我们做
2. **W3 control-plane 面板** (前端, 最高安全回报) — codex 线
3. **W4 skill 详情页** (前端, 最后一公里) — codex 线
4. W2 notification trigger_event (后端小修) — 我们做
5. W5 46 wrapper triage (前端) — codex 线, 逐资源
6. D 文档清理 (任何时候, 便宜)
7. C 愿景欠债 (大工程, 单独立项, 建议从 ADR-0024 recovery inbox 起 —— 最接近半接线)

分工: A/W2 后端我们修 (一组一 PR, 照采集节奏); B/W3-5 开 issue 给 codex; C 愿景要你拍板哪些真做 (很多是 accepted 但未必现在要); D 文档我随手清。
