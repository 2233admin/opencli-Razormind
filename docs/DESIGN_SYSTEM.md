# OpenCLI Admin 前端设计系统（锁定版 v1）

> 状态: **LOCKED**。改任何 token 值或新增视觉档位, 必须先改本文档再改代码。
> 依据: 2026-07-03 全量样式审计（110 处硬编码 hex、472 处 Tailwind 任意值、12 种近黑背景并存、6 个 accent 色系混战）。

## 风格基调

**Dark Ops Console（暗色作业控制台）**: 单主题纯暗色、高信息密度、telemetry 电传风格。
冷 zinc 灰阶 + 单一 primary 蓝 + 5 系语义色。克制的 2px 控件圆角, 三级阴影, 无装饰性渐变（画布底渐变除外）。

一句话判断标准: 像 SpaceX 任务控制台, 不像 SaaS 营销页。

## 0. 30 秒速查（先看这里, 90% 场景够用）

| 我要写… | 用这个 |
|---|---|
| 页面底 / 面板 / 悬浮背景 | `bg-ops-black` / `bg-ops-panel` / `bg-ops-raised` |
| 描边 | `border-ops-line`; inline 场合 `var(--oc-line)` |
| 灰字 | 正文 `text-zinc-100` / 次要 `text-zinc-400` / 弱化 `text-zinc-500` |
| 交互 / 选中 / 链接 | 文字 `text-primary-400`, 面 `bg-primary-600` |
| 成功 / 警告 / 危险 / 信息 / agent | 终态: `text-signal-success/warning/danger/info/agent`（T3a 落地即用）; 迁移期 `emerald`/`amber`/`red`/`sky`/`violet` 仍合法 |
| 图表 / xyflow / SVG 里的色常量 | signal hex: `#35b779` `#d99a3d` `#e15b64` `#4fb7d6` `#9b7bf3` |
| 小字 | `text-2xs`(11px) / `text-3xs`(10px), **禁 `text-[Npx]`** |
| 圆角 | 控件 `rounded-xs`(2px) / 面板 `rounded-md` / 弹层 `rounded-lg` |
| 阴影 | `shadow-panel` / `shadow-overlay` / `shadow-drag` |
| 数据 / 代码字体 | `font-mono` |
| 缺 ui 原语 (Tabs/Popover/…) | `npx shadcn@latest add <x>` 拉源码进 `components/ui/`, 再按本规范 token 化, 禁手搓（前置 T0 配置修复） |

## 1. 颜色

### 1.1 背景（三级海拔, 唯一真相源）

| 海拔 | Token | 值 | Tailwind 类 | 用途 |
|---|---|---|---|---|
| 0 页面底 | `--oc-bg` / `--color-ops-black` | `#050708` | `bg-ops-black` | body、画布底 |
| 1 面板 | `--oc-surface` / `--color-ops-panel` | `#0a0d10` | `bg-ops-panel` | 卡片、侧栏、面板 |
| 2 悬浮 | `--oc-surface-raised` / `--color-ops-raised` | `#101418` | `bg-ops-raised` | hover、弹层、raised 卡 |

**禁止**: 任何手写近黑 hex（`#050505`/`#060608`/`#0a0a0a`/`#0b0c0e` 等 12 种审计变体全部违规）。
inline style / SVG 用 `var(--oc-bg)` / `var(--oc-surface)` / `var(--oc-surface-raised)`。

### 1.2 灰阶

只用 **zinc**。禁 slate / gray / neutral / stone。
描边: `--oc-line`（默认）/ `--oc-line-strong`（hover）/ `--oc-line-hot`（active, primary 色相）。

### 1.3 Accent（交互色）

唯一 primary 蓝 `#2f7df6`（`--color-primary-500`, 类 `text-primary-400`、`bg-primary-600` 等, 有 50/100/300/400/500/600/700 档）。
选中、focus、active、链接、拖拽 placeholder 一律 primary。**禁 Tailwind 内置 `blue-*`**。
focus ring 统一 `#4f9bff`（primary-400, 已在 `:focus-visible` 全局定义）。

