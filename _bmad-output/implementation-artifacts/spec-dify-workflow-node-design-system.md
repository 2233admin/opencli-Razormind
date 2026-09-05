---
title: '用 Dify 技术型设计系统重做工作流节点'
type: 'feature'
created: '2026-08-28'
status: 'done'
review_loop_iteration: 0
baseline_commit: '7c2d19790890da5835292b82f1e1607766d6748b'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 当前节点把身份、状态、参数、领域操作和端口塞入单体组件；宽度、缩放、布局和端口几何不一致，视觉层级弱且难维护。

**Approach:** 建立 Dify 技术型节点原语并迁移 workflow node：固定几何、真实图标、清晰标题/状态、短摘要和类型化 IN/OUT。详细参数和领域操作进入 Inspector；不替换全站组件库，不改变工作流语义。

## Boundaries & Constraints

**Always:** 节点固定宽 240px、最小高 96px；状态/锁定/选中不改变几何；外层允许端口溢出，内层 Surface 才裁剪；端口 ID/type/direction、Top/Bottom、菜单、键盘和 `data-port-*` 保持；统一 low `<0.5`、mid `<1`、high `>=1`；使用现有 token 和 Lucide；明暗主题、focus-visible、reduced-motion 可用。

**Ask First:** 改端口方向/图布局、canonical/store/runtime 数据、引入组件库、重做其他 node 类型、改变 Inspector 之外的业务操作。

**Never:** polygon/纹理/发光/整卡脉冲/字母 sigil/8–9px 正文；节点内表单、完整 prompt、调试值、图片按钮、徽章云或迷你网络；状态互相覆盖或只靠颜色表达。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 缩放 | 0.4 / 0.75 / 1.0 | 几何和 Handle 不变，仅密度变化 | 关闭 contextual zoom 时强制 high |
| 状态组合 | selected + error + locked | 外环、错误内边/文字、锁标同时存在 | 不互相覆盖 |
| 端口 | 无/单/多/动态 | 顺序、ID、类型和菜单保持 | 延续现有 merge 规则 |
| 图片节点 | generation/asset | 节点仅摘要；操作/指标进 Inspector | URL 参数和 gallery 保持 |
| 主题/键盘 | light/dark、Tab | 对比正常；焦点显示端口名/方向 | 无 hover 也可发现 |

</frozen-after-approval>

## Code Map

- `frontend/components/flow/nodes/workflow-node.tsx:312-730` -- 保留身份、端口、状态和路由适配；移除视觉单体与领域面板。
- `frontend/components/flow/nodes/workflow-node-primitives.tsx` -- 新建 Root、Surface、Header、Summary、Interface、PortHandle。
- `frontend/app/flow-canvas.css:113-310` -- 节点/端口/状态/密度/focus；移除硬编码暗色和失效 token。
- `frontend/components/flow/workflow-canvas-surface.tsx:154-157,277-285` -- 唯一 zoom bucket 来源。
- `frontend/components/flow/inspector.tsx:1592-2092` -- 接收图片操作及 map/network/runtime 详情。
- `frontend/lib/flow/collision.ts`, `frontend/lib/flow/layout.ts`, `frontend/lib/flow/store.ts` -- 复用统一 geometry。
- `frontend/lib/workflow/node-visuals.ts`, `frontend/lib/flow/icons.tsx` -- 类别 accent 与真实 catalog 图标。
- workflow 回归脚本及新 `frontend/e2e/workflow-node-design.spec.mjs` -- 行为与视觉矩阵。

## Tasks & Acceptance

**Execution:**
- [x] 建立节点原语和共享 geometry/status contract。
- [x] 迁移 `workflow-node.tsx`，保留数据、端口、事件和 zoom 语义。
- [x] 将图片操作、map/network/runtime 详情迁入 Inspector。
- [x] 统一 CSS、layout/store 和 node visuals。
- [x] 更新回归脚本并新增 Playwright 状态矩阵。
- [x] 完成审查修正：恢复端口 merge/wiring 语义、共享动态 geometry、独立状态组合、Inspector 实值与图标映射。
- [x] 完成 light/dark 与焦点对比修正，并以真实 Canvas 端口、Inspector、缩放与截图附件覆盖验证。

**Acceptance Criteria:**
- Given 任意运行/能力/包状态，when 状态变化，then 几何/端口稳定且状态可组合、可读。
- Given low/mid/high，when 缩放，then 不重新布局，只改变信息量。
- Given 键盘或鼠标操作端口，when 聚焦/悬停/布线，then 名称、方向和类型可发现，原连接/菜单不变。
- Given 图片节点，when 创作或选资产，then 从 Inspector 完成原操作且 URL 参数保持。
- Given light/dark，when 查看全部状态，then 无硬编码暗色、低对比度或颜色唯一编码。

