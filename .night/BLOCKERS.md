# Blockers

## Phase: 2 — OpenClaw 真实运行验证阻塞（非规格反例）

Attempted: 实测 `openclaw agent --agent main -m "..." --json` 的 JSON 输出结构
（adapter 的 reply 字段探测需要真实 payload 确认）。

Blocked by: main agent 配置的模型 `volcengine/kimi-k2.6` 返回 billing 错误
（"account does not have a valid CodingPlan subscription / API key has run out
of credits"）。任何 agent turn 都失败于模型计费层，拿不到正常 JSON 输出。

Needs: 用户决定——(a) 给 volcengine 充值/续订 CodingPlan；(b) 在
~/.openclaw 配置切换 main agent 到有余额的 provider（如 deepseek）；(c)
接受 adapter 以"容错解析 + fake binary 测试"交付，真实输出结构待 key 恢复后
再校准。

State: 分支 night，adapter 已交付（容错解析：JSON 探测 + 非 JSON 退化 +
非零退出 error），fake binary 测试 13 个全过。规格（base.py/pi_adapter.py
模式）无反例。