### 1.4 语义色（5 系, 状态语义唯一映射）

| 语义 | Tailwind 系 | signal token |
|---|---|---|
| 成功 / running healthy | `emerald` | `--color-signal-green` `#35b779` |
| 警告 / degraded | `amber` | `--color-signal-amber` `#d99a3d` |
| 危险 / error / 破坏性操作 | `red` | `--color-signal-red` `#e15b64` |
| 信息 / 中性提示 | `sky` | `--color-signal-cyan` `#4fb7d6` |
| Agent / 特殊实体 | `violet` | `--color-signal-violet` `#9b7bf3` |
| 高亮 / 稀缺强调（仅点缀） | — | `--color-signal-gold` `#d6a84f` |

**禁**: `green` `yellow` `cyan` `purple` `indigo` `fuchsia` `rose` `blue` 直用。
迁移映射: green→emerald, yellow→amber, cyan→sky, purple/indigo/fuchsia→violet, rose→red, blue→primary, slate→zinc。

**单轨化决议 (2026-07-03 DX review, D9/D14)**: 终态 = **角色名** signal 语义 token 类唯一真相源: `signal-success` / `signal-warning` / `signal-danger` / `signal-info` / `signal-agent` / `signal-highlight`（shadcn 语义变量风格, 结构参照 next-shadcn-dashboard-starter; agent 零查表——写"警告色"而非记"降级=琥珀"）。hex 值沿用现有 signal 色板不变。Tailwind 内置 5 系 (emerald/amber/red/sky/violet) 为**过渡期合法写法**, 待 T3b 迁移完成后转禁。前置: T3a 先在 @theme 落角色名变量（含 foreground 配对评估）; Tailwind v4 透明度修饰符 (`/12` 等) 对 token 原生可用。**本节状态 = 迁移目标, 非 locked**; 其余章节维持 locked。

## 2. 字体与字号

| 用途 | 类 / 变量 |
|---|---|
| UI 正文 | 默认（`--font-ui`, Geist + 思源黑体族） |
| 数据 / 代码 / 数字列 | `font-mono`（= `--font-code`, tabular-nums 已全局开） |
| 大写小标签（KPI 标题等） | `font-telemetry` + `telemetry-label` utility |

`font-code` 与 `font-mono` 等价, 新代码统一写 `font-mono`。组件层禁自定义 `font-family`。

字号阶梯（密集 UI 扩展了两档 micro）:

| 类 | 值 | 用途 |
|---|---|---|
| `text-3xs` | 10px | 画布节点元数据、最小注记 |
| `text-2xs` | 11px | 密集表格、状态标签、面板小字 |
| `text-xs` | 12px | 次要正文、表格默认 |
| `text-sm` | 14px | 正文默认 |
| `text-base`+ | — | 标题按 Tailwind 默认 |

**禁 `text-[Npx]` 任意值**。遗留 `text-[9px]` 暂豁免（待逐步升 10px）, 新代码禁用。

## 3. 圆角

| 类 | 值 | 用途 |
|---|---|---|
| `rounded-xs` | 2px | 控件: button / input / badge / select（本系统签名圆角） |
| `rounded-sm` | 4px | chip、小卡、行内块 |
| `rounded-md` | 6px | 面板、卡片、画布节点 |
| `rounded-lg` | 8px | 弹窗、大型弹层 |
| `rounded-full` | — | pill、状态点、头像 |

**禁 `rounded-[...]` 任意值**（`rounded-[2px]` → `rounded-xs`）。`rounded-xl` 及以上不属于本风格。

## 4. 阴影（三级, @theme token）

| 类 | 用途 |
|---|---|
| `shadow-panel` | 画布内面板 chrome（controls / minimap / palette） |
| `shadow-overlay` | 弹窗、popover、menu |
| `shadow-drag` | 拖拽 / resize 进行中的元素 |

