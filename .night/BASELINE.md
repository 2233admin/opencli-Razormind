# Overnight Run Baseline — 2026-08-08

## 环境
- 仓库: opencli-Razormind fork worktree (wt-control), branch: night
- BASE: 8af917e (feat/control-center-panels tip, 含 W3 控制中心 3 commits)
- Python: uv 0.12.2 / cpython-3.13 (uv sync --extra dev)
- 测试命令: `PYTHONPATH= uv run pytest`（必须清空 PYTHONPATH——Hermes agent 运行时注入自身 venv 到 PYTHONPATH，污染 pydantic/pydantic_core 解析）

## 基线（Phase 0）
- 后端 pytest: **2701 passed, 1 failed, 50 skipped**（846.82s）
  - 唯一失败: test_capability_exposure_matrix::test_every_unreferenced_api_wrapper_has_an_explicit_decision
  - 根因: W3 控制中心引用了 4 个 control wrapper，矩阵未同步（F2，本次已修）
- 覆盖率: 87.57% (红线 80%) ✓
- 前端回归契约: 21 pass / 1 fail（studio node selector 断言过时 = F1，本次已修）
- LOC: 后端 541 py 文件 ~97,992 行（含测试）

## 基线封存
git log --oneline -- .night/BASELINE.md（唯一一次提交见 Phase 0 commit）
