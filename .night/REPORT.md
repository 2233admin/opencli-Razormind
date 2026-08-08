# Overnight Run Report — 2026-08-08

## 任务复述（三句话）
1. 让项目能**实时调度 OpenClaw/Hermes 干活**：新增 `openclaw_adapter.py` + `hermes_adapter.py` 到 `backend/agent_runtimes/`，使 operations-agents 可 dispatch 到这两个 agent（走 RuntimeAdapter 契约 + stdio 单次调用）。
2. 做**全量测试并跑出来**：后端 pytest（覆盖率红线 80%）+ 前端回归契约全绿，基线封存。
3. **收敛**：triage 并修复发现的问题（含上游遗留 regression），自找问题自解决，早上可 review 的 diff 序列。

## Phase 结局
- Phase 0: (待定)
- Phase 1: (待定)
- Phase 2: (待定)
- Phase 3: (待定)

## 基线封存证明
(待补: git log --oneline -- .night/BASELINE.md)

## 基线对照表
| 指标 | 基线 | 结束 |
|---|---|---|
| 后端测试数 | (待定) | |
| 后端通过/失败 | | |
| 前端 check pass/fail | | |
| LOC | | |

## FLAKY 集
(待补)

## Phase 2 映射表 / QUARANTINE 摘要
(待补)

## Phase 3 发现
- implemented: (待补)
- proposed: (待补)
- failed: (待补)

## BLOCKERS
(待补)

## 最可能是错的决定
(待补)