**禁手写 `shadow-[...rgba...]`**。特效光晕（如碰撞红圈）属动画反馈, 豁免。

## 5. 间距

4px 网格（`p-1`=4 起步）。密集区允许 0.5 半档（`gap-1.5` / `px-2.5`）——这是有意的高密度设计, 不是违规。禁 `p-[Npx]` 任意值。

## 6. Motion

| 变量 | 用途 |
|---|---|
| `--oc-ease` `cubic-bezier(0.2,0.8,0.2,1)` | 标准过渡（hover/border/bg, 120–160ms） |
| `--oc-ease-emphasized` | 强调位移（面板进出, 200–300ms） |

时长档: 120 / 160 / 200 / 300ms。`--m3-*` 变量与 `m3-sheet-in` / `m3-level-in` 为遗留动画专用, 新代码不引入新的 M3 依赖。`prefers-reduced-motion` 已全局处理。

### 6.1 共享体验基础层（2026-07-13）

- 物理反馈 token: `--motion-duration-press` 70ms、`--motion-duration-response` 180ms、`--motion-duration-spatial` 320ms，以及 `--motion-ease-force-in/spatial/settle`。
- Button / Card / Switch / Toggle / Tabs / Slider 必须消费上述共享 token；页面内不得另建平行动效曲线。
- SSGOI 只控制 `AppShell` 内的 routed content；Sidebar、Header、Command Palette 必须保持边界外并持续挂载。
- 同一 DOM 不得被两套系统共同控制。持久 AppShell（包括主题按钮）不得挂载 React ViewTransition：局部边界也可能在路由更新时触发原生 root 快照，与 SSGOI 整页动画叠加。
- App Router 的 `loading.tsx` / `error.tsx` 是体验基础层的一部分，导航等待期间 Shell 保持可交互，错误态必须提供恢复动作。
- Reduced Motion 下所有 transition/animation 收敛为 0.01ms、单次执行，并关闭平滑滚动。

### 6.2 页面与操作连续性（2026-09-05）

- 页面过渡按 pathname 识别，所有应用路由只使用一条 SSGOI 规则；页面进入使用 8px 位移与淡入，退出原位淡出。时长与曲线读取 §6.1 的 spatial token（320ms），不使用库预设的隐式弹簧时长。
- 点击导航按前进方向进入，浏览器后退按反方向进入；方向不取决于侧栏顺序。筛选和 query 页签原位更新，不重挂整页；加载态变为内容时不再次入场。
- 侧栏与页签的选中反馈使用共享 response token（180ms）做颜色变化，标记保持在目标项内，不跨导航分组滑动。Reduced Motion 下页面静态切换，控件遵循全局减弱动态规则。
- 一个共享加载区只有一个持续动画主体；骨架静态展示，SVG 渐变和滤镜使用实例唯一 ID。
- 面包屑显示真实页面并链接上级；项目标签只在工作区、项目与工作流一致时保留运行上下文。
- 保存、验证、发布状态在正常布局中可见，不覆盖画布；打开运行输入面板不自动提交任务。

### 6.3 SmoothUI 操作反馈（2026-09-05）

- SmoothUI 按源码组件接入 `components/smoothui/`，保留来源与 MIT 许可；继续使用应用的 Base UI 按钮、颜色与动效 token，不导入另一套品牌 token。
- 异步按钮只在真实 Promise 完成后显示成功。失败保留可重试状态；查询 refetch 还必须检查结果中的错误标志，不能把 resolve 直接视为成功。
- 状态切换只影响按钮内容，反馈读取 response token。按钮宽度、焦点和名称保持明确，复制状态保留被复制对象名称；运行中改变 Reduced Motion 立即生效。
- API/MCP 页面保留 `ButtonCopy` 复制组件；复制地址等图标入口使用完整的可访问名称，复制失败保留重试反馈，不把凭证或秘密写入界面。
- **任务与通知采用 Linear + SmoothUI**：保持 Linear 双栏信息架构、紧凑工具栏与普通计数；SmoothUI 用于现有局部审批与反馈。组件库接入应服从该页面结构与信息密度。

