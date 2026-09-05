# 前端页面切换验收

规范来源为 `docs/DESIGN_SYSTEM.md` §6.1 的既有共享 token。`MOTION.md` 的工作流编辑器局部交互规则继续适用于画布和面板。

## 一致性要求

- 页面内容由 SSGOI 统一调度，侧栏和顶栏持续挂载。所有 pathname 使用同一条规则，不按菜单索引另设方向。
- 空间切换读取 `--motion-duration-spatial`（320ms）和 `--motion-ease-spatial`（`cubic-bezier(0.32, 0.72, 0, 1)`）。进入页面位移 8px 并淡入，退出页面原位淡出。
- 普通点击沿前进方向进入，历史后退反向进入。同页 query 页签、筛选和加载态解析不重放整页入场。
- 导航选中态留在目标项内；颜色反馈读取 response token（180ms），不叠加跨分组弹簧和旧版 ripple。
- 减弱动态模式静态切换；在页面打开后改变系统偏好也必须生效。快速连续导航结束后只保留一个可见页面和一个可交互的导航壳。

## 检测方法

本轮浏览器实测发现，原顶栏主题按钮的局部 React ViewTransition 会在路由切换中额外产生 `::view-transition-old(root)`（250ms）及 theme-icon（180ms）动画。移除该边界后，验收要求只有 SSGOI 调度的页面 WAAPI 动画，不能通过忽略原生快照来让测试通过。

SSGOI 7.0.4 的内置方向推断把所有 `popstate` 和回到前一 pathname 的点击都视为后退。应用通过 Navigation API 观察实际 push/replace/traverse 及历史索引来区分方向，保持 Next 对导航的控制。没有 Navigation API 的浏览器，普通同源链接仍按前进处理，历史遍历方向回退到库的推断；不修改浏览器 History 方法或历史数据。

`frontend/e2e/route-motion.spec.mjs` 使用隔离浏览器与 API fixtures。正常动效测试保留 `no-preference`，不注入禁用动画的 CSS。记录实际 `Element.animate` 调用，并读取运行中 Animation 的时长、曲线和关键帧；不能以“最终页面只有一个容器”代替过程验收。

运行中切换减弱动态偏好时，先确认页面动画正在播放，再检查浏览器发出 MediaQueryList `change` 后的第一帧已经没有页面动画，并验证恢复偏好后的首次导航重新使用 spatial token。此处不以固定 50ms 等待代替浏览器事件交付。长序列用例允许开发服务器按需编译页面；断言仍要求实际动画为 320ms。

在已有本地前端运行时：

```powershell
cd frontend
$env:PLAYWRIGHT_SMOKE_PORT = '8030'
$env:PLAYWRIGHT_REUSE_EXISTING_SERVER = '1'
pnpm test:route-motion --workers=1
pnpm check:navigation-transitions
```

自动化业务数据是测试样本；这些用例验证导航、动画与页面壳，不代替真实采集、发布和执行链路验收。动画适配器的时长读取、方向、暂停、恢复、打断和样式清理由 `scripts/check-route-motion-behavior.mjs` 单独验证。

## 源码覆盖

2026-09-05 检查了 `(app)` 下 47 个页面及 5 个 layout/loading/error 文件。页面文件未发现额外的整体 Motion 入场声明，所有入口都经过共享 `AppRouteTransition`。

| 路由族 | 覆盖范围 |
| --- | --- |
| 概览与任务 | dashboard、inbox 四个 query 页签、tasks 列表与详情、notifications |
| 项目 | studio、new、workflow、workflow/image、templates；项目概览、data、api、operations、evidence、relationships、galaxy |
| 插件与自动化 | plugins、plugins/opencli、operations-agents、agents、skills 列表与详情、plans、schedules |
| 数据与资源 | records、records/graph、nodes 列表与详情、workers、browsers、sources 列表/新建/详情 |
| 管理 | providers、catalog、routing；control、kill-switch、advisory-report、odp-state、actions；system、settings |
| 兼容与辅助入口 | agent-workbench、canvas、factors |

页面中的加载指示、审批反馈、任务状态和工作流交互属于局部状态动画。源码检查将这些与整体切页动画分开；数据相关详情、错误恢复和持续运行状态不能仅靠主导航测试推断通过。

## 本轮结果（2026-09-05）

| 检查 | 结果 |
| --- | --- |
| 10 个侧栏入口，按菜单顺序及逆序点击，共 18 次切换 | 通过；进入均为 +8px、320ms、共享曲线，持久 Shell 未重挂 |
| 数据、资源、模型、控制、智能体 5 组相关视图，共 12 次切换 | 通过；与侧栏使用同一页面动画，控件反馈为 180ms |
| Inbox query 页签 | 通过；页面边界保持，未触发整页动画 |
| 浏览器后退和前进 | 通过；后退 -8px，前进 +8px |
| 折叠侧栏连续 A → B → C 导航 | 通过；最终只有一个页面边界，导航壳保持可交互 |
| 启动前开启 Reduced Motion | 通过；页面没有 WAAPI，控件为 0.01ms |
| 运行中开启并恢复 Reduced Motion | 通过；偏好事件后第一帧停止页面动画，恢复后首次导航仍为 320ms |
| 导航与动效脚本 | 29/29 通过，其中 12 项直接验证动画适配器与方向观察器 |
| TypeScript、修改范围 ESLint、diff whitespace 检查 | 通过 |
| 独立代码及测试证据复核 | 未发现需要修复的高优先级问题 |

完整浏览器回归首次为 6 项通过、1 项在 `page.goto('/dashboard')` 等待 HTML 时超时；失败 trace 没有收到页面响应，开发服务器日志记录该请求耗时 33.9s。服务恢复后，仅重跑该 Reduced Motion 用例，验证通过。以上结果合计覆盖全部 7 个浏览器场景，不表示完整回归首次零重试通过。

开发模式的按需编译仍可能造成导航等待；本轮修复的是页面切换动画与反馈一致性，未把编译耗时算作动画时长，也未据此声称真实业务全流程已验收。
