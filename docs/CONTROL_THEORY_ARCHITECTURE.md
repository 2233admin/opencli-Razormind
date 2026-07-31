# opencli-admin as a Data Acquisition Control System

> 工程控制论(钱学森)视角的架构原则 + 落地路线。
> 本文同时覆盖**诊断**(哪些反馈回路断了)与**运行**(如何把系统建成自稳定控制系统)。
>
> 核心命题:opencli-admin 不是"多数据源采集平台",而是
> **可观测、可反馈、可自稳定的数据采集控制系统**。RSS/API/CLI/Web/skill 看似不同,
> 本质都是可观测性·可控制性各异的**被控对象**。

---

## 0. 一条硬前提:控制器不能建在会撒谎的传感器上

这是本文档相对"直接写一个 control 层"提案的最重要修正。

一个反馈控制器基于**观测量**做决策(降频、熔断、切 sink)。如果观测量本身失真,
控制器会基于假信号损害正确的源——**比没有控制器更糟**。因此控制层有严格的**前置依赖**:
先让传感器诚实,再谈自动控制。

审计发现的"撒谎传感器",及其修复状态:

| 传感器缺陷(审计编号) | 症状 | 状态 |
|---|---|---|
| P0-2 odp-ingest `bus=None` 假报 accepted | 摄入黑洞被计为成功 | ✅ B1 已修(`d1f33d9`) |
| P0-1 5xx 不可重试→吞成 success=False | 瞬时故障被记成永久失败 | ✅ B1 已修 |
| P0-3 reap 毒消息与 trim 混同→静默丢 | DLQ 计数不反映真实丢失 | ✅ B1 已修 |
| P1-7 DualSink shadow 错误无人读 | 影子写全挂但任务显示 completed | ⏳ B3(错误上浮契约) |
| P1-6 cursor 非原子推进 | 并发下 cursor 丢更新=状态失真 | ⏳ B4(cursor 原子) |
| P1-8 CI 不测关键路径 | 回归无反馈就合并 | ⏳ B2(CI 门禁) |

**结论:PR-Control 系列必须排在 B3/B4 之后(或与之交织),不可先行。**

---

## 1. 系统的控制论定义

```
被控对象 Plant:      RSS / API / CLI / Web / opencli / skill / ODP / Redis / Postgres
传感器 Sensors:      run_events / accepted / rejected / duplicate / latency /
                     freshness_lag / odp_pending / odp_stream_lag / dlq_count
控制器 Controller:   runner(局部) + scheduler(时间) + policy engine(上层)
执行器 Actuators:    pause/resume source / retry / change interval / reduce page_size /
                     switch write_strategy / claim pending / move to DLQ / require review
目标 Setpoints:      新鲜度 / 吞吐 / 低重复率 / 低拒绝率 / 低延迟 / 稳定 / 低成本
扰动 Disturbances:   RSS 格式乱 / API 限流 / DOM 变 / CLI 失败 / Redis 积压 /
                     Postgres 慢 / 网络抖动 / 账号失效
```

模块的控制论重命名(现名 → 控制论角色):

| 现名 | 控制论角色 | 工程含义 |
|---|---|---|
| Connector/Channel | 被控对象接口 / 采样器 | 只 fetch/parse,不控制全局 |
| Runner | 局部控制器 | 控一次 run 的分页/重试/cursor |
| Scheduler | 时间控制器 | 决定何时下一次采样 |
| ODP ingest | 输入缓冲器 | 接收采样、削峰 |
| Redis Streams | 传输通道 | 承载事件流(非事实源) |
| odp-store | 状态写入执行器 | 把事件落成事实 |
| Postgres | 系统状态记忆 | 事实/历史/证据 |
| run_events | 传感器读数 | 观测信号 |
| topology UI | 控制室仪表盘 | 展示状态/误差/扰动/动作 |
| policy engine | 上层控制器 | 调参/熔断/降级/恢复 |
| DLQ | 故障隔离区 | 坏消息不污染主回路 |

---

## 2. 八条设计原则

1. **数据源是被控对象** — 每个 source 必须有状态、观测量、控制策略,不只是一行配置。
2. **connector 只采样,不控制全局** — fetch/parse 归 channel;重试/降频/熔断归 runner/controller。
3. **cursor 是控制状态,不是普通字段** — 只能在 durable write 成功后推进(审计 P1-6/B4)。
4. **run_events 是传感器** — 没有 run evidence 就不能自动控制;传感器必须诚实(§0)。
5. **ODP 是缓冲+事实写入通道** — Redis 是传输不是事实源;Postgres 是状态记忆。
6. **所有失败必须分类** — transient / permanent / auth / rate_limit / schema_drift /
   backpressure / poison_message,不同失败→不同控制动作(审计 P0-1 已把 HTTP 状态纳入分类)。
7. **topology 是控制室** — 前端展示控制状态/误差/动作/扰动,不是装饰连线。
8. **自动化必须可解释** — 每次自动调整记录:为什么调/调了什么/调前指标/调后指标/是否恢复。
   (= 控制论"信息即控制力":不可解释的调整是不可信的控制。)