## 7. 皮肤（skins）

`data-skin` 的 `spacex` / `nvidia` / `binance` 为实验遗留。**默认皮肤是唯一验收基准**, 新代码不得只在某 skin 下调样式。

## 8. 组件纪律

1. 新 UI 先用 `components/ui/*`（原语）与 `components/opencli/*`（面板体系: OperatorCard / MetricTile / PanelHeader / WorkbenchPanel）; 不够用先扩组件, 不允许页面内手搓平行实现。
2. 画布类页面（PlanCanvas / Network / Topology / node-kit 节点渲染）同样受本规范约束, 无画布豁免。
3. `telemetry-*` / `operator-*` utility 是画布面板的官方皮肤, 保留并优先使用。
4. Radix 状态选择器 `data-[state=...]` 不算任意值, 正常使用。
5. 文字角色优先使用 `type-headline-*` / `type-title-*` / `type-body-*` / `type-label-*`; 遥测与数据使用 `font-data`, 代码使用 `font-code`。这些角色集中定义于 `app/globals.css`。

## 9. 改 token 值流程

1. grep 该 token 全仓使用面（类名 + `var(--…)` 双形态）, 记下受影响页面。
2. `@theme`/`:root` 与本文档在同一 commit 内改。
3. 预览过一遍受影响页面（重点: 画布类 + 最大表格页）。git 历史即变更记录, 不另设 changelog。

## 10. 落地清单（2026-07-03 DX review, 含 codex outside-voice 修正）

- [ ] **T0 (P1)** 修 shadcn 配置: `components.json` baseColor slate→neutral, 去除对不存在的 tailwind.config.js 的引用, 适配 TW4 CSS-first; 附 add 后 token 化改造步骤（codex #6, D16）
- [ ] **T1 (P1)** token 扫描闸门: Node 扫描脚本固化本次审计 grep 模式（`text-[Npx]`/`rounded-[…]`/近黑 hex/禁用色系, 覆盖 JSX+CSS+inline+图表常量）, 报错含改法, allowlist 豁免 index.css 既有 utility, **frontend 现无 lint 基建, 含 bootstrap**: 建 `npm run lint` 接 CI; ESLint 后补做 IDE 波浪线（D7/D13, codex #1/#2/#8）
- [ ] **T2 (P1)** 入口链接: 仓根 CLAUDE.md 指向本文档（已落）; README 前端节补链接（D6）
- [ ] **T3a (P1)** @theme 落角色名 signal 变量: `--color-signal-{success,warning,danger,info,agent,highlight}`, 让速查表终态 API 立即可用（D14）
- [ ] **T3b (P2)** 语义色单轨迁移: Tailwind 5 系 → `signal-*` 角色类 ~500 处; 同时评估 `--oc-*` / `ops-*` / shadcn 变量三系合并（codex #7）
- [ ] **T4 (P2)** snippet 库: 标准面板/卡片/弹窗/表格/状态徽章 JSX 模板附录, 砍 TTHW 主力（codex #9, D15 折中）
- [ ] **T5 (P3)** `text-[9px]` 遗留 26 处清理升 `text-3xs`（D16）
- [ ] **T6 (P3)** agent 验收演习: 给 agent 3 个典型 UI 任务, 测零额外提示能否走对 token/组件路径, 记录 miss = TTHW 实测闭环（codex #10, D16）

## 附: 审计基线（2026-07-03）

硬编码 hex 110 / rgb() 25 / 任意值 472（text 216、tracking 51、尺寸 65、bg 29、rounded 12、shadow 5）; zinc 846 vs slate 9; accent: amber 165 / red 136 / emerald 123 / sky 97 / violet 46 + 散装 blue/green/purple/indigo/yellow/cyan ~70。清洗后此表应趋近全绿。
