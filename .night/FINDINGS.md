# Findings — Phase 3 adversarial walk

## F3-1: validate_config 通用校验在三个 adapter 中重复（proposed）

Smell:        pi_adapter / hermes_adapter / openclaw_adapter 的
              validate_config 里 binary/cwd/env/timeout_seconds 的类型检查
              逐字重复（copy-paste 4-5 处 × 3 个文件）。
Root type:    RuntimeAdapter ABC (backend/agent_runtimes/base.py) 没有共享的
              通用 config 校验 helper —— 每个 adapter 自行重复同一套 isinstance
              守卫。
Change:       base.py 增加 `validate_common_config(config) -> list[str]`
              （binary/cwd/env/args/timeout_seconds 公共检查），三个 adapter
              的 validate_config 先调它再补各自特有检查。
Why it dies: 通用守卫集中在一处后，新 adapter 无需再复制；加新通用 key（如
              model/provider）只改一处。重复模式从"可写出"变"只能抄"。
Fanout est.:  4 个文件（base.py + 3 个 adapter + 各自测试无改动）
Confidence:   高
Status:       implemented (f3124bb, ΔLOC -22, 106 tests pass)

## F3-2: trigger_scope.py 缺文件尾换行（W292）（proposed，低价值）

Smell:        backend/workflow/trigger_scope.py:379 W292 no newline at EOF。
Root type:    无（文件级格式问题，非表示问题）。
Change:       加换行。
Why it dies: 不适用（无 root type）。
Fanout est.:  1
Confidence:   低（不符合"上溯到 root type"门槛，不落地）
Status:       implemented (3ee50ad, +1 newline, ruff clean)