---

## 3. 数据源按"可观测性/可控制性"分类(接地到真实 registry)

不按 RSS/API/… 分,按控制特性分。这直接决定 runner 的 `control_profile`。
下表覆盖 `backend/channels/registry.py` 实际注册的 7 种类型:

| channel_type | 可观测性 | 可控制性 | 主扰动 | runner 策略要点 |
|---|---|---|---|---|
| api | 高 | 高 | rate limit / token 过期 | cursor_token;尊重 Retry-After;429→降 page_size+降频 |
| rss | 中 | 中 | 格式乱 / 时间不准 / 重复 | 不信 source_ts;留 recent_event_ids + payload_hash;容忍重复;etag/304=healthy |
| cli | 中 | 中 | exit code / 环境依赖 | 看 returncode;binary 白名单(审计 P0-4);超时严格 |
| crawl4ai | 中 | 低 | DOM 变 / 反爬 / LLM 成本 | selector/extraction miss rate 熔断;成本上限;drift→暂停 |
| web_scraper | 低 | 低 | DOM 变 / 反爬 | miss rate > 阈值 → schema_drift,停抓入人工 review,不无限重试 |
| skill | 中 | 低 | 慢 / 不可预测 / 成本高 | 严格 timeout;记步骤证据;失败入人工 review |
| opencli | 中 | 中 | agent/bridge 不稳 / CDP 断 | 健康检查绑定端点(已修 `f6d7ef1`);dispatch 双路兜底 |

---

## 4. 拟新增 `backend/control/` 层(排在 B3/B4 之后)

不替代 runner;在 runner 之上做控制决策。

```
backend/control/
├─ models.py         # SourceControlState 枚举 + ControlAction
├─ measurements.py   # SourceMeasurement(传感器读数聚合)
├─ objectives.py     # SourceObjective(每源 setpoint)
├─ evaluator.py      # measurement + objective → SourceControlState(误差判断)
├─ policies.py       # state → list[ControlAction](反馈律,先 rule-based)
├─ actuators.py      # 真的改系统(interval/pause/page_size/write_strategy)
└─ controller.py     # 编排:observe → evaluate → decide → (advisory|act) → audit
```

关键契约草案(详见 essay,落地时对齐真实字段):
- `SourceMeasurement`:accepted/duplicates/rejected/各段 latency/error_rate/duplicate_rate/
  freshness_lag/cursor_advanced/odp_stream_lag/odp_pending/dlq_count/observed_at
- `SourceControlState`:HEALTHY/DEGRADED/BACKPRESSURED/RATE_LIMITED/AUTH_FAILED/
  SCHEMA_DRIFT/PAUSED/DEAD
- `ControlAction`:increase_interval/apply_backoff/pause/resume/reduce_page_size/
  switch_write_strategy/force_cursor_rescan/claim_pending/require_human_review
- 每个 channel 配 `control_profile`(kind + 阈值 + backoff),而非只有采集配置。

**安全闸门**:`CONTROL_MODE=advisory`(只建议不执行)必须先于 `CONTROL_MODE=automatic`;
automatic 下每个动作必写 audit log(原则 8)。

---

## 5. 反调和后的统一路线(审计 B 系列 + PR-Control 一条链)

不是两套并行计划。传感器诚实是控制层的地基,故交织成单序列:

| 阶段 | 内容 | 控制论意义 | 状态 |
|---|---|---|---|
| B1 | 3 个数据链路 P0(accepted 真实性/重试回路/毒消息) | 修好 3 个撒谎传感器 | ✅ `d1f33d9` |
| B2 | CI 门禁(alembic/cargo/coverage) | 装元反馈:回归在合并前被捕捉 | ⏳ |
| B3 | SSRF 统一校验器 + 错误上浮契约 | 传感器诚实(shadow 错误可见)+ 出站安全 | ⏳ |
| B4 | cursor 原子推进 + strangler 收口 | 控制状态可信(cursor 不丢更新) | ⏳ |
| PR-Control-1 | `control/models.py` + `measurements.py` + `objectives.py` | 定义传感器/setpoint,**零行为变更** | 待 B3/B4 |
| PR-Control-2 | run evidence + ODP metrics → `SourceMeasurement`;`GET /sources/{id}/control-state` | 传感器读数上线(只读) | 待 1 |
| PR-Control-3 | rule-based `evaluator`+`policies`,`CONTROL_MODE=advisory` | 反馈律,只建议不执行 | 待 2 |
| PR-Control-4 | `actuators` + `CONTROL_MODE=automatic` + audit log | 执行器闭环,可解释 | 待 3 |
| PR-Control-5 | topology 控制室(state/action/error/lag/pending/DLQ) | 仪表盘 | 待 2+ |

> 一句话:PR-Control-1 可与 B3/B4 并行起草(它零行为变更),但**advisory 控制器(PR-Control-3)
> 之前必须 B3/B4 落地**——否则控制器读的是失真信号。actuator(PR-Control-4)之前必须
> 有 advisory 跑过一段、验证策略不误伤。
