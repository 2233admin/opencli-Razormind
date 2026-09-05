# OpenAlice 基础模块整合验收

日期：2026-09-06。任务事实源为 [父任务 #116](https://github.com/2233admin/opencli-Razormind/issues/116)。本记录覆盖报告阅读器 #122 与工作状态 #123 的模块验收，不代表五项扩展任务或产品接线全部完成。

## 整合范围

隔离分支 `codex/openalice-adoption-integration-20260906` 以本地源快照 `08f0c9fa` 为基线，接入报告阅读器提交 `269e0fcb`、`e5bde339` 与工作状态提交 `ea5f04dd`、`9005681d`。主目录、主分支与业务数据库未随此整合更新。

报告解析复用 Marked、DOMPurify、highlight.js 及原有 XLSX 依赖。协调者修正 CSV 表头丢失、含制表符的 CSV 字段误判、数字字符串格式丢失，并补充 Markdown 自动资源限制和可重复的真实组件浏览器测试。测试应用位于 `frontend/e2e/report-content-harness/`，不属于产品路由。

工作状态复用原有运行、自动化与会话存储。Studio 页面请求通过既有受限管理员桥接解析到 governed Workspace；读取会话继续执行原有权限检查；变更操作与配置链接使用 governed Workspace，项目导航保留 Studio Workspace。配置页只接受已授权列表中的目标。

## 新鲜验证证据

- `python -m pytest tests/unit/test_agent_work_health.py tests/integration/test_studio_agent_session_access.py --no-cov -q`：12 项通过。
- 前端 `node --test scripts/check-report-content.mjs scripts/check-agent-work-health.mjs scripts/operations-agent-deep-link.test.mjs`：9 项通过。源码合同检查仅作为辅助证据。
- 前端 `pnpm test:report-content`：3 项真实组件浏览器测试通过，覆盖 SSR 页面响应、恶意 Markdown、引用链接、剪贴板正文、HTML sandbox/CSP、CSV/TSV/对象列对齐、375px 窄屏及回退状态。
- 前端完整 `tsc --noEmit` 通过；本次变更的 ESLint 无错误，默认 Playwright 配置中有一个原有未使用变量警告；`git diff --check` 通过。
- 独立只读审查分别接受工作状态修正与报告阅读器模块范围。父目标的最终独立验收尚未执行。

Chromium 会为被 CSP 阻止的 CSS `@import` 和背景图发出 `request` 事件。测试同时断言这些尝试以 `csp` 失败、未进入网络拦截器，避免把“产生事件”误判成“成功联网”。恶意内容未获得父页面权限。

## 环境与限制

验收使用已安装依赖的只读 `node_modules` junction 和 webpack 测试应用；没有通过共享 junction 安装依赖。干净安装被基线已存在的锁文件 `hono` override 与配置不一致问题阻止，这并非本次新增变更。没有改动该既有依赖策略，也没有执行生产数据库迁移或真实外部通知。

#121 的普通运行产物与来源修正仍在执行；#124 的产品页面接线和 #125 的外部回复仍需完成。当前结果可进入后续联调，尚不能据此认定整批可以合入并交付主项目。

路由记录：Luna 负责产物/阅读器实现，Sol 负责运行状态及权限修正；协调者负责整合、阅读器收尾和复测，独立审查者负责只读验收。首轮发现真实接入遗漏后退回修正；本轮还通过浏览器证据区分了 CSP 阻断与实际网络访问。