## Spec Change Log
- 2026-08-28：完成固定几何节点原语、Inspector 详情迁移、回归合同与 Canvas Playwright 覆盖。
- 2026-08-28：审查修正端口回退优先级、wiring 状态、动态 geometry、Inspector 实值、低密度状态、主题对比、图标和真实 Canvas 测试。

## Design Notes

节点是技术仪表盘，不是缩小 Inspector：Header 放身份和状态，Summary 最多两行，24px Interface 行显示 `IN/OUT · id · [type]`。外环、内边、中性标记分别表达选择、执行和包状态；其他 node 类型不迁移。

## Verification

**Commands:**
- `npm --prefix frontend run lint && npm --prefix frontend run build` -- 类型、SSR、样式通过。
- `npm --prefix frontend run check:workflow && npm --prefix frontend run check:workflow-image` -- 端口与图片合同通过。
- `npm --prefix frontend run test:smoke -- workflow-node-design.spec.mjs` -- 状态/缩放/主题/键盘矩阵通过。

**Manual checks:**
- 真实 Canvas 检查 light/dark、0.4/0.75/1.0、selected+error+locked，确认 240px 几何和 Handle 坐标稳定。

**Evidence (2026-08-28):**
- `npm --prefix frontend run lint`：通过（0 errors；仓库既有 2 warnings）。
- `npm --prefix frontend run build`：通过（编译、TypeScript、SSR/static generation）。
- `npm --prefix frontend run check:workflow`：通过；`npm --prefix frontend run check:workflow-image`：9/9 通过。
- 新鲜 `frontend` dev server（127.0.0.1:3001）上的 `npm --prefix frontend run test:smoke -- workflow-node-design.spec.mjs`：通过；以真实 XYFlow Zoom In/Out 观察 high→mid→low→high、比较节点 geometry 与全部 Handle 相对 rect，并验证关闭 contextual zoom 时强制 high。测试将 selected+error+locked 明确标记为 CSS 组合夹具，并向 Playwright report 附加 light/dark 截图。
- Browser 实机验证：Canvas 已检查 dark/light 表面，固定 240px 节点、可见 IN/OUT id/type、Lucide 图标和端口锚点均正常。

## Suggested Review Order

1. Shared fixed/dynamic geometry and pre-measure fallbacks — [`frontend/lib/flow/node-geometry.ts:1`](../../frontend/lib/flow/node-geometry.ts#L1), [`frontend/lib/flow/collision.ts:20`](../../frontend/lib/flow/collision.ts#L20), [`frontend/lib/flow/layout.ts:18`](../../frontend/lib/flow/layout.ts#L18), [`frontend/lib/flow/store.ts:897`](../../frontend/lib/flow/store.ts#L897), [`frontend/lib/workflow/to-react-flow.ts:75`](../../frontend/lib/workflow/to-react-flow.ts#L75).
2. Node contract, semantic fallback precedence, and DOM geometry ownership — [`frontend/components/flow/nodes/workflow-node.tsx:124`](../../frontend/components/flow/nodes/workflow-node.tsx#L124).
3. Extracted primitives, port semantics, keyboard menu behavior, and Handle remeasurement — [`frontend/components/flow/nodes/workflow-node-primitives.tsx:112`](../../frontend/components/flow/nodes/workflow-node-primitives.tsx#L112), [`frontend/components/flow/nodes/workflow-node-primitives.tsx:146`](../../frontend/components/flow/nodes/workflow-node-primitives.tsx#L146).
4. Canvas-level density and composable visual states — [`frontend/components/flow/workflow-canvas-surface.tsx:273`](../../frontend/components/flow/workflow-canvas-surface.tsx#L273), [`frontend/app/flow-canvas.css:113`](../../frontend/app/flow-canvas.css#L113).
5. Inspector’s image URL preservation and migrated runtime/relationship values — [`frontend/components/flow/inspector.tsx:1098`](../../frontend/components/flow/inspector.tsx#L1098), [`frontend/components/flow/inspector.tsx:1780`](../../frontend/components/flow/inspector.tsx#L1780).
6. Icon completeness and observable contracts — [`frontend/lib/flow/icons.tsx:1`](../../frontend/lib/flow/icons.tsx#L1), [`frontend/scripts/check-workflow-regressions.mjs:1194`](../../frontend/scripts/check-workflow-regressions.mjs#L1194), [`frontend/e2e/workflow-node-design.spec.mjs:55`](../../frontend/e2e/workflow-node-design.spec.mjs#L55).

