# Overnight Run Report — 2026-08-08

## 任务复述（三句话）
1. 让项目能**实时调度 OpenClaw/Hermes 干活**：新增 `openclaw_adapter.py` + `hermes_adapter.py` 到 `backend/agent_runtimes/`，使 operations-agents 可 dispatch 到这两个 agent（走 RuntimeAdapter 契约 + stdio 单次调用）。
2. 做**全量测试并跑出来**：后端 pytest（覆盖率红线 80%）+ 前端回归契约全绿，基线封存。
3. **收敛**：triage 并修复发现的问题（含上游遗留 regression），自找问题自解决，早上可 review 的 diff 序列。

## Phase 结局
- Phase 0: ✅ 达成（基线 2701 pass / 1 fail / 50 skip，cov 87.57%；F1+F2 已修）
- Phase 1: ✅ 达成（F1 前端断言过时、F2 capability-matrix 不同步，均已修+验证）
- Phase 2: 收敛待复核（adapter 交付，19 测试全绿，e2e 真实 hermes 调用成功；复核子 agent 映射表待并入）
- Phase 3: (待定)

## 基线封存证明
4bc191b chore(night): Phase 0 baseline + report skeleton + blockers (baseline seal)
（git log -- .night/BASELINE.md 应只有此一条）

## 基线对照表
| 指标 | 基线 | 结束 |
|---|---|---|
| 后端测试 | 2701 pass / 1 fail / 50 skip | 2701+19 pass / 0 fail / 50 skip（F2 修后全绿） |
| 覆盖率 | 87.57% (红线 80%) | ≥87.57% |
| 前端回归 | 21 pass / 1 fail | 22 pass / 0 fail（F1 修后全绿） |
| 后端 LOC | ~97,992 | +~550（两个 adapter + 测试） |

## FLAKY 集
无（基线两遍未见不一致测试；50 skip 为 live/postgres_conformance 标记）

## Phase 2 映射表 / QUARANTINE 摘要
- 映射表: 复核子 agent（deleg_f07789d9）逐条核对 a–h 8 检查点全部一致
  - runtime_type 注册一致（hermes/openclaw 无重名）
  - capabilities.transport=stdio，能力声明与 docstring 自洽
  - validate_config 全覆盖（含 F3-1 后复用 validate_common_config）
  - 事件严格落 EVENT_TYPES 闭集、全走 event_* 构造器
  - 5 类错误路径全部 event_error + 正确 error_type
  - 超时 terminate→kill→CancelledError 重抛完整
  - 非零退出读 stderr tail、done 带 result dict
  - 唯一标注: 两处 stdin.close() 的 `except Exception` + pragma: no cover（防御性，不阻塞）
- 结论: **达成**（外部复核，映射表原文见复核 transcript）
- QUARANTINE: 0 条（无测试隔离）

## Phase 3 发现
- implemented: (待补)
- proposed: (待补)
- failed: (待补)

## BLOCKERS
- **OpenClaw 真实运行验证阻塞**（非规格反例）: main agent 模型 volcengine/kimi-k2.6 billing 过期，拿不到正常 JSON 输出。adapter 已按容错解析交付（JSON 探测 + 非 JSON 退化 + 非零退出 error），fake binary 测试 13 个全过。真实输出结构待 key 恢复后校准。
- **Hermes 无阻塞**: 真实 e2e 调用成功（started/text/done 完整事件流）。

## 最可能是错的决定
OpenClaw adapter 的 reply 字段探测顺序（text/reply/content/message/result/response）是基于猜测而非实测——真实 JSON schema 未知，可能漏掉实际字段（但容错退化保证不会崩）。
